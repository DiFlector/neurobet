"""Read-only strategy replay for persisted odds-free tennis forecasts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional

from .policy import (
    TennisPolicy, load_tennis_policy, market_limits, set_is_observation_only,
    set_is_stake_allowed, windows_allow,
)

LIVE_FRESHNESS_SECONDS = 90
PREMATCH_HORIZON_HOURS = 6


def odds_free_event_filters(event_model: Any, now: datetime) -> tuple:
    """Shared current-event scope for odds-free forecasts and their replay."""
    from sqlalchemy import and_, or_

    live = and_(
        event_model.status == "live",
        event_model.last_seen_at >= now - timedelta(seconds=LIVE_FRESHNESS_SECONDS),
    )
    prematch = and_(
        event_model.status == "prematch",
        event_model.is_live.is_(False),
        event_model.scheduled_start_at >= now,
        event_model.scheduled_start_at <= now + timedelta(hours=PREMATCH_HORIZON_HOURS),
    )
    return (
        event_model.sport_code == "tennis",
        event_model.source == "fonbet",
        event_model.finished_at.is_(None),
        ~event_model.participant_a_name.contains("/"),
        ~event_model.participant_b_name.contains("/"),
        or_(live, prematch),
    )


def _utc(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _price_pair(
    rows: Iterable[Mapping[str, Any]], event_id: str, market: str, line: Optional[int], cutoff: datetime,
    max_age_seconds: int,
) -> tuple[Optional[dict], Optional[str]]:
    latest: dict[str, tuple[datetime, float]] = {}
    for row in rows:
        if str(row.get("event_id") or "") != event_id:
            continue
        if str(row.get("market_type") or "") != market:
            continue
        try:
            row_line = int(float(row.get("line_value") or 0)) if row.get("line_value") is not None else None
        except (TypeError, ValueError):
            continue
        if row_line != line:
            continue
        status = str(row.get("status") or "active").lower()
        if status not in {"active", "open", ""}:
            continue
        observed = _utc(row.get("observed_at"))
        if observed is None or observed > cutoff:
            continue
        outcome = str(row.get("outcome") or "").lower()
        side = "a" if outcome in {"a", "player_a", "home", "1"} else "b" if outcome in {"b", "player_b", "away", "2"} else None
        if side is None:
            continue
        try:
            odds = float(row.get("odds"))
        except (TypeError, ValueError):
            continue
        if odds <= 1.0:
            continue
        if side not in latest or observed > latest[side][0]:
            latest[side] = (observed, odds)
    if len(latest) != 2:
        return None, "TWO_SIDED_PRICE_MISSING"
    oldest = min(latest["a"][0], latest["b"][0])
    if (cutoff - oldest).total_seconds() > max_age_seconds:
        return None, "PRICE_STALE"
    return {
        "a": latest["a"][1], "b": latest["b"][1],
        "observed_at": max(latest["a"][0], latest["b"][0]).isoformat(),
    }, None


def replay_odds_free_forecasts(
    forecasts: Iterable[Mapping[str, Any]],
    odds_snapshots: Iterable[Mapping[str, Any]],
    *,
    policy: Optional[TennisPolicy] = None,
    confirm_positive_ev: bool = False,
    max_odds_age_seconds: Optional[int] = None,
) -> dict:
    """Replay configured market gates without writing bets or bankroll entries.

    Forecast JSON remains odds-free. Odds are joined only after the forecast's
    calculation time and are used solely to evaluate a paper signal.
    """
    selected_policy = policy or load_tennis_policy()
    max_age = int(max_odds_age_seconds or selected_policy.max_context_age_seconds)
    odds = list(odds_snapshots)
    ordered = sorted(
        forecasts,
        key=lambda row: (_utc(row.get("calculated_at") or row.get("observed_at")) or datetime.min.replace(tzinfo=timezone.utc),
                         str(row.get("forecast_id") or "")),
    )
    counts: Counter[str] = Counter()
    watches: dict[tuple, dict] = {}
    completed: set[tuple] = set()
    signals: list[dict] = []

    def reset_model(model_key: tuple[str, str, str]) -> None:
        for watch_key in list(watches):
            if watch_key[:3] == model_key:
                watches.pop(watch_key, None)

    for row in ordered:
        model_key = (str(row.get("event_id") or ""), str(row.get("model_name") or ""),
                     str(row.get("model_version") or ""))
        if row.get("forecast_available") is False:
            reset_model(model_key)
            counts["FORECAST_UNAVAILABLE"] += 1
            continue
        as_of = _utc(row.get("calculated_at") or row.get("observed_at"))
        if as_of is None:
            reset_model(model_key)
            counts["FORECAST_TIME_MISSING"] += 1
            continue
        probabilities = row.get("probabilities") if isinstance(row.get("probabilities"), Mapping) else {}
        state = row.get("state") if isinstance(row.get("state"), Mapping) else {}
        try:
            set_number = int(state.get("sets_a", 0)) + int(state.get("sets_b", 0)) + 1
        except (TypeError, ValueError):
            set_number = 0
        data_quality = float(row.get("data_quality") or 0.0) * 100.0
        if not model_key[0]:
            counts["EVENT_ID_MISSING"] += 1
            continue
        markets = [("match_winner", None, probabilities.get("match_a"))]
        if set_number > 0:
            markets.append((f"set_winner:{set_number}", set_number, probabilities.get("current_set_a")))

        for market, line, raw_probability_a in markets:
            key = (*model_key, market)
            if key in completed:
                continue
            if line is not None:
                if set_is_observation_only(market, "tennis", selected_policy.observe_set):
                    watches.pop(key, None)
                    counts["SET_OBSERVATION_ONLY"] += 1
                    continue
                if not set_is_stake_allowed(market, "tennis", selected_policy):
                    watches.pop(key, None)
                    counts["SET_NOT_CONFIGURED_FOR_STAKING"] += 1
                    continue
            try:
                probability_a = float(raw_probability_a)
            except (TypeError, ValueError):
                watches.pop(key, None)
                counts["MARKET_PROBABILITY_MISSING"] += 1
                continue
            if data_quality < selected_policy.min_data_quality:
                watches.pop(key, None)
                counts["DATA_QUALITY_TOO_LOW"] += 1
                continue
            pair, price_error = _price_pair(
                odds, model_key[0], "match_winner" if line is None else "set_winner",
                line, as_of, max_age,
            )
            if pair is None:
                watches.pop(key, None)
                counts[price_error or "TWO_SIDED_PRICE_MISSING"] += 1
                continue
            limits = market_limits(market, selected_policy)
            candidates = []
            for side, probability, price in (
                ("player_a", probability_a, pair["a"]),
                ("player_b", 1.0 - probability_a, pair["b"]),
            ):
                allowed, reason = windows_allow(probability, price, market, selected_policy)
                edge = probability - (1.0 / price)
                expected_value = probability * price - 1.0
                if allowed and edge < limits["min_edge"]:
                    allowed, reason = False, "LOW_EDGE"
                if allowed and limits["max_edge"] is not None and edge > limits["max_edge"]:
                    allowed, reason = False, "EDGE_TOO_HIGH"
                if confirm_positive_ev and expected_value <= 0.0:
                    allowed, reason = False, "NON_POSITIVE_EV"
                counts[reason or "POLICY_ELIGIBLE"] += 1
                if allowed:
                    candidates.append({"side": side, "probability": probability, "odds": price,
                                       "edge": edge, "expected_value": expected_value})
            if not candidates:
                watches.pop(key, None)
                continue
            candidate = max(candidates, key=lambda item: (item["edge"], item["probability"]))
            prior = watches.get(key)
            if prior and as_of <= prior["last_at"]:
                counts["DUPLICATE_OR_OUT_OF_ORDER_FORECAST"] += 1
                continue
            count = int(prior["count"]) + 1 if prior and prior["side"] == candidate["side"] else 1
            watches[key] = {"side": candidate["side"], "count": count, "last_at": as_of}
            counts["CONFIRMATION_SLICES"] += 1
            if count < limits["confirmations"]:
                continue
            signal_key = (key, candidate["side"])
            if signal_key in completed:
                continue
            completed.add(key)
            signals.append({
                "event_id": model_key[0], "model_name": model_key[1], "model_version": model_key[2],
                "market": market, "outcome": candidate["side"],
                "probability": round(candidate["probability"], 6), "odds": candidate["odds"],
                "edge": round(candidate["edge"], 6), "expected_value": round(candidate["expected_value"], 6),
                "confirmations": count, "required_confirmations": limits["confirmations"],
                "forecast_as_of": as_of.isoformat(), "odds_as_of": pair["observed_at"],
                "data_quality": round(data_quality, 2),
                "confirmation_mode": "positive_ev" if confirm_positive_ev else "policy",
            })
            counts["SIGNALS"] += 1

    policy_payload = selected_policy.as_dict()
    policy_digest = hashlib.sha256(json.dumps(
        policy_payload, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "schema_version": "1.0.0",
        "mode": "read_only_candidate_replay",
        "confirm_positive_ev": bool(confirm_positive_ev),
        "policy": policy_payload,
        "policy_digest": policy_digest,
        "forecast_count": len(ordered),
        "signal_count": len(signals),
        "counts": dict(sorted(counts.items())),
        "signals": signals,
        "writes_bets": False,
        "writes_bankroll": False,
    }
