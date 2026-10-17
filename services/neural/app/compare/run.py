"""Fit candidates on train, choose calibrators on validation, score the frozen test once."""

import json
import logging
from typing import Dict, List, Sequence

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.models import ExperimentResult

logger = logging.getLogger("neural.compare")

from .calibration import ProbabilityCalibrator
from .candidates.bagging import TreeBaggingCandidate
from .candidates.boosting import CatBoostCandidate, HistGradientCandidate
from .candidates.markov import MarkovCandidate
from .candidates.networks import GRUCandidate, MLPCandidate, ResidualMLPCandidate
from .candidates.residual import ResidualAblation, ResidualCandidate, markov_probabilities
from .candidates.logistic import LogisticCandidate, _matrix
from .candidates.market import MarketCandidate
from .candidates.rating import RatingBook, RatingCandidate
from ..metrics import error_summary, sliced_metrics
from .report import match_log_losses, score_candidate
from .rows import CompareRow
from .splits import assert_disjoint, event_ids, split_fixed_test, split_matches, test_fingerprint


def _probability_records(rows: Sequence[CompareRow], probabilities: np.ndarray) -> list:
    """Same decision fields the walk-forward report slices, one row per priced snapshot."""
    records = []
    for row, prob in zip(rows, probabilities):
        price = row.odds_a if float(prob) >= 0.5 else row.odds_b
        records.append({
            "market": "match_winner",
            "market_family": "match",
            "label": int(row.label),
            "probability": float(prob),
            "odds": float(price or 0.0),
            "phase": row.phase or "unknown",
            "sport_code": "tennis",
        })
    return records


def frozen_test_ids(session: Session) -> set:
    rows = session.execute(
        text("SELECT strategy_config FROM experiment_results WHERE name LIKE 'tennis_model_compare%'")
    ).fetchall()
    found = set()
    for (config,) in rows:
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(config, dict):
            found.update(config.get("test_event_ids") or [])
    return found


def _weights(rows: Sequence[CompareRow]) -> np.ndarray:
    _x, _y, weights = _matrix(rows)
    return weights


def _choose_calibrator(raw: np.ndarray, fit_rows: Sequence[CompareRow], select_rows: Sequence[CompareRow], test_ids: set):
    """Pick none, sigmoid, or isotonic on the later validation matches, then refit on all validation."""
    if not fit_rows or not select_rows:
        calibrator = ProbabilityCalibrator("none")
        calibrator.fit(raw[:1], np.asarray([0]), ["none"], set())
        return calibrator
    fit_ids = set(event_ids(fit_rows))
    raw_by_id = {}
    # raw is aligned with the concatenation of fit_rows then we pass slices from the caller.
    best = None
    best_loss = None
    labels_select = np.asarray([row.label for row in select_rows], dtype=int)
    for method in ("none", "sigmoid", "isotonic"):
        calibrator = ProbabilityCalibrator(method)
        fit_probs = raw[: len(fit_rows)]
        calibrator.fit(
            fit_probs,
            np.asarray([row.label for row in fit_rows], dtype=int),
            event_ids(fit_rows),
            test_ids,
            _weights(fit_rows),
        )
        chosen = calibrator.transform(raw[len(fit_rows) : len(fit_rows) + len(select_rows)])
        if len(set(labels_select.tolist())) < 2:
            loss = 1.0
        else:
            from sklearn.metrics import log_loss

            loss = float(log_loss(labels_select, np.clip(chosen, 1e-6, 1 - 1e-6), labels=[0, 1]))
        if best_loss is None or loss < best_loss:
            best_loss = loss
            best = method
    full = ProbabilityCalibrator(best or "none")
    full_rows_probs = raw[: len(fit_rows) + len(select_rows)]
    full.fit(
        full_rows_probs,
        np.asarray([row.label for row in list(fit_rows) + list(select_rows)], dtype=int),
        event_ids(list(fit_rows) + list(select_rows)),
        test_ids,
        _weights(list(fit_rows) + list(select_rows)),
    )
    return full


