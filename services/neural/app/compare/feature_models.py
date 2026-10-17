"""Offline candidates on the production numeric feature tape."""

from typing import Optional, Sequence

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..commit import side_odds_from_row
from ..dataset import MatchSnapshots, positive_probabilities, thin_match
from ..models import (
    build_gradient_boosting_pipeline,
    build_logistic_regression_pipeline,
)
from .candidates.ensemble import apply_market_offset, logit

MAX_STEPS = 12
SEED = 42


def prepare(matches: Sequence[MatchSnapshots]) -> list:
    return [thin_match(match) for match in matches if match.rows]


def stack(matches: Sequence[MatchSnapshots], names: Sequence[str]):
    rows = []
    labels = []
    weights = []
    markets = []
    odds = []
    for match in matches:
        count = len(match.rows)
        if count == 0:
            continue
        weight = 1.0 / count
        for index, row in enumerate(match.rows):
            rows.append(row)
            labels.append(match.row_labels[index] if match.row_labels else match.label)
            weights.append(weight)
            markets.append(match.row_markets[index] if match.row_markets else "match_winner")
            odds.append(side_odds_from_row(row, names))
    if not rows:
        width = len(names)
        return (
            np.empty((0, width)),
            np.empty((0,), dtype=int),
            np.empty((0,)),
            [],
            np.empty((0, 2)),
        )
    return (
        np.asarray(rows, dtype=float),
        np.asarray(labels, dtype=int),
        np.asarray(weights, dtype=float),
        markets,
        np.asarray(odds, dtype=float),
    )


def market_probability(matches: Sequence[MatchSnapshots], names: Sequence[str]) -> np.ndarray:
    _x, _y, _w, _markets, odds = stack(matches, names)
    out = np.full(len(odds), 0.5, dtype=float)
    for index, (left, right) in enumerate(odds):
        if left > 1.0 and right > 1.0:
            total = 1.0 / left + 1.0 / right
            out[index] = (1.0 / left) / total
    return out


def _loss(labels, probs) -> float:
    if len(labels) < 2 or len(set(labels.tolist())) < 2:
        return 1.0
    return float(log_loss(labels, np.clip(probs, 1e-6, 1 - 1e-6), labels=[0, 1]))


class MatrixModel:
    def __init__(self):
        self.model = None
        self.trials = []
        self.chosen = None
        self.calibration_report_ = {}

    def predict(self, matches, names) -> np.ndarray:
        rows, _y, _w, _markets, _odds = stack(matches, names)
        if len(rows) == 0:
            return np.empty((0,), dtype=float)
        return positive_probabilities(self.model, rows)


class LogisticFeatureModel(MatrixModel):
    name = "logistic"

    def select(self, train, validation, names) -> None:
        x_train, y_train, w_train, _m, _o = stack(train, names)
        x_val, y_val, _w, _m, _o = stack(validation, names)
        best = None
        best_loss = None
        for c_value in (0.01, 0.1, 1.0, 10.0):
            model = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("classifier", LogisticRegression(C=c_value, max_iter=1000, solver="lbfgs")),
                ]
            )
            model.fit(x_train, y_train, classifier__sample_weight=w_train)
            loss = _loss(y_val, model.predict_proba(x_val)[:, 1])
            self.trials.append({"C": c_value, "log_loss": round(loss, 6)})
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = c_value
                self.model = model
        self.chosen = {"C": best}

    def fit_final(self, train, validation, names) -> None:
        model = build_logistic_regression_pipeline(C=float(self.chosen["C"]), calibration_method="sigmoid")
        model.fit_matches(list(train) + list(validation), names)
        self.model = model
        self.calibration_report_ = getattr(model, "calibration_report_", {})


