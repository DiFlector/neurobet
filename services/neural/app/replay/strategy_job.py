"""Load one decision tape and rank tennis stake gates away from the final window."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("neural.strategy_search")

REPORT_NAME = "strategy_search_report.json"
WINDOWS_NAME = "strategy_windows.json"


def checkpoint_dir() -> str:
    return os.getenv("CHECKPOINT_DIR", "/app/models/checkpoints")


def live_gate_params() -> Dict[str, Any]:
    from tennis_adapter.policy import load_tennis_policy

    policy = load_tennis_policy()
    return {
        "min_probability": policy.min_probability,
        "short_odds_lo": policy.short_odds_lo,
        "short_odds_hi": policy.short_odds_hi,
        "mid_odds_lo": policy.mid_odds_lo,
        "mid_odds_hi": policy.mid_odds_hi,
        "match_odds_hi": policy.match_odds_hi,
        "min_edge": policy.min_edge,
        "confirmations": policy.confirmations,
        "observe_set": policy.observe_set,
        "max_odds_staleness_sec": 15.0,
    }


def blocked_holdout_ids(session) -> List[str]:
    from db.models.ml_registry import ModelVersion

    row = (
        session.query(ModelVersion)
        .filter(
            ModelVersion.model_name == "tennis_logistic_regression_epoch",
            ModelVersion.version_tag == "tennis_v3.0.0",
        )
        .one_or_none()
    )
    if row is None:
        return []
    stored = (row.hyperparameters or {}).get("holdout_event_ids") or []
    return [str(item) for item in stored]


def finished_match_ids(session, sport: str = "tennis") -> List[str]:
    from db.models.events import Event
    from sqlalchemy import select

    events = list(
        session.execute(
            select(Event.id)
            .where(Event.sport_code == sport)
            .where(Event.status.in_(["finished", "completed"]))
            .where(Event.source_event_id.op("~")(r"^[0-9]+$"))
            .order_by(Event.scheduled_start_at.desc().nullslast(), Event.id.desc())
        ).scalars()
    )
    return [str(item) for item in events]


def record_match_tape(session, model_version_id: str, event_ids: List[str], params: Dict[str, Any], market: str = "match"):
    """Return the runner summary and the raw candidate tape."""
    import uuid

    from db.models.events import Event, EventStateSnapshot
    from db.models.ml_registry import ModelVersion
    from db.models.odds import OddsSnapshot
    from db.models.predictions import LLMDecision
    from features import registry as feature_registry
    from sqlalchemy import select

    from ..composite_predictor import load_predictor
    from .engine import ReplayRunner
    from .jobs import _cached_by_event, _load_cached_match, _load_match

    model = session.get(ModelVersion, uuid.UUID(str(model_version_id)))
    if model is None or model.status not in ("ACTIVE", "SHADOW"):
        raise RuntimeError("strategy search needs an ACTIVE or SHADOW checkpoint")
    sport = str(model.sport_code or "tennis")
    builder = feature_registry.get(sport)
    parsed = [uuid.UUID(item) for item in event_ids]
    found = list(
        session.execute(
            select(Event).where(Event.id.in_(parsed)).where(Event.sport_code == sport).where(Event.status.in_(["finished", "completed"]))
        ).scalars()
    )
    by_id = {str(event.id): event for event in found}
    events = [by_id[item] for item in event_ids if item in by_id]
    if len(events) != len(event_ids):
        raise RuntimeError("strategy window is missing finished matches")
    cached = _cached_by_event(sport, builder, event_ids)
    history = None
    if any(cached is None or str(event.id) not in cached for event in events) and sport == "tennis":
        from ..players import PlayerHistory

        history = PlayerHistory.load(session)
    matches = []
    for event in events:
        stored = None if cached is None else cached.get(str(event.id))
        loaded = (
            _load_match(session, event, history, EventStateSnapshot, OddsSnapshot, LLMDecision)
            if stored is None
            else _load_cached_match(session, event, stored, OddsSnapshot)
        )
        if loaded is not None:
            matches.append(loaded)
    config = _replay_config(params, market)
    predictor = load_predictor(model.artifact_path)
    runner = ReplayRunner(predictor, config, sport)
    summary = runner.run(matches, builder)
    summary["model_version_id"] = str(model.id)
    summary["model_name"] = model.model_name
    summary["version_tag"] = model.version_tag
    summary.pop("bet_records", None)
    return summary, runner.candidates, model


def _replay_config(params: Dict[str, Any], market: str = "match") -> Dict[str, Any]:
    markets = ["set_winner"] if market == "set2" else ["match_winner"]
    return {
        "markets": markets,
        "market": markets[0],
        "stake_sets": [2] if market == "set2" else None,
        "min_edge": float(params["min_edge"]),
        "min_probability": float(params["min_probability"]),
        "odds_gate_mode": "live",
        "min_odds": 1.20,
        "max_odds": float(params.get("match_odds_hi") or params.get("max_odds") or 2.40),
        "short_odds_lo": float(params.get("short_odds_lo") or 1.20),
        "short_odds_hi": float(params.get("short_odds_hi") or 1.40),
        "mid_odds_lo": float(params.get("mid_odds_lo") or 1.60),
        "mid_odds_hi": float(params.get("mid_odds_hi") or 1.80),
        "match_odds_hi": float(params.get("match_odds_hi") or params.get("max_odds") or 2.20),
        "observe_set": int(params.get("observe_set") or 1),
        "confirmations": int(params["confirmations"]),
        "max_odds_staleness_sec": float(params["max_odds_staleness_sec"]),
        "execution_latency_sec": 2.0,
        "allow_slippage": True,
        "initial_bankroll": 5000.0,
        "fixed_stake": 100.0,
        "bankruptcy_threshold": 100.0,
        "record_candidates": True,
    }


def prepare_windows(session) -> Dict[str, Any]:
    from .strategy_split import split_match_ids

    windows = split_match_ids(finished_match_ids(session), blocked_holdout_ids(session))
    if len(windows["gate_final"]) < 100 or len(windows["search"]) < 100 or len(windows["model_final"]) < 100:
        raise RuntimeError("not enough finished matches for disjoint strategy windows")
    payload = {"sport_code": "tennis", **windows}
    write_json(WINDOWS_NAME, payload)
    return payload


def _tape_for_market(candidates: List[Dict[str, Any]], market: str) -> List[Dict[str, Any]]:
    if market == "set2":
        return [row for row in candidates if str(row.get("market") or "") == "set_winner:2"]
    return [row for row in candidates if str(row.get("market") or "") == "match_winner"]


def _compact_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact = [
        {
            "params": row["params"],
            "bets": row["bets"],
            "roi": row["roi"],
            "net_pnl": row["net_pnl"],
            "win_rate": row["win_rate"],
            "max_drawdown": row["max_drawdown"],
            "bankruptcies": row["bankruptcies"],
            "is_live_baseline": row.get("is_live_baseline", False),
        }
        for row in rows
    ]
    compact.sort(key=lambda row: (-(row["roi"] if row["roi"] is not None else -999), row["max_drawdown"], -row["bets"]))
    return compact


def search_gates(session, model_version_id: str, windows: Dict[str, Any], market: str = "match") -> Dict[str, Any]:
    from .strategy_search import choose_gates, iter_gate_configs, iter_range_gate_configs, prefer_gate, simulate_gates

    baseline = live_gate_params()
    logger.info("Recording %s gate tape for %d matches", market, len(windows["search"]))
    _summary, candidates, model = record_match_tape(session, model_version_id, windows["search"], baseline, market)
    tape = _tape_for_market(candidates, market)
    live_rows = []
    for index, params in enumerate(iter_gate_configs(), start=1):
        report = simulate_gates(tape, params)
        report["is_live_baseline"] = _same_gates(params, baseline)
        live_rows.append(report)
        if index % 500 == 0:
            logger.info("Scored %d live gate combinations", index)
    range_rows = []
    for index, params in enumerate(iter_range_gate_configs(), start=1):
        range_rows.append(simulate_gates(tape, params))
        if index % 500 == 0:
            logger.info("Scored %d range gate combinations", index)
    live_winner = choose_gates(live_rows)
    range_winner = choose_gates(range_rows)
    winner = prefer_gate(range_winner, live_winner)
    live_compact = _compact_rows(live_rows)
    range_compact = _compact_rows(range_rows)
    payload = {
        "market": market,
        "model_version_id": str(model.id),
        "model_name": model.model_name,
        "version_tag": model.version_tag,
        "search_matches": len(windows["search"]),
        "candidate_rows": len(tape),
        "live_combinations": len(live_rows),
        "range_combinations": len(range_rows),
        "baseline": next(row for row in live_compact if row["is_live_baseline"]),
        "live_winner": live_winner,
        "range_winner": range_winner,
        "winner": winner,
        "live_top": live_compact[:10],
        "range_top": range_compact[:10],
    }
    write_json(f"strategy_{market}_search_report.json", payload)
    logger.info("Gate search %s winner=%s", market, json.dumps(winner["params"]) if winner else "none")
    return payload


def run_final_replay(session, model_version_id: str, windows: Dict[str, Any], params: Dict[str, Any], market: str = "match") -> Dict[str, Any]:
    """Score the chosen gates once on the untouched final hundred and compare with the live gates."""
    from .strategy_search import simulate_gates

    summary, candidates, model = record_match_tape(session, model_version_id, windows["gate_final"], live_gate_params(), market)
    tape = _tape_for_market(candidates, market)
    baseline = simulate_gates(tape, live_gate_params())
    chosen = simulate_gates(tape, params)
    payload = {
        "model_version_id": str(model.id),
        "model_name": model.model_name,
        "version_tag": model.version_tag,
        "market": market,
        "matches": len(windows["gate_final"]),
        "official_replay": {key: summary.get(key) for key in ("bets", "wins", "losses", "roi", "net_pnl", "win_rate", "max_drawdown", "bankruptcies", "final_balance")},
        "chosen": chosen,
        "baseline": baseline,
        "adopted": _better(chosen, baseline),
    }
    write_json(f"strategy_{market}_final_report.json", payload)
    return payload


def _same_gates(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    keys = (
        "min_edge",
        "min_probability",
        "confirmations",
        "max_odds_staleness_sec",
        "short_odds_lo",
        "short_odds_hi",
        "mid_odds_lo",
        "mid_odds_hi",
        "match_odds_hi",
    )
    return all(float(left[key]) == float(right[key]) for key in keys)


def _better(chosen: Dict[str, Any], baseline: Dict[str, Any]) -> bool:
    if chosen.get("roi") is None or int(chosen.get("bets") or 0) < 20 or int(chosen.get("bankruptcies") or 0) > 0:
        return False
    base_roi = -1.0 if baseline.get("roi") is None else float(baseline["roi"])
    return float(chosen["roi"]) > base_roi and float(chosen["max_drawdown"]) <= float(baseline.get("max_drawdown") or 0.0) + 500.0


def _model_id_for_market(session, market: str) -> str:
    from db.models.ml_registry import ModelVersion

    name = "tennis_gradient_boosting_epoch" if market == "set2" else "tennis_hist_gradient_roi"
    tag = "tennis_v3.0.0" if market == "set2" else "tennis_v3.1.0"
    row = (
        session.query(ModelVersion)
        .filter(ModelVersion.model_name == name, ModelVersion.version_tag == tag, ModelVersion.status == "SHADOW")
        .one()
    )
    return str(row.id)


def write_json(name: str, payload: Dict[str, Any]) -> str:
    path = os.path.join(checkpoint_dir(), name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def main() -> None:
    import argparse

    from db.connection import SessionLocal

    parser = argparse.ArgumentParser(description="Search tennis stake gates on a fixed window")
    parser.add_argument("--phase", choices=("windows", "search", "final"), default="search")
    parser.add_argument("--market", choices=("match", "set2"), default="match")
    parser.add_argument("--model-id", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with SessionLocal() as session:
        windows = prepare_windows(session)
        if args.phase == "windows":
            logger.info("windows search=%d final=%d model_final=%d", len(windows["search"]), len(windows["gate_final"]), len(windows["model_final"]))
            return
        model_id = args.model_id or _model_id_for_market(session, args.market)
        if args.phase == "search":
            search_gates(session, model_id, windows, args.market)
            return
        report_path = os.path.join(checkpoint_dir(), f"strategy_{args.market}_search_report.json")
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
        winner = report.get("winner")
        if not winner:
            raise RuntimeError("gate search has no acceptable winner")
        run_final_replay(session, model_id, windows, winner["params"], args.market)


if __name__ == "__main__":
    main()
