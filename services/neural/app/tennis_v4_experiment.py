"""Frozen, match-grouped tennis v4 quality and policy experiment.

Run in neural with ``python -m app.tennis_v4_experiment``. Experiment artifacts
are written outside the model registry; no model status is changed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sqlalchemy import select

from db.connection import SessionLocal
from db.models import Event
from features.selection import SELECTED_TENNIS_FEATURES_V4
from tennis_adapter.policy import load_tennis_policy, market_limits

from .dataset import MatchSnapshots, project_market, stack_matches, thin_match
from .dataset_cache import read_store
from .models import TennisMarketOffsetEstimator

logger = logging.getLogger("neural.tennis_v4_experiment")
DIAGNOSTIC_RUN = "baa22233-e876-4c2f-ba42-b4fdbd1d0714"
MARKET_INDEX = SELECTED_TENNIS_FEATURES_V4.index("no_vig_prob_player_a")
SET_INDEX = SELECTED_TENNIS_FEATURES_V4.index("market_is_set")
CURRENT_SET_INDEX = SELECTED_TENNIS_FEATURES_V4.index("current_set")


def _canonical(matches, names, result_times):
    missing = [name for name in SELECTED_TENNIS_FEATURES_V4 if name not in names]
    if missing:
        raise RuntimeError("Dataset missing v4 feature columns: " + ", ".join(missing))
    indices = [names.index(name) for name in SELECTED_TENNIS_FEATURES_V4]
    output = []
    for match in matches:
        rows = np.asarray(match.rows, dtype=float)
        output.append(MatchSnapshots(
            event_id=match.event_id,
            started_at=match.started_at,
            label=match.label,
            rows=rows[:, indices] if len(rows) else np.empty((0, len(indices))),
            observed_at=match.observed_at,
            row_labels=match.row_labels,
            row_markets=match.row_markets,
            row_odds_a=match.row_odds_a,
            row_odds_b=match.row_odds_b,
            result_available_at=result_times.get(match.event_id),
        ))
    return output


def _match_metrics(labels: Sequence[int], probabilities: Sequence[float], event_ids: Sequence[str]) -> dict:
    grouped: Dict[str, list] = defaultdict(list)
    for label, probability, event_id in zip(labels, probabilities, event_ids):
        grouped[str(event_id)].append((int(label), float(probability)))
    per_match = []
    flat_y, flat_p, flat_w = [], [], []
    for event_id, rows in grouped.items():
        y = int(rows[0][0])
        ps = np.clip([item[1] for item in rows], 1e-6, 1 - 1e-6)
        per_match.append((event_id, y, float(np.mean(-(y * np.log(ps) + (1-y) * np.log(1-ps)))), float(np.mean((ps-y) ** 2))))
        flat_y.extend([item[0] for item in rows])
        flat_p.extend(ps.tolist())
        flat_w.extend([1.0 / len(rows)] * len(rows))
    if not per_match:
        return {"status": "insufficient", "matches": 0}
    y = [item[1] for item in per_match]
    p = [float(np.mean([r[1] for r in grouped[item[0]]])) for item in per_match]
    if len(set(y)) < 2:
        auc = None
    else:
        auc = float(roc_auc_score(flat_y, flat_p, sample_weight=flat_w))
    ece = _ece(flat_y, flat_p, flat_w)
    return {
        "status": "scored",
        "matches": len(per_match),
        "snapshots": len(flat_y),
        "log_loss": float(np.mean([item[2] for item in per_match])),
        "brier": float(np.mean([item[3] for item in per_match])),
        "auc": auc,
        "ece": ece,
        "log_loss_ci95": _bootstrap_interval(per_match, 2),
        "brier_ci95": _bootstrap_interval(per_match, 3),
    }


def _ece(y, p, w, bins: int = 10) -> float:
    yy, pp, ww = np.asarray(y), np.asarray(p), np.asarray(w)
    total = float(ww.sum())
    if not len(yy) or total <= 0:
        return 0.0
    value = 0.0
    for lo in np.linspace(0, 1, bins + 1)[:-1]:
        mask = (pp >= lo) & (pp < lo + 1 / bins if lo < 1 - 1e-12 else pp <= 1)
        if np.any(mask):
            weight = float(ww[mask].sum())
            value += weight / total * abs(float(np.average(pp[mask], weights=ww[mask])) - float(np.average(yy[mask], weights=ww[mask])))
    return value


def _bootstrap_interval(rows, column: int, iterations: int = 1000):
    if len(rows) < 20:
        return None
    rng = np.random.default_rng(42)
    values = np.asarray([row[column] for row in rows], dtype=float)
    draws = rng.choice(values, size=(iterations, len(values)), replace=True).mean(axis=1)
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def _model_probs(model, match: MatchSnapshots, family: str) -> dict:
    projected = project_market(match, family)
    if len(projected.rows) == 0:
        return {}
    X = np.asarray(projected.rows, dtype=float)
    p = model.predict_proba(X)[:, 1]
    return {i: float(value) for i, value in zip(
        [j for j, market in enumerate(match.row_markets or []) if (market == "match_winner" if family == "match" else str(market).startswith("set_winner:"))], p
    )}


def _raw_market_prob(match: MatchSnapshots, indexes: Iterable[int]) -> dict:
    return {index: float(match.rows[index][MARKET_INDEX]) for index in indexes}


def _family_indexes(match: MatchSnapshots, family: str) -> list:
    if not match.row_markets:
        return list(range(len(match.rows))) if family == "match" else []
    return [i for i, market in enumerate(match.row_markets) if (market == "match_winner" if family == "match" else str(market).startswith("set_winner:"))]


def _quality_rows(matches, probs_by_match, family):
    labels, probs, ids = [], [], []
    for match in matches:
        for index in _family_indexes(match, family):
            if index in probs_by_match.get(match.event_id, {}):
                labels.append(match.row_labels[index] if match.row_labels else match.label)
                probs.append(probs_by_match[match.event_id][index])
                ids.append(match.event_id)
    return _match_metrics(labels, probs, ids)


def _qualifying_side(probability, odds_a, odds_b, market, limits):
    side = "a" if probability >= 0.5 else "b"
    selected_p = probability if side == "a" else 1 - probability
    selected_odds = odds_a if side == "a" else odds_b
    if selected_odds <= 1:
        return None
    edge = selected_p * selected_odds - 1
    if selected_p < limits["min_probability"] or edge < limits["min_edge"]:
        return None
    if limits["mode"] == "range" and not (limits["min_odds"] <= selected_odds < limits["max_odds"]):
        return None
    return side, selected_p, selected_odds, edge


def _first_decision(match, probs, family, policy):
    allowed_set = str(policy.stake_sets).replace(",", " ").split()
    required = int(policy.match_confirmations if family == "match" else policy.set_confirmations)
    market_name = "match_winner" if family == "match" else "set_winner"
    limits = market_limits(market_name, policy)
    history = []
    side, streak = None, 0
    for index in _family_indexes(match, family):
        market = (match.row_markets or ["match_winner"] * len(match.rows))[index]
        current_set = int(float(match.rows[index][CURRENT_SET_INDEX] or 1))
        if family == "set":
            try:
                set_no = int(str(market).split(":")[-1])
            except ValueError:
                continue
            if set_no not in {int(item) for item in allowed_set} or set_no == policy.observe_set or current_set < set_no:
                continue
        elif current_set < 2:
            continue
        odds_a = float(match.row_odds_a[index]) if match.row_odds_a is not None else 0.0
        odds_b = float(match.row_odds_b[index]) if match.row_odds_b is not None else 0.0
        p = probs[match.event_id][index]
        choice = _qualifying_side(p, odds_a, odds_b, market_name, limits)
        if choice is None:
            side, streak = None, 0
            continue
        chosen, selected_p, selected_odds, edge = choice
        if chosen == side:
            streak += 1
        else:
            side, streak = chosen, 1
        history.append((index, chosen, selected_p, selected_odds, edge))
        if streak >= required:
            index, chosen, selected_p, selected_odds, edge = history[-1]
            # Use the first same-side snapshot at least two seconds after decision
            # as the latency quote, when the tape has one; otherwise record no fill.
            decision_at = match.observed_at[index]
            target_market = (match.row_markets or ["match_winner"] * len(match.rows))[index]
            later = [j for j in _family_indexes(match, family)
                     if (match.row_markets or ["match_winner"] * len(match.rows))[j] == target_market
                     and match.observed_at[j] >= decision_at + timedelta(seconds=2)]
            fill_index = later[0] if later else None
            if fill_index is None or (match.observed_at[fill_index] - decision_at).total_seconds() > 15:
                return None
            fill_odds = float(match.row_odds_a[fill_index] if chosen == "a" else match.row_odds_b[fill_index])
            effective = selected_p * fill_odds - 1 if fill_odds > 1 else -1
            if effective < limits["min_edge"]:
                return None
            return {"event_id": match.event_id, "family": family, "side": chosen, "label": match.row_labels[index] if match.row_labels else match.label,
                    "probability": selected_p, "odds": fill_odds, "edge": effective, "decision_at": decision_at.isoformat(),
                    "profit": (fill_odds - 1) * 100 if (chosen == "a") == (int(match.row_labels[index] if match.row_labels else match.label) == 1) else -100}
    return None


def _combine_market_probabilities(probabilities):
    combined = {}
    for family in ("match", "set"):
        for event_id, rows in probabilities[family].items():
            combined.setdefault(event_id, {}).update(rows)
    return combined


def _decisions(matches, probabilities, mode, policy):
    bets = []
    for match in matches:
        match_pick = _first_decision(match, probabilities, "match", policy)
        set_pick = _first_decision(match, probabilities, "set", policy)
        if set_pick:
            bets.append(set_pick)
        if match_pick:
            if mode == "independent":
                bets.append(match_pick)
            elif set_pick and match_pick["side"] == set_pick["side"]:
                bets.append(match_pick)
    bets.sort(key=lambda item: (item["decision_at"], item["event_id"], item["family"]))
    return bets


def _decision_metrics(bets, total_matches, event_ids=None):
    if not bets:
        return {"status": "no_decisions", "bets": 0, "unique_matches": 0, "win_rate": None, "roi": None,
                "max_drawdown": 0.0, "coverage_ci95": None, "win_rate_ci95": None, "roi_ci95": None}
    wins = sum(1 for item in bets if item["profit"] > 0)
    turnover = len(bets) * 100
    balance = peak = 5000.0
    max_dd = 0.0
    for item in bets:
        balance += item["profit"]
        peak = max(peak, balance)
        max_dd = max(max_dd, peak - balance)
    intervals = _bootstrap_decision_intervals(bets, event_ids or sorted({item["event_id"] for item in bets}))
    return {"status": "scored", "bets": len(bets), "unique_matches": len({item["event_id"] for item in bets}),
            "coverage": len({item["event_id"] for item in bets}) / max(total_matches, 1), "wins": wins, "losses": len(bets)-wins,
            "win_rate": wins / len(bets), "turnover": turnover, "roi": sum(item["profit"] for item in bets) / turnover,
            "max_drawdown": max_dd, "side_a": sum(item["side"] == "a" for item in bets), "side_b": sum(item["side"] == "b" for item in bets),
            **intervals}


def _decision_funnel(matches, probabilities, family, policy):
    """Count unique-match and snapshot stages separately for one market head."""
    market_name = "match_winner" if family == "match" else "set_winner"
    limits = market_limits(market_name, policy)
    required = int(policy.match_confirmations if family == "match" else policy.set_confirmations)
    allowed_set = {int(item) for item in str(policy.stake_sets).replace(",", " ").split()}
    data_matches, candidate_matches, confirmed_matches = set(), set(), set()
    candidate_rows = confirmations = 0
    for match in matches:
        indexes = _family_indexes(match, family)
        if not indexes:
            continue
        data_matches.add(match.event_id)
        previous_side, streak = None, 0
        for index in indexes:
            market = (match.row_markets or ["match_winner"] * len(match.rows))[index]
            current_set = int(float(match.rows[index][CURRENT_SET_INDEX] or 1))
            if family == "set":
                try:
                    set_no = int(str(market).split(":")[-1])
                except ValueError:
                    continue
                if set_no not in allowed_set or set_no == policy.observe_set or current_set < set_no:
                    continue
            elif current_set < 2:
                continue
            odds_a = float(match.row_odds_a[index]) if match.row_odds_a is not None else 0.0
            odds_b = float(match.row_odds_b[index]) if match.row_odds_b is not None else 0.0
            probability = probabilities[match.event_id][index]
            choice = _qualifying_side(probability, odds_a, odds_b, market_name, limits)
            if choice is None:
                previous_side, streak = None, 0
                continue
            side = choice[0]
            candidate_rows += 1
            candidate_matches.add(match.event_id)
            streak = streak + 1 if side == previous_side else 1
            previous_side = side
            if streak == required:
                confirmations += 1
                confirmed_matches.add(match.event_id)
    executed = [_first_decision(match, probabilities, family, policy) for match in matches]
    executed = [item for item in executed if item]
    return {
        "available_matches": len(matches),
        "matches_with_market_data": len(data_matches),
        "candidate_rows": candidate_rows,
        "candidate_matches": len(candidate_matches),
        "confirmation_events": confirmations,
        "confirmed_matches": len(confirmed_matches),
        "executed_selections": len(executed),
        "executed_matches": len({item["event_id"] for item in executed}),
    }


def _bootstrap_roi(rows, iterations=1000):
    grouped: Dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["event_id"]].append(float(row["profit"]))
    if len(grouped) < 20:
        return None
    values = np.asarray([sum(value) for value in grouped.values()], dtype=float)
    counts = np.asarray([len(value) for value in grouped.values()], dtype=float)
    rng = np.random.default_rng(42)
    indexes = rng.integers(0, len(values), size=(iterations, len(values)))
    profits = values[indexes].sum(axis=1)
    turnover = counts[indexes].sum(axis=1) * 100
    roi = profits / np.maximum(turnover, 1)
    return [float(np.quantile(roi, .025)), float(np.quantile(roi, .975))]


def _bootstrap_decision_intervals(bets, event_ids, iterations=1000):
    grouped: Dict[str, list] = defaultdict(list)
    for bet in bets:
        grouped[str(bet["event_id"])].append(bet)
    ids = list(dict.fromkeys(str(item) for item in event_ids))
    if len(ids) < 20:
        return {"coverage_ci95": None, "win_rate_ci95": None, "roi_ci95": None}
    rng = np.random.default_rng(42)
    values = {key: (len(rows), sum(float(row["profit"]) for row in rows),
                    sum(float(row["profit"]) > 0 for row in rows)) for key, rows in grouped.items()}
    coverage, win_rate, roi = [], [], []
    for _ in range(iterations):
        sample = rng.choice(ids, size=len(ids), replace=True)
        counts = np.asarray([values.get(str(key), (0, 0.0, 0)) for key in sample], dtype=float)
        bet_count, profit, wins = counts.sum(axis=0)
        coverage.append(float(np.count_nonzero(counts[:, 0]) / len(ids)))
        win_rate.append(float(wins / bet_count) if bet_count else np.nan)
        roi.append(float(profit / (100.0 * bet_count)) if bet_count else np.nan)
    def interval(items):
        finite = np.asarray([item for item in items if np.isfinite(item)], dtype=float)
        return [float(np.quantile(finite, .025)), float(np.quantile(finite, .975))] if len(finite) else None
    return {"coverage_ci95": interval(coverage), "win_rate_ci95": interval(win_rate), "roi_ci95": interval(roi)}


def _score_model(model, matches):
    output = {"match": {}, "set": {}}
    for match in matches:
        for family in output:
            indexes = _family_indexes(match, family)
            if not indexes:
                output[family][match.event_id] = {}
                continue
            projected = np.asarray([match.rows[index] for index in indexes], dtype=float)
            output[family][match.event_id] = dict(zip(indexes, model.predict_proba(projected)[:, 1].astype(float)))
    return output


def _market_baseline(matches):
    return {family: {m.event_id: _raw_market_prob(m, _family_indexes(m, family)) for m in matches} for family in ("match", "set")}


def _prepare_data(session):
    from .dataset import get_dataset_builder
    builder = get_dataset_builder("tennis")
    matches, names = builder.load_matches(session)
    ids = [item.event_id for item in matches]
    event_rows = session.execute(select(Event.id, Event.finished_at).where(Event.id.in_(ids))).all()
    result_times = {str(event_id): finished_at for event_id, finished_at in event_rows if finished_at is not None}
    canonical = _canonical(matches, names, result_times)
    # Reconstruct the reviewed replay population: latest 100 complete numeric
    # Fonbet events, of which 86 had usable tapes in the recorded run.
    events = list(session.execute(select(Event.id).where(Event.sport_code == "tennis", Event.status.in_(["finished", "completed"]), Event.source_event_id.op("~")(r"^[0-9]+$"))
                                  .where(Event.finished_at <= datetime.fromisoformat("2026-10-19T19:40:10+00:00"))
                                  .order_by(Event.scheduled_start_at.desc().nullslast(), Event.id.desc()).limit(100)).scalars())
    diagnostic = {str(item) for item in events} & {item.event_id for item in canonical}
    valid = [item for item in canonical if item.result_available_at is not None and item.event_id not in diagnostic]
    missing_time = sum(1 for item in canonical if item.result_available_at is None)
    valid.sort(key=lambda item: (item.result_available_at, item.event_id))
    return valid, diagnostic, missing_time


def run_experiment(output_path: str):
    from features.selection import SELECTED_TENNIS_FEATURES_V4
    with SessionLocal() as session:
        matches, diagnostic_ids, missing_time = _prepare_data(session)
    if len(matches) < 120:
        raise RuntimeError(f"Strict chronological experiment requires at least 120 eligible non-diagnostic matches; found {len(matches)}.")
    lock_count = max(1, int(round(len(matches) * .2)))
    development, final_test = matches[:-lock_count], matches[-lock_count:]
    warmup = max(20, int(len(development) * .4))
    remaining = development[warmup:]
    blocks = [block.tolist() for block in np.array_split(np.asarray(remaining, dtype=object), 4) if len(block)]
    if len(blocks) != 4:
        raise RuntimeError("Could not form four chronological walk-forward windows.")
    candidates = [(2, 10.0), (2, 30.0), (3, 10.0), (3, 30.0)]
    wf_reports = {
        f"depth{depth}_l2{int(reg)}": {
            "match": [], "set": [],
            "bets": {"set_confirmed": [], "independent": []},
            "bets_by_market": {mode: {family: [] for family in ("match", "set")}
                               for mode in ("set_confirmed", "independent")},
        }
        for depth, reg in candidates
    }
    policy = load_tennis_policy()
    current_id = 0
    for window, block in enumerate(blocks):
        train_end = warmup + sum(len(item) for item in blocks[:window])
        train = matches[:train_end]
        cutoff = min(item.result_available_at for item in block)
        train = [item for item in train if item.result_available_at < cutoff]
        fold_test = block
        for depth, reg in candidates:
            name = f"depth{depth}_l2{int(reg)}"
            estimator = TennisMarketOffsetEstimator(max_depth=depth, reg_lambda=reg)
            fit = [thin_match(item, 12) for item in train]
            estimator.fit_matches(fit, SELECTED_TENNIS_FEATURES_V4)
            scored = _score_model(estimator, fold_test)
            for family in ("match", "set"):
                wf_reports[name][family].append(_quality_rows(fold_test, scored[family], family))
            for mode in ("set_confirmed", "independent"):
                all_probs = _combine_market_probabilities(scored)
                bets = _decisions(fold_test, all_probs, mode, policy)
                test_ids = [item.event_id for item in fold_test]
                wf_reports[name]["bets"][mode].append(_decision_metrics(bets, len(fold_test), test_ids))
                for family in ("match", "set"):
                    market_bets = [item for item in bets if item["family"] == family]
                    wf_reports[name]["bets_by_market"][mode][family].append(
                        _decision_metrics(market_bets, len(fold_test), test_ids)
                    )
        logger.info("walk-forward window %d/%d complete (%d train, %d test)", window+1, len(blocks), len(train), len(fold_test))

    def mean_family(name, family):
        values = [row.get("log_loss") for row in wf_reports[name][family] if row.get("log_loss") is not None]
        return float(np.mean(values)) if values else float("inf")
    selected = min(candidates, key=lambda pair: (
        np.mean([mean_family(f"depth{pair[0]}_l2{int(pair[1])}", family) for family in ("match", "set")]),
        pair[0], -pair[1]
    ))
    model_name = f"depth{selected[0]}_l2{int(selected[1])}"
    policy_options = []
    for mode in ("set_confirmed", "independent"):
        reports = wf_reports[model_name]["bets"][mode]
        scored = [item for item in reports if item.get("status") == "scored"]
        roi = float(np.mean([item["roi"] for item in scored])) if scored else -1.0
        coverage = float(np.mean([item["coverage"] for item in scored])) if scored else 0.0
        drawdown = float(np.mean([item["max_drawdown"] for item in scored])) if scored else float("inf")
        policy_options.append((mode, roi, coverage, drawdown))
    # Prefer a positive mean walk-forward ROI, then maximize unique-match
    # coverage; drawdown breaks any remaining tie. ROI magnitude is not a
    # substitute for the precommitted coverage criterion.
    chosen_mode = sorted(policy_options, key=lambda row: (-int(row[1] > 0), -row[2], row[3], row[0]))[0][0]
    policy_hash_payload = {"tennis_policy": policy.as_dict(), "match_decision_mode": chosen_mode}
    policy_sha256 = hashlib.sha256(json.dumps(policy_hash_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # Lock the model family and policy before opening the reserved final period once.
    final_estimator = TennisMarketOffsetEstimator(max_depth=selected[0], reg_lambda=selected[1])
    final_estimator.fit_matches([thin_match(item, 12) for item in development], SELECTED_TENNIS_FEATURES_V4)
    final_scores = _score_model(final_estimator, final_test)
    baseline = _market_baseline(final_test)
    current = TennisMarketOffsetEstimator(max_depth=3, reg_lambda=10.0)
    current.fit_matches([thin_match(item, 12) for item in development], SELECTED_TENNIS_FEATURES_V4)
    current_scores = _score_model(current, final_test)
    locked_metrics = {}
    for family in ("match", "set"):
        locked_metrics[family] = {
            "market": _quality_rows(final_test, baseline[family], family),
            "v4_current_algorithm": _quality_rows(final_test, current_scores[family], family),
            "selected_candidate": _quality_rows(final_test, final_scores[family], family),
        }
    final_bets = _decisions(final_test, _combine_market_probabilities(final_scores), chosen_mode, policy)
    final_ids = [item.event_id for item in final_test]
    final_decisions = _decision_metrics(final_bets, len(final_test), final_ids)
    current_bets = _decisions(final_test, _combine_market_probabilities(current_scores), chosen_mode, policy)
    current_decisions = _decision_metrics(current_bets, len(final_test), final_ids)
    decisions_by_market = {
        family: _decision_metrics([item for item in final_bets if item["family"] == family], len(final_test), final_ids)
        for family in ("match", "set")
    }
    current_decisions_by_market = {
        family: _decision_metrics([item for item in current_bets if item["family"] == family], len(final_test), final_ids)
        for family in ("match", "set")
    }
    decision_funnel = {
        family: _decision_funnel(final_test, _combine_market_probabilities(final_scores), family, policy)
        for family in ("match", "set")
    }
    improvement = all(
        locked_metrics[family]["selected_candidate"].get("log_loss", float("inf")) < locked_metrics[family]["market"].get("log_loss", -float("inf"))
        and locked_metrics[family]["selected_candidate"].get("log_loss", float("inf")) < locked_metrics[family]["v4_current_algorithm"].get("log_loss", float("inf"))
        for family in ("match", "set")
    )
    coverage_improved = final_decisions.get("coverage", 0) > current_decisions.get("coverage", 0)
    release_pass = improvement and final_decisions.get("roi", -1) > 0 and coverage_improved
    payload = {
        "kind": "tennis_v4_improvement_experiment",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"available_strict_matches": len(matches), "diagnostic_run_id": DIAGNOSTIC_RUN,
                    "diagnostic_event_ids": sorted(diagnostic_ids), "excluded_missing_result_time": missing_time,
                    "ordered_dataset_sha256": hashlib.sha256("\n".join(
                        f"{item.event_id}|{item.result_available_at.isoformat()}|{item.label}|{len(item.rows)}" for item in matches
                    ).encode()).hexdigest(),
                    "development_matches": len(development), "final_test_matches": len(final_test),
                    "final_test_event_ids": [item.event_id for item in final_test],
                    "feature_names": SELECTED_TENNIS_FEATURES_V4, "training_cap": 12, "scoring_tape": "full"},
        "walk_forward": wf_reports,
        "selected_candidate": {"max_depth": selected[0], "reg_lambda": selected[1], "alpha_by_market": final_estimator.alpha_},
        "selected_match_policy": chosen_mode,
        "policy": policy.as_dict(),
        "policy_sha256": policy_sha256,
        "policy_comparison": [{"mode": item[0], "mean_roi": item[1], "mean_coverage": item[2], "mean_max_drawdown": item[3]} for item in policy_options],
        "policy_selection_rule": "positive walk-forward ROI first; then greater unique-match coverage; then lower drawdown",
        "final_test": {"metrics_by_market": locked_metrics, "decisions": final_decisions,
                       "decisions_by_market": decisions_by_market,
                       "decision_funnel_by_market": decision_funnel,
                       "current_v4_decisions_same_policy": current_decisions,
                       "current_v4_decisions_by_market": current_decisions_by_market,
                       "coverage_exceeds_current_v4": coverage_improved,
                       "selected_candidate_beats_market_and_v4_log_loss": improvement,
                       "release_gate_passed": release_pass},
        "release_recommendation": "release tennis_v4.1.0 SHADOW" if release_pass else "defer replacement; retain current SHADOW",
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = path.parent / "tennis_v4.1.0_candidate.joblib"
    import joblib
    joblib.dump(final_estimator, candidate_path)
    payload["experimental_checkpoint"] = str(candidate_path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("Experiment saved: %s; gate=%s", path, release_pass)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/app/models/checkpoints/experiments/tennis_v4_improvement.json")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    result = run_experiment(args.output)
    print(json.dumps({"output": args.output, "eligible_matches": result["dataset"]["available_strict_matches"],
                      "selected_candidate": result["selected_candidate"], "selected_policy": result["selected_match_policy"],
                      "final_test": result["final_test"], "release_recommendation": result["release_recommendation"]}, indent=2))


if __name__ == "__main__":
    main()