class BoostingFeatureModel(MatrixModel):
    name = "boosting"

    def select(self, train, validation, names) -> None:
        x_train, y_train, w_train, _m, _o = stack(train, names)
        x_val, y_val, _w, _m, _o = stack(validation, names)
        grid = (
            {"learning_rate": 0.03, "max_depth": 3, "min_samples_leaf": 50},
            {"learning_rate": 0.05, "max_depth": 4, "min_samples_leaf": 20},
            {"learning_rate": 0.1, "max_depth": 3, "min_samples_leaf": 20},
        )
        best = None
        best_loss = None
        for params in grid:
            model = HistGradientBoostingClassifier(max_iter=120, l2_regularization=1.0, random_state=SEED, **params)
            model.fit(x_train, y_train, sample_weight=w_train)
            loss = _loss(y_val, model.predict_proba(x_val)[:, 1])
            trial = {**params, "log_loss": round(loss, 6)}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = params
                self.model = model
        self.chosen = dict(best)

    def fit_final(self, train, validation, names) -> None:
        model = build_gradient_boosting_pipeline(
            learning_rate=self.chosen["learning_rate"],
            max_iter=120,
            max_depth=self.chosen["max_depth"],
            min_samples_leaf=self.chosen["min_samples_leaf"],
            l2_regularization=1.0,
            calibration_method="isotonic",
        )
        model.fit_matches(list(train) + list(validation), names)
        self.model = model
        self.calibration_report_ = getattr(model, "calibration_report_", {})


class BaggingFeatureModel(MatrixModel):
    name = "bagging"

    def select(self, train, validation, names) -> None:
        x_train, y_train, w_train, _m, _o = stack(train, names)
        x_val, y_val, _w, _m, _o = stack(validation, names)
        best = None
        best_loss = None
        for kind, trees in (("rf", 100), ("et", 100), ("rf", 300), ("et", 300)):
            model = self._build(kind, trees)
            model.fit(x_train, y_train, sample_weight=w_train)
            loss = _loss(y_val, model.predict_proba(x_val)[:, 1])
            self.trials.append({"kind": kind, "n_estimators": trees, "log_loss": round(loss, 6)})
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = {"kind": kind, "n_estimators": trees}
                self.model = model
        self.chosen = best

    def fit_final(self, train, validation, names) -> None:
        from ..models import TemporalCalibratedEstimator

        model = TemporalCalibratedEstimator(
            estimator=self._build(self.chosen["kind"], self.chosen["n_estimators"]),
            method="sigmoid",
        )
        model.fit_matches(list(train) + list(validation), names)
        self.model = model
        self.calibration_report_ = getattr(model, "calibration_report_", {})

    @staticmethod
    def _build(kind: str, trees: int):
        common = {
            "n_estimators": trees,
            "min_samples_leaf": 5,
            "max_depth": 8,
            "n_jobs": 2,
            "random_state": SEED,
        }
        if kind == "et":
            return ExtraTreesClassifier(**common)
        return RandomForestClassifier(**common)


class CatBoostFeatureModel(MatrixModel):
    name = "catboost"

    def __init__(self):
        super().__init__()
        self.available = False
        try:
            import catboost  # noqa: F401

            self.available = True
        except ImportError:
            self.available = False

    def select(self, train, validation, names) -> None:
        if not self.available:
            return
        from catboost import CatBoostClassifier

        x_train, y_train, w_train, _m, _o = stack(train, names)
        x_val, y_val, _w, _m, _o = stack(validation, names)
        model = CatBoostClassifier(
            iterations=300,
            learning_rate=0.05,
            depth=6,
            loss_function="Logloss",
            random_seed=SEED,
            thread_count=2,
            verbose=False,
            allow_writing_files=False,
        )
        model.fit(x_train, y_train, sample_weight=w_train, eval_set=(x_val, y_val), use_best_model=True)
        loss = _loss(y_val, model.predict_proba(x_val)[:, 1])
        self.trials.append({"iterations": 300, "log_loss": round(loss, 6)})
        self.model = model
        self.chosen = {"iterations": 300, "depth": 6}

    def fit_final(self, train, validation, names) -> None:
        from catboost import CatBoostClassifier
        from ..models import TemporalCalibratedEstimator

        estimator = CatBoostClassifier(
            iterations=300,
            learning_rate=0.05,
            depth=6,
            loss_function="Logloss",
            random_seed=SEED,
            thread_count=2,
            verbose=False,
            allow_writing_files=False,
        )
        model = TemporalCalibratedEstimator(estimator=estimator, method="sigmoid")
        model.fit_matches(list(train) + list(validation), names)
        self.model = model
        self.calibration_report_ = getattr(model, "calibration_report_", {})