def _half(rows: Sequence[CompareRow]):
    ids = event_ids(rows)
    cut = max(1, int(len(ids) * 0.5))
    first = set(ids[:cut])
    fit_rows = [row for row in rows if row.event_id in first]
    select_rows = [row for row in rows if row.event_id not in first]
    if not select_rows:
        select_rows = fit_rows
    return fit_rows, select_rows


def _tour_slices(select_rows: Sequence[CompareRow], test: Sequence[CompareRow], market_test: np.ndarray) -> List[dict]:
    """Fit the same residual inside one tour when the frozen test has at least 200 matches."""
    slices = []
    for level in ("atp_wta", "challenger", "itf"):
        indexes = [index for index, row in enumerate(test) if row.tour_level == level]
        test_level = [test[index] for index in indexes]
        n_matches = len({row.event_id for row in test_level})
        if n_matches < 200:
            slices.append({"tour_level": level, "status": "insufficient", "test_matches": n_matches})
            continue
        select_level = [row for row in select_rows if row.tour_level == level]
        market_level = np.asarray([market_test[index] for index in indexes], dtype=float)
        markov_select = markov_probabilities(select_level)
        markov_level = markov_probabilities(test_level)
        level_reports = []
        for kind in ("logistic", "hist"):
            residual = ResidualCandidate(kind)
            residual.fit(select_level, markov_select)
            report = score_candidate(test_level, residual.predict(test_level, markov_level), market_level)
            report["name"] = residual.name
            report["calibrator"] = "offset"
            level_reports.append(report)
        slices.append(
            {
                "tour_level": level,
                "status": "scored",
                "test_matches": n_matches,
                "candidates": level_reports,
            }
        )
    return slices


def _ablation_reports(select_rows: Sequence[CompareRow], test: Sequence[CompareRow], market_test: np.ndarray) -> List[dict]:
    """Market, Elo, score and full residual, scored once on the frozen test."""
    markov_select = markov_probabilities(select_rows)
    markov_test = markov_probabilities(test)
    reports = []
    for family in ("market", "elo", "score", "full"):
        ablation = ResidualAblation(family)
        ablation.fit(select_rows, markov_select)
        report = score_candidate(test, ablation.predict(test, markov_test), market_test)
        report["name"] = f"ablation_{family}"
        report["calibrator"] = "offset"
        report["fit_matches"] = ablation.fit_matches
        report["insufficient"] = bool(ablation.insufficient)
        report["status"] = "insufficient" if ablation.insufficient else "scored"
        if ablation.insufficient:
            report["beats_market"] = False
        reports.append(report)
    return reports


def run_ablation(session: Session, rows: Sequence[CompareRow], history, phase: str) -> dict:
    """Validation chooses nothing here. The four families are fixed and the test is scored once."""
    book = RatingBook(history)
    book.attach(rows)
    train, validation, test = split_matches(rows)
    assert_disjoint(train, validation, test)
    _fit_rows, select_rows = _half(validation)
    market = MarketCandidate()
    market_test = market.predict(test)
    reports = _ablation_reports(select_rows, test, market_test)
    payload = {
        "phase": phase,
        "kind": "ablation",
        "test_fingerprint": test_fingerprint(test),
        "test_event_ids": event_ids(test),
        "train_matches": len(event_ids(train)),
        "validation_matches": len(event_ids(validation)),
        "test_matches": len(event_ids(test)),
        "candidates": reports,
    }
    session.add(
        ExperimentResult(
            name=f"tennis_ablation_{phase}",
            strategy_config=payload,
            backtest_pnl=0.0,
            backtest_roi=0.0,
            win_rate=0.0,
            max_drawdown=0.0,
        )
    )
    session.commit()
    return payload


