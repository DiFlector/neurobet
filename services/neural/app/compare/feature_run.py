"""Score production-feature candidates once on a frozen block of 100 matches."""

import json
import logging
from collections import defaultdict
from typing import Dict, Sequence

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.models import ExperimentResult

from ..backtest.execution import logit_clv
from ..commit import confirming_index, favored_side, side_odds_from_row
from ..metrics import compute_classification_metrics
from .feature_models import (
    AblationFeatureModel,
    BaggingFeatureModel,
    BoostingFeatureModel,
    CatBoostFeatureModel,
    GRUFeatureModel,
    LogisticFeatureModel,
    MLPFeatureModel,
    ResidualMLPFeatureModel,
    market_probability,
    prepare,
    stack,
)
from .feature_split import event_ids, fingerprint, split_unseen_holdout
from .report import bootstrap_delta, reliability_bins

logger = logging.getLogger("neural.compare")


def reserved_test_ids(session: Session) -> set:
    rows = session.execute(
        text(
            """
            SELECT strategy_config FROM experiment_results
            WHERE name LIKE 'tennis_hypothesis_holdout%'
               OR name LIKE 'tennis_feature103_holdout%'
            """
        )
    ).fetchall()
    found = set()
    for (config,) in rows:
        if isinstance(config, str):
            config = json.loads(config)
        if isinstance(config, dict):
            found.update(str(item) for item in (config.get("test_event_ids") or []))
    return found


def run_feature_holdout(session: Session, matches, names: Sequence[str], test_matches: int = 100) -> dict:
    """Fit every family before the frozen block and score that block once."""
    prepared = prepare(matches)
    if len(names) != len(prepared[0].rows[0]):
        raise RuntimeError(f"Ширина ленты {len(prepared[0].rows[0])} не совпала с именами {len(names)}.")
    reserved = reserved_test_ids(session)
    train, validation, test = split_unseen_holdout(prepared, reserved, test_matches=test_matches)
    logger.info(
        "Feature holdout width=%d train=%d validation=%d test=%d reserved=%d",
        len(names),
        len(train),
        len(validation),
        len(test),
        len(reserved),
    )
    specs = [
        ("market", None),
        ("linear", LogisticFeatureModel()),
        ("boosting", BoostingFeatureModel()),
        ("bagging", BaggingFeatureModel()),
        ("catboost", CatBoostFeatureModel()),
        ("ablation", AblationFeatureModel()),
        ("mlp", MLPFeatureModel()),
        ("residual_mlp", ResidualMLPFeatureModel()),
        ("gru", GRUFeatureModel()),
    ]
    fitted = []
    for family, model in specs:
        logger.info("Fitting %s", family)
        if family == "market":
            fitted.append({"family": family, "name": "market", "probs": market_probability(test, names), "trials": [], "selection": None, "calibration": {}})
            continue
        if family == "catboost" and not model.available:
            fitted.append({"family": family, "name": model.name, "skipped": True})
            continue
        model.select(train, validation, names)
        model.fit_final(train, validation, names)
        bundle = getattr(model, "bundle", None) or {}
        selection = dict(model.chosen or {})
        if bundle:
            selection.update(
                {
                    "device": bundle.get("device_name"),
                    "parameters": bundle.get("parameters"),
                    "epochs": bundle.get("epochs"),
                }
            )
        fitted.append(
            {
                "family": family,
                "name": model.name,
                "probs": model.predict(test, names),
                "trials": model.trials,
                "selection": selection,
                "calibration": getattr(model, "calibration_report_", {}),
                "model": model,
            }
        )

    by_name = {item["family"]: item["probs"] for item in fitted if "probs" in item}
    labels, markets, event_keys = _targets(test, names)
    reports = []
    for item in fitted:
        if item.get("skipped"):
            reports.append({"family": item["family"], "status": "unavailable"})
            continue
        reports.append(
            _report(
                item,
                labels,
                markets,
                event_keys,
                test,
                names,
                by_name.get("linear"),
                by_name.get("boosting"),
                by_name.get("market"),
            )
        )
    payload = {
        "kind": "feature103_holdout",
        "feature_count": len(names),
        "feature_names": list(names),
        "test_matches_requested": test_matches,
        "test_fingerprint": fingerprint(test),
        "test_event_ids": event_ids(test),
        "reserved_previous_test": sorted(reserved),
        "train_matches": len(train),
        "validation_matches": len(validation),
        "test_matches": len(test),
        "candidates": _jsonable(reports),
    }
    best = min((item for item in reports if "metrics" in item), key=lambda item: item["metrics"]["log_loss"])
    bets = best["decisions"]
    session.add(
        ExperimentResult(
            name="tennis_feature103_holdout",
            strategy_config=payload,
            backtest_pnl=float(bets["roi"] * max(bets["bets"], 1)),
            backtest_roi=float(bets["roi"]),
            win_rate=float(bets["win_rate"]),
            max_drawdown=0.0,
        )
    )
    session.commit()
    return payload