def group_mask(names: Sequence[str], family: str) -> np.ndarray:
    if family == "full":
        return np.ones(len(names), dtype=bool)
    keys = {
        "market": ("odds", "market_", "implied", "line_", "drift", "velocity", "volatility", "suspension"),
        "score": ("set", "game", "point", "server", "break", "tiebreak", "elapsed", "ace", "fault", "paren", "serve", "return"),
        "history": ("player", "h2h", "prior", "elo", "injury", "fatigue", "news", "context", "rank"),
    }[family]
    return np.asarray([any(key in name for key in keys) for name in names], dtype=bool)


class AblationFeatureModel(MatrixModel):
    name = "ablation"

    def select(self, train, validation, names) -> None:
        x_train, y_train, w_train, _m, _o = stack(train, names)
        x_val, y_val, _w, _m, _o = stack(validation, names)
        best = None
        best_loss = None
        for family in ("market", "score", "history", "full"):
            mask = group_mask(names, family)
            if not mask.any():
                mask = np.ones(len(names), dtype=bool)
            model = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("classifier", LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs")),
                ]
            )
            model.fit(x_train[:, mask], y_train, classifier__sample_weight=w_train)
            loss = _loss(y_val, model.predict_proba(x_val[:, mask])[:, 1])
            self.trials.append({"group": family, "columns": int(mask.sum()), "log_loss": round(loss, 6)})
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = family
                self.model = model
        self.chosen = {"group": best}

    def fit_final(self, train, validation, names) -> None:
        self.mask_ = group_mask(names, self.chosen["group"])
        if not self.mask_.any():
            self.mask_ = np.ones(len(names), dtype=bool)
        rows, labels, weights, markets, _o = stack(validation, names)
        raw = self.model.predict_proba(rows[:, self.mask_])[:, 1]
        self._calibrators = {}
        report = {"group": self.chosen["group"], "columns": int(self.mask_.sum())}
        from .calibration import ProbabilityCalibrator

        for family, predicate in (
            ("match", lambda market: market == "match_winner"),
            ("set", lambda market: str(market).startswith("set_winner")),
        ):
            indexes = [index for index, market in enumerate(markets) if predicate(market)]
            calibrator = ProbabilityCalibrator("sigmoid")
            if len(indexes) >= 8 and len(set(labels[indexes].tolist())) > 1:
                calibrator.fit(
                    raw[indexes],
                    labels[indexes],
                    [f"{family}-{index}" for index in indexes],
                    set(),
                    weights[indexes],
                )
            self._calibrators[family] = calibrator
            report[family] = {"method": calibrator.method, "rows": len(indexes)}
        self.calibration_report_ = report

    def predict(self, matches, names) -> np.ndarray:
        rows, _y, _w, markets, _o = stack(matches, names)
        if len(rows) == 0:
            return np.empty((0,), dtype=float)
        raw = self.model.predict_proba(rows[:, self.mask_])[:, 1]
        return _apply_family_calibrators(raw, markets, self._calibrators)


def _torch_device():
    import torch

    if not torch.cuda.is_available():
        return torch.device("cpu"), "cpu"
    major, _minor = torch.cuda.get_device_capability()
    if major < 7:
        return torch.device("cpu"), "cpu"
    return torch.device("cuda"), "cuda"


