"""Epoch training that stops when validation stops improving.

A stalled run changes one hyperparameter and continues. The search ends when
every available change has been tried and none improves the same validation
slice. There is no fixed number of branches.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .dataset import stack_matches
from .metrics import compute_classification_metrics
from .models import TemporalCalibratedEstimator, _holdout_cut

logger = logging.getLogger("neural.epoch")

MIN_DELTA = 0.0001
PATIENCE = 8
MAX_EPOCHS = 100
HOLDOUT_MATCHES = 100


def split_holdout(matches: Sequence[Any], count: int = HOLDOUT_MATCHES):
    """Keep the latest matches out of every training slice."""
    ordered = sorted(matches, key=lambda match: (match.started_at, match.event_id))
    if len(ordered) <= count:
        raise ValueError(f"Need more than {count} matches to hold out the replay set")
    return ordered[:-count], ordered[-count:]


def _score(labels: np.ndarray, probs: np.ndarray) -> tuple[float, float]:
    if len(labels) < 2 or len(set(int(item) for item in labels.tolist())) < 2:
        return 1.0, 1.0
    metrics = compute_classification_metrics(labels, probs)
    return float(metrics["log_loss"]), float(metrics["brier_score"])


def _improved(loss: float, brier: float, best_loss: Optional[float], best_brier: Optional[float], min_delta: float) -> bool:
    if best_loss is None or best_brier is None:
        return True
    if loss < best_loss - min_delta:
        return True
    return abs(loss - best_loss) <= 1e-12 and brier < best_brier


def _log_epoch(family: str, params: Dict[str, Any], epoch: int, loss: float, brier: float, best_epoch: int, waited: int) -> None:
    logger.info(
        "Epoch family=%s params=%s epoch=%d log_loss=%.4f brier=%.4f best_epoch=%d waited=%d",
        family,
        json.dumps(params, sort_keys=True),
        epoch,
        loss,
        brier,
        best_epoch,
        waited,
    )


def _positive(model: Any, rows: np.ndarray) -> np.ndarray:
    probs = model.predict_proba(rows)
    classes = [int(item) for item in model.classes_]
    column = classes.index(1) if 1 in classes else 1
    return np.asarray(probs[:, column], dtype=float)


class FittedLinear:
    """Scaler and logistic model fitted outside a sklearn Pipeline."""

    def __init__(self, scaler: StandardScaler, classifier: LogisticRegression):
        self.scaler = scaler
        self.classifier = classifier
        self.classes_ = classifier.classes_

    def predict_proba(self, rows: np.ndarray) -> np.ndarray:
        return self.classifier.predict_proba(self.scaler.transform(np.asarray(rows, dtype=float)))

    def predict(self, rows: np.ndarray) -> np.ndarray:
        return (self.predict_proba(rows)[:, 1] >= 0.5).astype(int)


def _logistic_epoch_loop(train, val, C: float, max_epochs: int, patience: int, min_delta: float, seed: int):
    import warnings

    from sklearn.exceptions import ConvergenceWarning

    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    rows, labels, weights = stack_matches(train)
    val_rows, val_labels, _val_weights = stack_matches(val)
    scaler = StandardScaler().fit(rows)
    train_x = scaler.transform(rows)
    val_x = scaler.transform(val_rows)
    model = LogisticRegression(C=float(C), solver="saga", warm_start=True, max_iter=1, random_state=seed)
    best_loss = None
    best_brier = None
    best_epoch = 0
    waited = 0
    curve = []
    best_state = None
    reason = "max_epochs"
    for epoch in range(1, max_epochs + 1):
        model.fit(train_x, labels, sample_weight=weights)
        loss, brier = _score(val_labels, _positive(model, val_x))
        if _improved(loss, brier, best_loss, best_brier, min_delta):
            best_loss, best_brier, best_epoch, waited = loss, brier, epoch, 0
            best_state = (model.coef_.copy(), model.intercept_.copy(), np.asarray(model.classes_).copy())
        else:
            waited += 1
        curve.append({"epoch": epoch, "log_loss": round(loss, 6), "brier": round(brier, 6), "waited": waited})
        _log_epoch("lr", {"C": float(C)}, epoch, loss, brier, best_epoch, waited)
        if waited >= patience:
            reason = "validation_plateau"
            break
    model.coef_, model.intercept_, model.classes_ = best_state
    logger.info(
        "Epoch search stopped family=lr params=%s reason=%s best_epoch=%d log_loss=%.4f brier=%.4f",
        json.dumps({"C": float(C)}),
        reason,
        best_epoch,
        best_loss,
        best_brier,
    )
    return {
        "log_loss": float(best_loss),
        "brier": float(best_brier),
        "best_epoch": int(best_epoch),
        "reason": reason,
        "curve": curve,
        "model": FittedLinear(scaler, model),
    }


def refit_logistic(matches, C: float, epochs: int, seed: int = 42) -> FittedLinear:
    rows, labels, weights = stack_matches(matches)
    scaler = StandardScaler().fit(rows)
    model = LogisticRegression(C=float(C), solver="saga", warm_start=True, max_iter=1, random_state=seed)
    train_x = scaler.transform(rows)
    for _ in range(max(1, int(epochs))):
        model.fit(train_x, labels, sample_weight=weights)
    return FittedLinear(scaler, model)


def _boosting_epoch_loop(train, val, params: Dict[str, Any], max_epochs: int, patience: int, min_delta: float, seed: int):
    rows, labels, weights = stack_matches(train)
    val_rows, val_labels, _val_weights = stack_matches(val)
    model = HistGradientBoostingClassifier(
        learning_rate=float(params["learning_rate"]),
        max_iter=1,
        max_depth=int(params["max_depth"]),
        min_samples_leaf=int(params["min_samples_leaf"]),
        l2_regularization=float(params["l2_regularization"]),
        warm_start=True,
        random_state=seed,
    )
    best_loss = None
    best_brier = None
    best_epoch = 0
    waited = 0
    curve = []
    reason = "max_epochs"
    for epoch in range(1, max_epochs + 1):
        model.set_params(max_iter=epoch)
        model.fit(rows, labels, sample_weight=weights)
        loss, brier = _score(val_labels, _positive(model, val_rows))
        if _improved(loss, brier, best_loss, best_brier, min_delta):
            best_loss, best_brier, best_epoch, waited = loss, brier, epoch, 0
        else:
            waited += 1
        curve.append({"epoch": epoch, "log_loss": round(loss, 6), "brier": round(brier, 6), "waited": waited})
        _log_epoch("gb", params, epoch, loss, brier, best_epoch, waited)
        if waited >= patience:
            reason = "validation_plateau"
            break
    logger.info(
        "Epoch search stopped family=gb params=%s reason=%s best_epoch=%d log_loss=%.4f brier=%.4f",
        json.dumps(params, sort_keys=True),
        reason,
        best_epoch,
        best_loss,
        best_brier,
    )
    return {
        "log_loss": float(best_loss),
        "brier": float(best_brier),
        "best_epoch": int(best_epoch),
        "reason": reason,
        "curve": curve,
    }


def refit_boosting(matches, params: Dict[str, Any], epochs: int, seed: int = 42):
    rows, labels, weights = stack_matches(matches)
    model = HistGradientBoostingClassifier(
        learning_rate=float(params["learning_rate"]),
        max_iter=max(1, int(epochs)),
        max_depth=int(params["max_depth"]),
        min_samples_leaf=int(params["min_samples_leaf"]),
        l2_regularization=float(params["l2_regularization"]),
        random_state=seed,
    )
    model.fit(rows, labels, sample_weight=weights)
    return model


def _residual_epoch_loop(train, val, names, params: Dict[str, Any], max_epochs: int, patience: int, min_delta: float, state: Optional[Dict[str, Any]]):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    from .compare.feature_models import _market_from_odds, _mlp, _mlp_predict, _residual_logits, stack

    torch.manual_seed(42)
    torch.set_num_threads(2)
    device = torch.device("cpu")
    x_train, y_train, w_train, _markets, odds_train = stack(train, names)
    x_val, y_val, _w_val, _markets_val, odds_val = stack(val, names)
    scaler = StandardScaler().fit(x_train)
    model = _mlp(len(names), (64, 32), float(params["dropout"])).to(device)
    if state is not None:
        model.load_state_dict(state)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(params["learning_rate"]), weight_decay=float(params["weight_decay"]))
    dataset = TensorDataset(
        torch.from_numpy(scaler.transform(x_train).astype(np.float32)),
        torch.from_numpy(y_train.astype(np.float32)),
        torch.from_numpy(w_train.astype(np.float32)),
        torch.from_numpy(_market_from_odds(odds_train).astype(np.float32)),
    )
    loader = DataLoader(dataset, batch_size=256, shuffle=True)
    best_loss = None
    best_brier = None
    best_epoch = 0
    waited = 0
    curve = []
    best_state = None
    reason = "max_epochs"
    for epoch in range(1, max_epochs + 1):
        model.train()
        for batch_x, batch_y, batch_w, batch_m in loader:
            optimizer.zero_grad()
            raw = model(batch_x.to(device)).squeeze(-1)
            logits = _residual_logits(raw, batch_m.to(device))
            loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, batch_y.to(device), reduction="none")
            loss = (loss * batch_w.to(device)).sum() / batch_w.to(device).sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()
        probs = _mlp_predict(model, scaler, x_val, odds_val, device, True)
        loss_value, brier = _score(y_val, probs)
        if _improved(loss_value, brier, best_loss, best_brier, min_delta):
            best_loss, best_brier, best_epoch, waited = loss_value, brier, epoch, 0
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        else:
            waited += 1
        curve.append({"epoch": epoch, "log_loss": round(loss_value, 6), "brier": round(brier, 6), "waited": waited})
        _log_epoch("mlp", params, epoch, loss_value, brier, best_epoch, waited)
        if waited >= patience:
            reason = "validation_plateau"
            break
    model.load_state_dict(best_state)
    logger.info(
        "Epoch search stopped family=mlp params=%s reason=%s best_epoch=%d log_loss=%.4f brier=%.4f",
        json.dumps(params, sort_keys=True),
        reason,
        best_epoch,
        best_loss,
        best_brier,
    )
    return {
        "log_loss": float(best_loss),
        "brier": float(best_brier),
        "best_epoch": int(best_epoch),
        "reason": reason,
        "curve": curve,
        "state": best_state,
        "bundle": {"model": model, "scaler": scaler, "device": device},
    }


def initial_params(family: str) -> Dict[str, Any]:
    if family == "lr":
        return {"C": 1.0}
    if family == "gb":
        return {"learning_rate": 0.05, "max_depth": 4, "min_samples_leaf": 5, "l2_regularization": 0.0}
    return {"learning_rate": 1e-3, "dropout": 0.1, "weight_decay": 1e-3, "widths": [64, 32]}


def neighbors(family: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Next single-parameter changes. Values outside the useful range are omitted."""
    if family == "lr":
        value = float(params["C"])
        return [{"C": round(item, 8)} for item in (value / 10.0, value * 10.0) if 1e-4 <= item <= 1e4]
    if family == "gb":
        found = []
        rate = float(params["learning_rate"])
        if rate / 2.0 >= 0.001:
            found.append({**params, "learning_rate": round(rate / 2.0, 6)})
        penalties = [0.0, 1.0, 10.0, 100.0]
        penalty = float(params["l2_regularization"])
        if penalty in penalties and penalties.index(penalty) + 1 < len(penalties):
            found.append({**params, "l2_regularization": penalties[penalties.index(penalty) + 1]})
        leaves = [5, 20, 50, 100, 200]
        leaf = int(params["min_samples_leaf"])
        if leaf in leaves and leaves.index(leaf) + 1 < len(leaves):
            found.append({**params, "min_samples_leaf": leaves[leaves.index(leaf) + 1]})
        return found
    found = []
    rate = float(params["learning_rate"])
    if rate / 10.0 >= 1e-6:
        found.append({**params, "learning_rate": rate / 10.0})
    dropout = float(params["dropout"])
    decay = float(params["weight_decay"])
    if dropout + 0.2 <= 0.7 and decay * 10.0 <= 1.0:
        found.append({**params, "learning_rate": rate, "dropout": round(dropout + 0.2, 4), "weight_decay": decay * 10.0})
    return found