def run_comparison(session: Session, rows: Sequence[CompareRow], history, phase: str) -> dict:
    book = RatingBook(history)
    book.attach(rows)
    train, validation, test = split_matches(rows)
    assert_disjoint(train, validation, test)
    blocked = frozen_test_ids(session)
    if blocked & set(event_ids(train) + event_ids(validation)):
        raise RuntimeError("Подбор параметров задевает матчи ранее замороженного теста.")
    if not train or not validation or not test:
        raise RuntimeError("После embargo одна из частей разбиения пустая.")

    logistic = LogisticCandidate()
    logistic.select(train, validation)
    boosting = HistGradientCandidate()
    boosting.select(train, validation)
    catboost = CatBoostCandidate()
    if catboost.available:
        catboost.select(train, validation)
    candidates = [MarketCandidate(), RatingCandidate(), logistic, boosting]
    if catboost.available and catboost.model is not None:
        candidates.append(catboost)

    fit_rows, select_rows = _half(validation)
    ordered_val = list(fit_rows) + list(select_rows)
    test_ids = set(event_ids(test))
    reports = []
    market_test = None
    for candidate in candidates:
        raw_val = candidate.predict(ordered_val)
        calibrator = _choose_calibrator(raw_val, fit_rows, select_rows, test_ids)
        test_probs = calibrator.transform(candidate.predict(test))
        if candidate.name == "A_market":
            market_test = test_probs
        report = score_candidate(test, test_probs, market_test if market_test is not None else test_probs)
        report["name"] = candidate.name
        report["calibrator"] = calibrator.method
        report["calibrator_note"] = calibrator.skipped_reason
        if hasattr(candidate, "chosen_c"):
            report["C"] = candidate.chosen_c
        if hasattr(candidate, "params") and candidate.name.startswith("D_hist"):
            report["params"] = candidate.params
        reports.append(report)

    markov_select = markov_probabilities(select_rows)
    markov_test = markov_probabilities(test)
    for kind in ("logistic", "hist"):
        residual = ResidualCandidate(kind)
        residual.fit(select_rows, markov_select)
        residual_test = residual.predict(test, markov_test)
        residual_report = score_candidate(test, residual_test, market_test)
        residual_report["name"] = residual.name
        residual_report["calibrator"] = "offset"
        residual_report["fit_rows"] = len({row.event_id for row in select_rows})
        reports.append(residual_report)
    reports.extend(_ablation_reports(select_rows, test, market_test))
    tour_slices = _tour_slices(select_rows, test, market_test)

    payload = {
        "phase": phase,
        "test_fingerprint": test_fingerprint(test),
        "test_event_ids": event_ids(test),
        "train_matches": len(event_ids(train)),
        "validation_matches": len(event_ids(validation)),
        "test_matches": len(event_ids(test)),
        "selection_split": "validation_second_half",
        "candidates": reports,
        "tour_slices": tour_slices,
    }
    best = min(reports, key=lambda item: item["metrics"]["log_loss"])
    bets = best["bets"]
    session.add(
        ExperimentResult(
            name=f"tennis_model_compare_{phase}",
            strategy_config=payload,
            backtest_pnl=float(bets["roi"] * max(bets["bets"], 1)),
            backtest_roi=float(bets["roi"]),
            win_rate=float(bets["win_rate"]),
            max_drawdown=float(bets["max_drawdown"]),
        )
    )
    session.commit()
    return payload


def _match_loss(rows: Sequence[CompareRow], probs: np.ndarray) -> float:
    losses = match_log_losses(rows, np.asarray(probs, dtype=float))
    if not losses:
        return 1.0
    return float(np.mean(list(losses.values())))


def _reject_test(rows: Sequence[CompareRow], test_ids: set) -> None:
    if set(event_ids(rows)) & set(test_ids):
        raise RuntimeError("Подбор увидел матчи теста.")


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    return value


def _hypothesis_blocked(session: Session) -> set:
    rows = session.execute(
        text("SELECT strategy_config FROM experiment_results WHERE name LIKE 'tennis_hypothesis_holdout%'")
    ).fetchall()
    found = set()
    for (config,) in rows:
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(config, dict):
            found.update(config.get("test_event_ids") or [])
    return found