def _fit_mlp(train, validation, names, widths, dropout, weight_decay, max_epochs, residual: bool, gamma: float = 0.0):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    device, device_name = _torch_device()
    torch.manual_seed(SEED)
    torch.set_num_threads(2)
    x_train, y_train, w_train, _m, odds_train = stack(train, names)
    if float(gamma) > 0.0 and len(w_train):
        from ..dataset import example_weights

        prelim = _fit_mlp(train, validation, names, widths, dropout, weight_decay, max_epochs, residual, 0.0)
        prelim_probs = _mlp_predict(prelim["model"], prelim["scaler"], x_train, odds_train, prelim["device"], residual)
        w_train = example_weights(w_train, prelim_probs, y_train, gamma)
    x_val, y_val, _w, _m, odds_val = stack(validation, names)
    scaler = StandardScaler().fit(x_train)
    model = _mlp(len(names), widths, dropout).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=weight_decay)
    market_train = _market_from_odds(odds_train)
    dataset = TensorDataset(
        torch.from_numpy(scaler.transform(x_train).astype(np.float32)),
        torch.from_numpy(y_train.astype(np.float32)),
        torch.from_numpy(w_train.astype(np.float32)),
        torch.from_numpy(market_train.astype(np.float32)),
    )
    loader = DataLoader(dataset, batch_size=256, shuffle=True)
    best_state = None
    best_loss = None
    waited = 0
    stopped = 0
    for epoch in range(max_epochs):
        model.train()
        for batch_x, batch_y, batch_w, batch_m in loader:
            optimizer.zero_grad()
            raw = model(batch_x.to(device)).squeeze(-1)
            logits = _residual_logits(raw, batch_m.to(device)) if residual else raw
            loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, batch_y.to(device), reduction="none")
            loss = (loss * batch_w.to(device)).sum() / batch_w.to(device).sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()
        stopped = epoch + 1
        probs = _mlp_predict(model, scaler, x_val, odds_val, device, residual)
        loss_value = _loss(y_val, probs)
        if best_loss is None or loss_value < best_loss - 1e-4:
            best_loss = loss_value
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            waited = 0
        else:
            waited += 1
            if waited >= 4:
                break
    model.load_state_dict(best_state)
    return {
        "model": model,
        "scaler": scaler,
        "device": device,
        "device_name": device_name,
        "epochs": stopped,
        "parameters": int(sum(item.numel() for item in model.parameters())),
        "residual": residual,
    }


def _mlp(width, widths, dropout):
    from torch import nn

    layers = []
    previous = width
    for size in widths:
        layers.extend([nn.Linear(previous, size), nn.ReLU(), nn.Dropout(dropout)])
        previous = size
    layers.append(nn.Linear(previous, 1))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(*layers)

        def forward(self, batch):
            return self.net(batch)

    return Net()


def _market_from_odds(odds: np.ndarray) -> np.ndarray:
    out = np.full(len(odds), 0.5, dtype=np.float32)
    for index, (left, right) in enumerate(odds):
        if left > 1.0 and right > 1.0:
            total = 1.0 / left + 1.0 / right
            out[index] = (1.0 / left) / total
    return out


def _residual_logits(raw, market):
    import torch

    base = torch.log(market.clamp(1e-4, 1 - 1e-4) / (1 - market.clamp(1e-4, 1 - 1e-4)))
    return base + raw.clamp(-0.35, 0.35)


def _mlp_predict(model, scaler, rows, odds, device, residual: bool) -> np.ndarray:
    import torch

    if len(rows) == 0:
        return np.empty((0,), dtype=float)
    model.eval()
    with torch.no_grad():
        raw = model(torch.from_numpy(scaler.transform(rows).astype(np.float32)).to(device)).squeeze(-1)
        if residual:
            logits = _residual_logits(raw, torch.from_numpy(_market_from_odds(odds)).to(device))
        else:
            logits = raw
        return torch.sigmoid(logits).detach().cpu().numpy()