def _targets(matches, names):
    labels = []
    markets = []
    keys = []
    for match in matches:
        row_markets = match.row_markets or ["match_winner"] * len(match.rows)
        for index, row in enumerate(match.rows):
            labels.append(match.row_labels[index] if match.row_labels else match.label)
            markets.append(str(row_markets[index]))
            keys.append(f"{match.event_id}|{row_markets[index]}")
            side_odds_from_row(row, names)
    return np.asarray(labels, dtype=int), markets, keys


def _family_slice(labels, probs, markets, family: str):
    if family == "match":
        indexes = [index for index, market in enumerate(markets) if market == "match_winner"]
    else:
        indexes = [index for index, market in enumerate(markets) if market.startswith("set_winner")]
    if len(indexes) < 2 or len(set(int(labels[index]) for index in indexes)) < 2:
        return None
    picked = np.asarray(indexes)
    return compute_classification_metrics(labels[picked], np.asarray(probs)[picked])


def _paired(keys, labels, left, right) -> dict:
    grouped_left = defaultdict(list)
    grouped_right = defaultdict(list)
    for key, label, first, second in zip(keys, labels, left, right):
        first = min(1 - 1e-6, max(1e-6, float(first)))
        second = min(1 - 1e-6, max(1e-6, float(second)))
        loss_left = -(label * np.log(first) + (1 - label) * np.log(1 - first))
        loss_right = -(label * np.log(second) + (1 - label) * np.log(1 - second))
        grouped_left[key].append(loss_left)
        grouped_right[key].append(loss_right)
    mean_left = {key: float(np.mean(values)) for key, values in grouped_left.items()}
    mean_right = {key: float(np.mean(values)) for key, values in grouped_right.items()}
    return bootstrap_delta(mean_left, mean_right)


def _decisions(matches, names, probs) -> dict:
    """One confirming, stake-gated decision per market. probs follow the thinned row order."""
    profits = []
    clvs = []
    cursor = 0
    for match in matches:
        row_markets = match.row_markets or ["match_winner"] * len(match.rows)
        groups = defaultdict(list)
        for index, market in enumerate(row_markets):
            groups[str(market)].append(index)
        for market, indexes in groups.items():
            if len(indexes) < 2:
                continue
            pairs = []
            quotes = []
            for index in indexes:
                probability = float(probs[cursor + index])
                pairs.append((probability, 1.0 - probability))
                quotes.append(side_odds_from_row(match.rows[index], names))
            picked = confirming_index(pairs, quotes, market=market, sport_code="tennis")
            if picked is None:
                continue
            prob_a, prob_b = pairs[picked]
            odds_a, odds_b = quotes[picked]
            side = favored_side(prob_a, prob_b, odds_a, odds_b, market=market, sport_code="tennis")
            if side is None:
                continue
            label = match.row_labels[indexes[picked]] if match.row_labels else match.label
            won = (side == "a" and int(label) == 1) or (side == "b" and int(label) == 0)
            price = odds_a if side == "a" else odds_b
            if price <= 1.0:
                continue
            profits.append((price - 1.0) if won else -1.0)
            close_a, close_b = side_odds_from_row(match.rows[indexes[-1]], names)
            clv = logit_clv(_chosen_novig(close_a, close_b, side), _chosen_novig(odds_a, odds_b, side))
            if clv is not None:
                clvs.append(clv)
        cursor += len(match.rows)
    if not profits:
        return {"roi": 0.0, "bets": 0, "win_rate": 0.0, "mean_clv": None}
    wins = sum(1 for value in profits if value > 0)
    return {
        "roi": round(float(sum(profits) / len(profits)), 4),
        "bets": len(profits),
        "win_rate": round(wins / len(profits), 4),
        "mean_clv": round(float(np.mean(clvs)), 4) if clvs else None,
    }


def _chosen_novig(odds_a: float, odds_b: float, side: str):
    if odds_a <= 1.0 or odds_b <= 1.0:
        return None
    share = (1.0 / odds_a) / (1.0 / odds_a + 1.0 / odds_b)
    return share if side == "a" else 1.0 - share


def _report(item, labels, markets, keys, matches, names, linear, boosting, market) -> dict:
    probs = np.asarray(item["probs"], dtype=float)
    metrics = compute_classification_metrics(labels, probs)
    decisions = _decisions(matches, names, probs)
    return {
        "family": item["family"],
        "name": item["name"],
        "metrics": metrics,
        "accuracy": metrics["accuracy"],
        "reliability": reliability_bins(labels, probs),
        "by_market_family": {
            "match": _family_slice(labels, probs, markets, "match"),
            "set": _family_slice(labels, probs, markets, "set"),
        },
        "versus_logistic": None if linear is None else _paired(keys, labels, probs, linear),
        "versus_boosting": None if boosting is None else _paired(keys, labels, probs, boosting),
        "versus_market": None if market is None else _paired(keys, labels, probs, market),
        "decisions": decisions,
        "selection": item.get("selection"),
        "calibration": item.get("calibration"),
        "validation_trials": item.get("trials"),
    }


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