def _key(params: Dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, default=str)


def beats(candidate: Dict[str, Any], champion: Dict[str, Any], min_delta: float = MIN_DELTA) -> bool:
    return _improved(float(candidate["log_loss"]), float(candidate["brier"]), float(champion["log_loss"]), float(champion["brier"]), min_delta)


def search_until_plateau(
    family: str,
    evaluate: Callable[[Dict[str, Any], Optional[Dict[str, Any]]], Dict[str, Any]],
    initial: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Follow hyperparameter changes while validation improves, then stop."""
    current = dict(initial or initial_params(family))
    visited = {_key(current)}
    champion = evaluate(current, None)
    champion["params"] = dict(current)
    history = [_public_trial(champion)]
    while True:
        trials = []
        for candidate in neighbors(family, champion["params"]):
            marker = _key(candidate)
            if marker in visited:
                continue
            visited.add(marker)
            trial = evaluate(candidate, champion.get("state"))
            trial["params"] = dict(candidate)
            history.append(_public_trial(trial))
            trials.append(trial)
        better = [trial for trial in trials if beats(trial, champion)]
        if not better:
            logger.info("Hyperparameter search stopped family=%s reason=no_change_improved", family)
            break
        champion = min(better, key=lambda trial: (float(trial["log_loss"]), float(trial["brier"])))
        logger.info(
            "Adopted hyperparameters family=%s params=%s log_loss=%.4f brier=%.4f",
            family,
            json.dumps(champion["params"], sort_keys=True),
            float(champion["log_loss"]),
            float(champion["brier"]),
        )
    champion["visited"] = history
    return champion


def _public_trial(trial: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "params": dict(trial["params"]),
        "log_loss": float(trial["log_loss"]),
        "brier": float(trial["brier"]),
        "best_epoch": int(trial.get("best_epoch") or 0),
        "reason": trial.get("reason"),
    }


def evaluate_family(family: str, params: Dict[str, Any], train, val, names: Sequence[str], state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if family == "lr":
        result = _logistic_epoch_loop(train, val, float(params["C"]), MAX_EPOCHS, PATIENCE, MIN_DELTA, 42)
    elif family == "gb":
        result = _boosting_epoch_loop(train, val, params, MAX_EPOCHS, PATIENCE, MIN_DELTA, 42)
    else:
        result = _residual_epoch_loop(train, val, names, params, MAX_EPOCHS, PATIENCE, MIN_DELTA, state)
    result["params"] = dict(params)
    return result


def early_stop_base(family: str, params: Dict[str, Any], matches, names: Sequence[str]) -> tuple[Any, Dict[str, Any]]:
    """Choose the epoch on the late part of the base matches, then refit that epoch on all of them."""
    ordered = [match for match in matches if len(match.rows) > 0]
    cut = _holdout_cut(len(ordered), 0.15)
    train, val = ordered[:cut], ordered[cut:]
    if family == "lr":
        report = _logistic_epoch_loop(train, val, float(params["C"]), MAX_EPOCHS, PATIENCE, MIN_DELTA, 42)
        fitted = refit_logistic(ordered, float(params["C"]), int(report["best_epoch"]))
    elif family == "gb":
        report = _boosting_epoch_loop(train, val, params, MAX_EPOCHS, PATIENCE, MIN_DELTA, 42)
        fitted = refit_boosting(ordered, params, int(report["best_epoch"]))
    else:
        report = _residual_epoch_loop(train, val, names, params, MAX_EPOCHS, PATIENCE, MIN_DELTA, None)
        fitted = report["bundle"]
        report = {key: value for key, value in report.items() if key not in ("bundle", "state")}
    report["params"] = dict(params)
    report.pop("model", None)
    return fitted, report


class EpochTunedEstimator(TemporalCalibratedEstimator):
    """Temporal calibration whose base model is chosen by epoch early stopping."""

    def __init__(
        self,
        estimator=None,
        method: str = "sigmoid",
        calibration_fraction: float = 0.2,
        family: str = "lr",
        params: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(estimator=estimator, method=method, calibration_fraction=calibration_fraction)
        self.family = family
        self.params = params if params is not None else initial_params(family)

    def fit_matches(self, matches: Sequence[Any], feature_names: Optional[Sequence[str]] = None, loss_gamma: float = 0.0):
        ordered = [match for match in matches if len(match.rows) > 0]
        names = list(feature_names or [])
        self.market_feature_index_ = names.index("market_is_set") if "market_is_set" in names else None
        self.base_event_ids_ = []
        self.calibration_event_ids_ = []
        if len(ordered) < 4:
            return super().fit_matches(ordered, names, loss_gamma)
        cut = _holdout_cut(len(ordered), self.calibration_fraction)
        earlier = ordered[:cut]
        fitted, report = early_stop_base(self.family, self.params, earlier, names)
        self.epoch_report_ = report
        self._supplied_base = fitted
        return super().fit_matches(ordered, names, 0.0)


class ResidualEpochEstimator(ClassifierMixin, BaseEstimator):
    """Residual MLP with the same temporal calibration split and epoch stopping."""

    def __init__(self, params: Optional[Dict[str, Any]] = None, calibration_fraction: float = 0.2):
        self.params = params
        self.calibration_fraction = calibration_fraction

    def fit_matches(self, matches: Sequence[Any], feature_names: Optional[Sequence[str]] = None, loss_gamma: float = 0.0):
        from .models import _HoldoutCalibrator

        ordered = [match for match in matches if len(match.rows) > 0]
        names = list(feature_names or [])
        self.params = dict(self.params or initial_params("mlp"))
        self.method = "sigmoid"
        self.feature_names_ = names
        cut = _holdout_cut(len(ordered), self.calibration_fraction)
        earlier, later = ordered[:cut], ordered[cut:]
        self.base_event_ids_ = [str(match.event_id) for match in earlier]
        self.calibration_event_ids_ = [str(match.event_id) for match in later]
        fitted, report = early_stop_base("mlp", self.params, earlier, names)
        self.epoch_report_ = report
        self.classes_ = np.asarray([0, 1])
        self.model_ = ResidualProbability(fitted, names, self.params)
        if len(later) >= 2:
            from .dataset import project_market

            cal_rows, cal_labels, cal_weights = stack_matches(
                [match for match in (project_market(item, "match") for item in later) if match.rows] or later
            )
            if len(cal_rows) >= 8 and len(set(cal_labels.tolist())) > 1:
                calibrator = _HoldoutCalibrator(self.model_, "sigmoid")
                calibrator.fit_holdout(cal_rows, cal_labels, cal_weights)
                self.model_ = calibrator
        return self

    def predict_proba(self, rows: np.ndarray) -> np.ndarray:
        return self.model_.predict_proba(rows)


class ResidualProbability:
    """predict_proba rebuilt from saved weights. The network class itself is not stored."""

    def __init__(self, bundle: Dict[str, Any], names: Sequence[str], params: Dict[str, Any]):
        self.scaler = bundle["scaler"]
        self.state = {key: value.detach().cpu().clone() for key, value in bundle["model"].state_dict().items()}
        self.names = list(names)
        self.params = dict(params)
        self.classes_ = np.asarray([0, 1])

    def predict_proba(self, rows: np.ndarray) -> np.ndarray:
        import torch

        from .compare.feature_models import _mlp, _mlp_predict

        device = torch.device("cpu")
        widths = tuple(self.params.get("widths") or (64, 32))
        model = _mlp(len(self.names), widths, float(self.params["dropout"])).to(device)
        model.load_state_dict(self.state)
        matrix = np.asarray(rows, dtype=float)
        positive = _mlp_predict(model, self.scaler, matrix, _odds(matrix, self.names), device, True)
        positive = np.clip(positive, 1e-6, 1.0 - 1e-6)
        return np.column_stack([1.0 - positive, positive])


def _odds(rows: np.ndarray, names: Sequence[str]) -> np.ndarray:
    from .commit import side_odds_from_row

    return np.asarray([side_odds_from_row(row, names) for row in np.asarray(rows, dtype=float)], dtype=float)