class TorchFeatureModel:
    configs = ()
    residual = False
    name = "mlp"

    def __init__(self, max_epochs: int = 25):
        self.max_epochs = max_epochs
        self.bundle = None
        self.trials = []
        self.chosen = None
        self.calibration_report_ = {}
        self._calibrators = {}

    def select(self, train, validation, names) -> None:
        best = None
        best_loss = None
        for config in self.configs:
            bundle = _fit_mlp(
                train,
                validation,
                names,
                config["widths"],
                config["dropout"],
                config["weight_decay"],
                self.max_epochs,
                self.residual,
            )
            rows, labels, _w, _m, odds = stack(validation, names)
            probs = _mlp_predict(bundle["model"], bundle["scaler"], rows, odds, bundle["device"], self.residual)
            loss = _loss(labels, probs)
            trial = {**config, "widths": list(config["widths"]), "log_loss": round(loss, 6), "epochs": bundle["epochs"]}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = bundle
                self.chosen = trial
        self.bundle = best

    def fit_final(self, train, validation, names) -> None:
        self._names = list(names)
        self._fit_market_calibrators(validation, names)

    def predict(self, matches, names) -> np.ndarray:
        rows, _y, _w, markets, odds = stack(matches, names)
        raw = _mlp_predict(
            self.bundle["model"], self.bundle["scaler"], rows, odds, self.bundle["device"], self.residual
        )
        return _apply_family_calibrators(raw, markets, self._calibrators)

    def _fit_market_calibrators(self, validation, names) -> None:
        from .calibration import ProbabilityCalibrator

        rows, labels, weights, markets, odds = stack(validation, names)
        raw = _mlp_predict(
            self.bundle["model"], self.bundle["scaler"], rows, odds, self.bundle["device"], self.residual
        )
        self._calibrators = {}
        report = {}
        for family, predicate in (("match", lambda market: market == "match_winner"), ("set", lambda market: str(market).startswith("set_winner"))):
            indexes = [index for index, market in enumerate(markets) if predicate(market)]
            calibrator = ProbabilityCalibrator("sigmoid")
            if len(indexes) >= 8 and len(set(labels[indexes].tolist())) > 1:
                calibrator.fit(raw[indexes], labels[indexes], [f"{family}-{index}" for index in indexes], set(), weights[indexes])
            self._calibrators[family] = calibrator
            report[family] = {"method": calibrator.method, "rows": len(indexes), "reason": calibrator.skipped_reason}
        self.calibration_report_ = report


class MLPFeatureModel(TorchFeatureModel):
    name = "mlp"
    residual = False
    configs = (
        {"widths": (32, 16), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (64, 32), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (32, 16), "dropout": 0.3, "weight_decay": 1e-2},
    )


class ResidualMLPFeatureModel(TorchFeatureModel):
    name = "residual_mlp"
    residual = True
    configs = (
        {"widths": (32, 16), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (64, 32), "dropout": 0.1, "weight_decay": 1e-3},
    )

    def predict(self, matches, names) -> np.ndarray:
        predicted = super().predict(matches, names)
        market = market_probability(matches, names)
        if len(predicted) and float(np.nanmax(np.abs(logit(predicted) - logit(market)))) > 0.35 + 1e-5:
            return apply_market_offset(market, np.clip(logit(predicted) - logit(market), -0.35, 0.35))
        return predicted


def _apply_family_calibrators(raw, markets, calibrators) -> np.ndarray:
    out = np.asarray(raw, dtype=float).copy()
    for index, market in enumerate(markets):
        family = "set" if str(market).startswith("set_winner") else "match"
        calibrator = calibrators.get(family)
        if calibrator is not None:
            out[index] = float(calibrator.transform(np.asarray([out[index]]))[0])
    return out