def run_hypothesis(
    session: Session,
    rows: Sequence[CompareRow],
    history,
    phase: str,
    test_matches: int = 100,
) -> dict:
    """Choose one finalist per family on earlier matches and score the latest holdout once."""
    book = RatingBook(history)
    book.attach(rows)
    train, validation, test = split_fixed_test(rows, test_matches=test_matches)
    assert_disjoint(train, validation, test)
    blocked = _hypothesis_blocked(session)
    if blocked & set(event_ids(train) + event_ids(validation)):
        raise RuntimeError("Подбор параметров задевает матчи ранее замороженного теста.")
    logger.info(
        "Hypothesis split train=%d validation=%d test=%d",
        len(event_ids(train)),
        len(event_ids(validation)),
        len(event_ids(test)),
    )
    test_ids = set(event_ids(test))
    _reject_test(train, test_ids)
    _reject_test(validation, test_ids)
    fit_rows, select_rows = _half(validation)
    ordered_val = list(fit_rows) + list(select_rows)
    market_test = np.asarray([row.market_prob_a for row in test], dtype=float)
    trials = []
    reports = []

    def finish(family: str, candidate, validation_loss: float, predict_val, predict_test):
        calibrator = _choose_calibrator(np.asarray(predict_val(), dtype=float), fit_rows, select_rows, test_ids)
        report = score_candidate(test, calibrator.transform(np.asarray(predict_test(), dtype=float)), market_test)
        report["name"] = candidate.name
        report["family"] = family
        report["calibrator"] = calibrator.method
        report["calibrator_note"] = calibrator.skipped_reason
        report["validation_log_loss"] = round(float(validation_loss), 6)
        chosen = getattr(candidate, "chosen", None)
        if chosen is None:
            chosen = getattr(candidate, "chosen_c", None)
        if chosen is None and hasattr(candidate, "params"):
            chosen = candidate.params
        report["selection"] = _jsonable(chosen)
        reports.append(report)

    market = MarketCandidate()
    market_loss = _match_loss(validation, market.predict(validation))
    for method in ("none", "sigmoid", "isotonic"):
        probe_rows, probe_select = _half(validation)
        probe = list(probe_rows) + list(probe_select)
        raw = market.predict(probe)
        calibrator = ProbabilityCalibrator(method)
        calibrator.fit(
            raw[: len(probe_rows)],
            np.asarray([row.label for row in probe_rows], dtype=int),
            event_ids(probe_rows),
            test_ids,
            _weights(probe_rows),
        )
        chosen_probs = calibrator.transform(raw[len(probe_rows) : len(probe_rows) + len(probe_select)])
        trials.append(
            {
                "family": "market",
                "method": calibrator.method,
                "requested": method,
                "log_loss": round(_match_loss(probe_select, chosen_probs), 6),
            }
        )
    finish("market", market, market_loss, lambda: market.predict(ordered_val), lambda: market.predict(test))

    rating = RatingCandidate()
    markov = MarkovCandidate()
    rating_loss = _match_loss(validation, rating.predict(validation))
    markov_loss = _match_loss(validation, markov.predict(validation))
    trials.append({"family": "rating_markov", "name": rating.name, "log_loss": round(rating_loss, 6)})
    trials.append({"family": "rating_markov", "name": markov.name, "log_loss": round(markov_loss, 6)})
    prior = rating if rating_loss <= markov_loss else markov
    finish("rating_markov", prior, min(rating_loss, markov_loss), lambda: prior.predict(ordered_val), lambda: prior.predict(test))

    logistic = LogisticCandidate()
    logistic.select(train, validation)
    trials.extend({"family": "linear", **item} for item in logistic.trials)
    finish("linear", logistic, _match_loss(validation, logistic.predict(validation)), lambda: logistic.predict(ordered_val), lambda: logistic.predict(test))

    forest = TreeBaggingCandidate()
    forest.select(train, validation)
    trials.extend({"family": "bagging", **item} for item in forest.trials)
    finish("bagging", forest, _match_loss(validation, forest.predict(validation)), lambda: forest.predict(ordered_val), lambda: forest.predict(test))

    boosting = HistGradientCandidate()
    boosting.select(train, validation)
    trials.extend({"family": "boosting", **item} for item in boosting.trials)
    finish(
        "boosting",
        boosting,
        _match_loss(validation, boosting.predict(validation)),
        lambda: boosting.predict(ordered_val),
        lambda: boosting.predict(test),
    )

    catboost = CatBoostCandidate()
    if catboost.available:
        catboost.select(train, validation)
        cat_loss = _match_loss(validation, catboost.predict(validation))
        trials.append({"family": "catboost", "name": catboost.name, "log_loss": round(cat_loss, 6)})
        finish("catboost", catboost, cat_loss, lambda: catboost.predict(ordered_val), lambda: catboost.predict(test))
    else:
        trials.append({"family": "catboost", "status": "unavailable"})

    residual_best = None
    residual_loss = None
    markov_train = markov_probabilities(train)
    markov_validation = markov_probabilities(validation)
    markov_ordered = markov_probabilities(ordered_val)
    markov_test = markov_probabilities(test)
    for family in ("elo", "score", "full"):
        residual = ResidualAblation(family)
        residual.fit(train, markov_train)
        loss = _match_loss(validation, residual.predict(validation, markov_validation))
        trials.append(
            {
                "family": "residual",
                "name": residual.name,
                "log_loss": round(loss, 6),
                "insufficient": bool(residual.insufficient),
            }
        )
        if residual_loss is None or loss < residual_loss:
            residual_best = residual
            residual_loss = loss
    finish(
        "residual",
        residual_best,
        residual_loss,
        lambda: residual_best.predict(ordered_val, markov_ordered),
        lambda: residual_best.predict(test, markov_test),
    )

    def finish_network(family: str, candidate):
        logger.info("Fitting %s", family)
        candidate.forbidden = test_ids
        candidate.select(train, validation)
        trials.extend({"family": family, **item} for item in candidate.trials)
        bundle = candidate.bundle or {}
        candidate.chosen = {
            **(candidate.chosen or {}),
            "device": bundle.get("device_name"),
            "device_note": bundle.get("device_note"),
            "parameters": bundle.get("parameters"),
            "epochs": bundle.get("epochs"),
        }
        finish(
            family,
            candidate,
            _match_loss(validation, candidate.predict(validation)),
            lambda: candidate.predict(ordered_val),
            lambda: candidate.predict(test),
        )
        test_probs = np.asarray(candidate.predict(test), dtype=float)
        records = _probability_records(test, test_probs)
        reports[-1]["error_slices"] = sliced_metrics(records)
        reports[-1]["error_analysis"] = error_summary(records)

    finish_network("mlp", MLPCandidate())
    finish_network("residual_mlp", ResidualMLPCandidate())
    finish_network("gru", GRUCandidate())

    payload = {
        "phase": phase,
        "kind": "hypothesis_holdout",
        "test_matches_requested": test_matches,
        "test_fingerprint": test_fingerprint(test),
        "test_event_ids": event_ids(test),
        "train_matches": len(event_ids(train)),
        "validation_matches": len(event_ids(validation)),
        "test_matches": len(event_ids(test)),
        "selection_split": "validation_only",
        "validation_trials": _jsonable(trials),
        "candidates": _jsonable(reports),
    }
    best = min(reports, key=lambda item: item["metrics"]["log_loss"])
    bets = best["bets"]
    session.add(
        ExperimentResult(
            name=f"tennis_hypothesis_holdout_{phase}",
            strategy_config=payload,
            backtest_pnl=float(bets["roi"] * max(bets["bets"], 1)),
            backtest_roi=float(bets["roi"]),
            win_rate=float(bets["win_rate"]),
            max_drawdown=float(bets["max_drawdown"]),
        )
    )
    session.commit()
    return payload