def prefix_lengths(matches: Sequence[MatchSnapshots]) -> list:
    """Length of the visible prefix for every row. Later rows of the same market are excluded."""
    lengths = []
    for match in matches:
        seen = {}
        markets = match.row_markets or ["match_winner"] * len(match.rows)
        order = sorted(range(len(match.rows)), key=lambda index: (match.observed_at[index], index))
        position = {}
        for index in order:
            market = str(markets[index])
            seen[market] = seen.get(market, 0) + 1
            position[index] = min(seen[market], MAX_STEPS)
        lengths.extend(position[index] for index in range(len(match.rows)))
    return lengths


class GRUFeatureModel:
    name = "gru"
    widths = (8, 16)

    def __init__(self, max_epochs: int = 25):
        self.max_epochs = max_epochs
        self.bundle = None
        self.trials = []
        self.chosen = None
        self.calibration_report_ = {}
        self._calibrators = {}

    def select(self, train, validation, names) -> None:
        best = None
        best_loss = None
        for hidden in self.widths:
            bundle = self._fit(train, validation, names, hidden)
            probs = self._predict_bundle(bundle, validation, names)
            _x, labels, _w, _m, _o = stack(validation, names)
            loss = _loss(labels, probs)
            trial = {"hidden": hidden, "log_loss": round(loss, 6), "epochs": bundle["epochs"]}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = bundle
                self.chosen = trial
        self.bundle = best

    def fit_final(self, train, validation, names) -> None:
        self._names = list(names)
        raw = self._predict_bundle(self.bundle, validation, names)
        _x, labels, weights, markets, _o = stack(validation, names)
        from .calibration import ProbabilityCalibrator

        self._calibrators = {}
        report = {}
        for family, predicate in (("match", lambda market: market == "match_winner"), ("set", lambda market: str(market).startswith("set_winner"))):
            indexes = [index for index, market in enumerate(markets) if predicate(market)]
            calibrator = ProbabilityCalibrator("sigmoid")
            if len(indexes) >= 8 and len(set(int(labels[index]) for index in indexes)) > 1:
                calibrator.fit(
                    raw[indexes],
                    labels[indexes],
                    [f"{family}-{index}" for index in indexes],
                    set(),
                    weights[indexes],
                )
            self._calibrators[family] = calibrator
            report[family] = {"method": calibrator.method, "rows": len(indexes)}
        self.calibration_report_ = report

    def predict(self, matches, names) -> np.ndarray:
        raw = self._predict_bundle(self.bundle, matches, names)
        _x, _y, _w, markets, _o = stack(matches, names)
        return _apply_family_calibrators(raw, markets, self._calibrators)

    def _fit(self, train, validation, names, hidden):
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset

        device, device_name = _torch_device()
        torch.manual_seed(SEED)
        torch.set_num_threads(2)
        x_train, _y, _w, _m, _o = stack(train, names)
        scaler = StandardScaler().fit(x_train)
        sequences, labels, lengths, weights = _prefixes(train, names, scaler)
        model = _gru(len(names), hidden).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-3)
        loader = DataLoader(
            TensorDataset(
                torch.from_numpy(sequences),
                torch.from_numpy(labels),
                torch.from_numpy(lengths),
                torch.from_numpy(weights),
            ),
            batch_size=64,
            shuffle=True,
        )
        best_state = None
        best_loss = None
        waited = 0
        stopped = 0
        for epoch in range(self.max_epochs):
            model.train()
            for batch_x, batch_y, batch_len, batch_w in loader:
                optimizer.zero_grad()
                logits = model(batch_x.to(device), batch_len.to(device))
                batch_w = batch_w.to(device)
                loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, batch_y.to(device), reduction="none")
                loss = (loss * batch_w).sum() / batch_w.sum().clamp_min(1e-6)
                loss.backward()
                optimizer.step()
            stopped = epoch + 1
            probs = self._predict_raw(model, scaler, validation, names, device)
            _x, y_val, _w, _m, _o = stack(validation, names)
            loss_value = _loss(y_val, probs)
            if best_loss is None or loss_value < best_loss - 1e-4:
                best_loss = loss_value
                best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
                waited = 0
            else:
                waited += 1
                if waited >= 4:
                    break
        model.load_state_dict(best_state)
        return {
            "model": model,
            "scaler": scaler,
            "device": device,
            "device_name": device_name,
            "epochs": stopped,
            "parameters": int(sum(item.numel() for item in model.parameters())),
            "hidden": hidden,
        }

    def _predict_bundle(self, bundle, matches, names) -> np.ndarray:
        return self._predict_raw(bundle["model"], bundle["scaler"], matches, names, bundle["device"])

    @staticmethod
    def _predict_raw(model, scaler, matches, names, device) -> np.ndarray:
        import torch

        sequences, _labels, lengths, _weights, positions = _prefixes(matches, names, scaler, with_positions=True)
        if len(positions) == 0:
            return np.empty((0,), dtype=float)
        model.eval()
        with torch.no_grad():
            logits = model(torch.from_numpy(sequences).to(device), torch.from_numpy(lengths).to(device))
            probs = torch.sigmoid(logits).detach().cpu().numpy()
        out = np.full(int(positions.max()) + 1, 0.5, dtype=float)
        out[positions] = probs
        return out


def _gru(width, hidden):
    from torch import nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(input_size=width, hidden_size=hidden, batch_first=True)
            self.head = nn.Linear(hidden, 1)

        def forward(self, batch, lengths):
            packed = nn.utils.rnn.pack_padded_sequence(batch, lengths.cpu(), batch_first=True, enforce_sorted=False)
            _out, state = self.gru(packed)
            return self.head(state[-1]).squeeze(-1)

    return Net()


def _prefixes(matches, names, scaler, with_positions: bool = False):
    sequences = []
    labels = []
    lengths = []
    weights = []
    positions = []
    cursor = 0
    width = len(names)
    for match in matches:
        markets = match.row_markets or ["match_winner"] * len(match.rows)
        grouped = {}
        for index, row in enumerate(match.rows):
            grouped.setdefault(str(markets[index]), []).append(index)
        for indexes in grouped.values():
            ordered = sorted(indexes, key=lambda index: (match.observed_at[index], index))
            for end, index in enumerate(ordered):
                prefix = ordered[: end + 1]
                if len(prefix) > MAX_STEPS:
                    slots = []
                    for slot in range(MAX_STEPS):
                        picked = int(round(slot * (len(prefix) - 1) / (MAX_STEPS - 1)))
                        if not slots or slots[-1] != picked:
                            slots.append(picked)
                    prefix = [prefix[slot] for slot in slots]
                matrix = scaler.transform(np.asarray([match.rows[item] for item in prefix], dtype=float)).astype(np.float32)
                padded = np.zeros((MAX_STEPS, width), dtype=np.float32)
                padded[: len(prefix)] = matrix
                sequences.append(padded)
                label = match.row_labels[index] if match.row_labels else match.label
                labels.append(float(label))
                lengths.append(len(prefix))
                weights.append(1.0 / len(ordered))
                positions.append(cursor + index)
        cursor += len(match.rows)
    if not sequences:
        empty = np.zeros((0, MAX_STEPS, width), dtype=np.float32)
        if with_positions:
            return empty, np.zeros(0, np.float32), np.zeros(0, np.int64), np.zeros(0, np.float32), np.zeros(0, np.int64)
        return empty, np.zeros(0, np.float32), np.zeros(0, np.int64), np.zeros(0, np.float32)
    packed = (
        np.asarray(sequences, dtype=np.float32),
        np.asarray(labels, dtype=np.float32),
        np.asarray(lengths, dtype=np.int64),
        np.asarray(weights, dtype=np.float32),
    )
    if with_positions:
        return packed + (np.asarray(positions, dtype=np.int64),)
    return packed
