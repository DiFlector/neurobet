"""Point-in-time label audit for numeric Fonbet singles tennis events."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re
import hashlib
import json
import os
import uuid
from sqlalchemy import text

from db.connection import SessionLocal
from db.models import (Event, EventStateSnapshot, ExtTennisMatch, League,
    TennisPlayerIdentityLink, TennisPlayerMetricSnapshot, TennisPlayerProfile, TennisPointDocument,
    TennisPointHistoryVersion, TennisPointMatchLink)
from db.models.external_tennis import ExtTennisServeLog
from tennis_adapter.point_history import is_authoritative_point_history_version
from tennis_adapter.normalizer import normalize_russian_player_name
from tennis_result_observation import score_signature


PIT_FEATURE_SOURCE_GROUPS = (
    "player_metrics", "serve_metrics_a", "serve_metrics_b", "event_state", "event_surface",
)


def _validate_feature_availability(as_of: datetime, source_available_at: dict,
                                  contract_defaults: set[str] | None = None) -> dict:
    """Fail closed unless a source is causal or a documented contract prior is used."""
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    contract_defaults = contract_defaults or set()
    missing, future = [], []
    for name in PIT_FEATURE_SOURCE_GROUPS:
        raw = source_available_at.get(name)
        if isinstance(raw, str):
            try:
                raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                raw = None
        if not isinstance(raw, datetime):
            if name not in contract_defaults:
                missing.append(name)
            continue
        available = raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
        if available > cutoff:
            future.append(name)
    return {"certified": not missing and not future,
            "missing_sources": missing, "future_sources": future,
            "contract_defaulted_sources": sorted(contract_defaults),
            "as_of": cutoff.isoformat()}


def _is_finished_set(a: int, b: int) -> bool:
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def _tour(text: str, metadata: dict | None = None, external_tour: str | None = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                         ("tour", "category", "circuit", "age_group", "tournament_type"))
    value = f"{text} {explicit} {external_tour or ''}".casefold()
    if any(token in value for token in ("junior", "juniors", "юниор", "юнош", "u18", "u16")):
        return "junior"
    if "challenger" in value or "челленджер" in value:
        return "challenger"
    if "itf" in value or "futures" in value or "фьючерс" in value:
        return "itf"
    if "wta" in value:
        return "wta"
    if "atp" in value:
        return "atp"
    return "unknown"


def _tour_source(text: str, metadata: dict | None = None, external_tour: str | None = None) -> str:
    """Describe which source supplied the retrospective competition category."""
    metadata = metadata if isinstance(metadata, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                         ("tour", "category", "circuit", "age_group", "tournament_type"))
    if _tour(explicit) != "unknown":
        return "fonbet_metadata"
    if external_tour and _tour("", external_tour=external_tour) != "unknown":
        return "flashscore_match_archive"
    if _tour(text) != "unknown":
        return "fonbet_competition_name"
    return "unknown"


def _external_tour_level(tour_level: str | None, gender: str | None) -> str:
    level = str(tour_level or "").strip().casefold()
    sex = str(gender or "").strip().casefold()
    if level == "atp_wta":
        return {"male": "atp", "female": "wta"}.get(sex, "unknown")
    # Flashscore's female Challenger rows represent the WTA 125 circuit.
    if level == "challenger" and sex == "female":
        return "wta"
    return level if level in {"atp", "wta", "challenger", "itf", "junior"} else "unknown"


def _discipline(event: Event, competition: str) -> str:
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in ("discipline", "event_type", "draw_type"))
    value = " ".join((event.participant_a_name, event.participant_b_name, competition)).casefold()
    combined = f"{value} {explicit.casefold()}"
    if any(x in combined for x in ("doubles", "double", "пары", "пара ")) or "/" in value:
        return "doubles"
    if any(x in explicit.casefold() for x in ("singles", "single", "одиноч")):
        return "singles"
    return "singles" if "/" not in value else "unknown"


def _official_winner(event: Event) -> tuple[int | None, str]:
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") or {}
    if event.status.casefold() != "finished" or official.get("status", "").upper() != "FINISHED":
        return None, "official_result_not_finished"
    if official.get("is_walkover") or official.get("is_retired"):
        return None, "walkover_or_retirement"
    winner = {"player_a": 0, "player_b": 1}.get(official.get("winner"))
    if winner is None:
        return None, "winner_missing_or_invalid"
    final_score = str(official.get("final_score") or "")
    match_score = re.match(r"\s*(\d+)\s*:\s*(\d+)", final_score)
    sets_text = re.search(r"\(([^)]*)\)", final_score)
    if not match_score or not sets_text:
        return None, "final_set_score_missing"
    set_scores = [(int(a), int(b)) for a, b in re.findall(r"(\d+)\s*[-:]\s*(\d+)", sets_text.group(1))]
    if not set_scores or any(not _is_finished_set(a, b) for a, b in set_scores):
        return None, "invalid_final_set_score"
    a_sets = sum(a > b for a, b in set_scores)
    b_sets = sum(b > a for a, b in set_scores)
    if (a_sets, b_sets) != (int(match_score.group(1)), int(match_score.group(2))):
        return None, "match_score_disagrees_with_sets"
    if (winner == 0) != (a_sets > b_sets):
        return None, "winner_disagrees_with_score"
    declared = metadata.get("set_winners")
    if isinstance(declared, dict):
        for index, (a, b) in enumerate(set_scores, 1):
            declared_winner = declared.get(str(index))
            expected = "player_a" if a > b else "player_b"
            if declared_winner and declared_winner != expected:
                return None, "set_winner_disagrees_with_score"
    return winner, "confirmed"


def _point_order(value) -> int | None:
    return {"0": 0, "00": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4, "ADV": 4}.get(str(value).strip().upper())


def _orient(pair, external_home_is_a: bool):
    values = list(pair or [])
    if len(values) != 2:
        return None
    return values if external_home_is_a else values[::-1]


def _snapshot_state(snapshot):
    detail = snapshot.score_detail if isinstance(snapshot.score_detail, dict) else {}
    sport = snapshot.sport_state if isinstance(snapshot.sport_state, dict) else {}
    stats = detail.get("stats") if isinstance(detail.get("stats"), dict) else sport.get("stats", {})
    game = detail.get("current_game") if isinstance(detail.get("current_game"), dict) else {}
    lines = stats.get("set_lines") if isinstance(stats, dict) else []
    lines = lines if isinstance(lines, list) else []
    live_index = next((i for i in range(len(lines) - 1, -1, -1) if isinstance(lines[i], dict) and lines[i].get("live")), None)
    period = int(snapshot.current_period) if snapshot.current_period else ((live_index + 1) if live_index is not None else None)
    line = lines[live_index] if live_index is not None else {}
    try:
        games = (int(line["games_a"]), int(line["games_b"]))
    except (KeyError, TypeError, ValueError):
        return None
    is_tiebreak = bool(game.get("is_tiebreak") or sport.get("tiebreak"))
    if is_tiebreak:
        try:
            point_a, point_b = int(game.get("points_a")), int(game.get("points_b"))
        except (TypeError, ValueError):
            return None
    else:
        point_a, point_b = _point_order(game.get("points_a")), _point_order(game.get("points_b"))
    if point_a is None or point_b is None:
        return None
    server = detail.get("server") or sport.get("server")
    server = {"player_a": 0, "player_b": 1, "a": 0, "b": 1}.get(str(server).casefold())
    return period, games, (point_a, point_b), server, is_tiebreak


def _source_point_key(item: dict, external_home_is_a: bool):
    before = item.get("state_before")
    game = item.get("game_score")
    if not isinstance(before, list) or not isinstance(game, list):
        return None
    is_tiebreak = bool(item.get("tie_break"))
    if is_tiebreak:
        try:
            point_values = [int(x) for x in before]
        except (TypeError, ValueError):
            return None
    else:
        point_values = [_point_order(x) for x in before]
    point = _orient(point_values, external_home_is_a)
    games = _orient(game, external_home_is_a)
    if point is None or games is None or None in point:
        return None
    server = item.get("server")
    if server not in (0, 1, None):
        return None
    if server is not None:
        server = server if external_home_is_a else 1 - server
    return item.get("set"), tuple(games), tuple(point), server, is_tiebreak


def _snapshot_matches_source_point(snapshot_state, signature):
    """Match the observed score exactly; unknown source fields remain wildcards.

    The source PBP can confirm the score transition while omitting its server
    marker. In that case the confirmed outcome remains a valid label. A known
    server must still agree with Fonbet's saved state.
    """
    if snapshot_state is None or signature is None or len(snapshot_state) != len(signature):
        return False
    return all(
        expected is None or observed == expected
        for observed, expected in zip(snapshot_state, signature)
    )


def _count_bucket():
    return {"events": 0, "valid_match_labels": 0, "point_examples": 0, "game_examples": 0, "set_examples": 0}


def _utc_result_time(event: Event):
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") or {}
    raw = official.get("available_at") or official.get("observed_at")
    value = None
    if isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            value = None
    # A scheduled start is not evidence of when the result became available.
    # Undated legacy results stay out of the training manifest.
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _score_token_pattern(signature: tuple[int, ...]) -> str:
    """PostgreSQL regex for exact ordered score tokens with flexible punctuation."""
    return r"^[^0-9]*" + r"[^0-9]+".join(map(str, signature)) + r"[^0-9]*$"


def _snapshot_search_lower_bound(event: Event) -> datetime | None:
    """Return the first time this Fonbet event could have produced a saved snapshot."""
    value = getattr(event, "first_seen_at", None)
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _result_score_snapshot_evidence(session, events: dict) -> tuple[dict, int]:
    """Find a persisted Fonbet state that exactly shows each final score.

    A result backfilled today must not erase an earlier system observation.
    The latest exact score observation is used as a conservative availability
    time: it is guaranteed that the result was known by then, while finding the
    first occurrence would require scanning a match's entire snapshot history.
    Scheduled time and mutable Event.finished_at are never consulted.
    """
    expected = {}
    for event_id, event in events.items():
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
        official = metadata.get("fonbet_official_result") or {}
        signature = score_signature(official.get("final_score"))
        if signature:
            expected[event_id] = signature

    evidence = {}
    # first_seen_at is a safe per-event lower bound: the event must have been
    # ingested before a snapshot referencing its ID could be saved. Search each
    # event through the event/time B-tree, newest first, and stop at its latest
    # exact score. This is a conservative availability timestamp and avoids
    # scanning/transferring the entire snapshot history for each event.
    requested = [
        (event_id, _score_token_pattern(signature), _snapshot_search_lower_bound(events[event_id]))
        for event_id, signature in expected.items()
    ]
    bounded = [item for item in requested if item[2] is not None]
    if bounded:
        requested_params = {
            "event_ids": [item[0] for item in bounded],
            "lower_bounds": [item[2] for item in bounded],
        }
        latest_rows = session.execute(text("""
            WITH requested AS (
                SELECT * FROM unnest(
                    CAST(:event_ids AS uuid[]),
                    CAST(:lower_bounds AS timestamptz[])
                ) AS r(event_id, lower_bound)
            )
            SELECT requested.event_id, snapshot.id, snapshot.observed_at,
                   snapshot.status, snapshot.score
            FROM requested
            JOIN LATERAL (
                SELECT id, observed_at, status, score
                FROM event_state_snapshots
                WHERE event_id = requested.event_id
                  AND observed_at >= requested.lower_bound
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
            ) AS snapshot ON TRUE
        """), requested_params).yield_per(10000)
        patterns = {event_id: pattern for event_id, pattern, _ in bounded}
        for row in latest_rows:
            if row.score is None or re.fullmatch(patterns[row.event_id], str(row.score)) is None:
                continue
            evidence[row.event_id] = {
                "snapshot_id": str(row.id), "observed_at": row.observed_at,
                "status": row.status, "score": row.score,
            }

        # Most finished matches have the exact official score in their latest
        # persisted snapshot. Only mismatches need the slower historical search;
        # this retains corrections/older matching evidence without scanning every
        # event's full tape in the common case.
        unresolved = [item for item in bounded if item[0] not in evidence]
        if unresolved:
            historical_rows = session.execute(text("""
                WITH requested AS (
                    SELECT * FROM unnest(
                        CAST(:event_ids AS uuid[]),
                        CAST(:patterns AS text[]),
                        CAST(:lower_bounds AS timestamptz[])
                    ) AS r(event_id, score_pattern, lower_bound)
                )
                SELECT requested.event_id, snapshot.id, snapshot.observed_at,
                       snapshot.status, snapshot.score
                FROM requested
                JOIN LATERAL (
                    SELECT id, observed_at, status, score
                    FROM event_state_snapshots
                    WHERE event_id = requested.event_id
                      AND observed_at >= requested.lower_bound
                      AND score ~ requested.score_pattern
                    ORDER BY observed_at DESC, id DESC
                    LIMIT 1
                ) AS snapshot ON TRUE
            """), {
                "event_ids": [item[0] for item in unresolved],
                "patterns": [item[1] for item in unresolved],
                "lower_bounds": [item[2] for item in unresolved],
            }).yield_per(10000)
            for row in historical_rows:
                evidence[row.event_id] = {
                    "snapshot_id": str(row.id), "observed_at": row.observed_at,
                    "status": row.status, "score": row.score,
                }

    # first_seen_at is non-null for normally ingested events. Retain a correct
    # no-bound fallback for legacy rows that predate this field's guarantee.
    for event_id, pattern, lower_bound in requested:
        if lower_bound is not None or event_id in evidence:
            continue
        row = session.query(
            EventStateSnapshot.id, EventStateSnapshot.event_id, EventStateSnapshot.observed_at,
            EventStateSnapshot.status, EventStateSnapshot.score,
        ).filter(
            EventStateSnapshot.event_id == event_id,
            EventStateSnapshot.score.op("~")(pattern),
        ).order_by(EventStateSnapshot.observed_at, EventStateSnapshot.id).limit(1).first()
        if row is not None:
            evidence[event_id] = {
                "snapshot_id": str(row.id), "observed_at": row.observed_at,
                "status": row.status, "score": row.score,
            }
    return evidence, len(evidence)


def _resolve_result_availability(metadata_time, snapshot_evidence):
    """Use the earliest actual result observation, never a scheduled/finished time."""
    observed_at = snapshot_evidence.get("observed_at") if snapshot_evidence else None
    if observed_at is not None and (metadata_time is None or observed_at < metadata_time):
        return observed_at, "fonbet_exact_final_score_snapshot"
    if metadata_time is not None:
        return metadata_time, "fonbet_official_result_metadata"
    return None, None


def _window_summary(event_ids, event_times, labels_by_event, required):
    """Count only whole matches that actually have a target label in this split."""
    ids = set(event_ids)
    labelled_ids = {event_id for event_id in ids if labels_by_event.get(event_id)}
    class_a = sum(0 in labels_by_event.get(event_id, set()) for event_id in labelled_ids)
    class_b = sum(1 in labels_by_event.get(event_id, set()) for event_id in labelled_ids)
    return {"matches": len(labelled_ids), "candidate_matches": len(ids),
            "class_a": class_a, "class_b": class_b,
            "both_classes": class_a > 0 and class_b > 0,
            "meets_match_minimum": len(labelled_ids) >= required}


def _temporal_precedence(train_ids, validation_ids, target_temporal):
    """Require every selected train outcome to predate validation forecasts."""
    train_labels = [target_temporal.get(event_id, {}).get("label_available_at") for event_id in train_ids]
    validation_forecasts = [target_temporal.get(event_id, {}).get("forecast_at") for event_id in validation_ids]
    missing_train = sum(value is None for value in train_labels)
    missing_validation = sum(value is None for value in validation_forecasts)
    valid_train = [value for value in train_labels if value is not None]
    valid_validation = [value for value in validation_forecasts if value is not None]
    latest_train_label = max(valid_train) if valid_train else None
    earliest_validation_forecast = min(valid_validation) if valid_validation else None
    passed = (missing_train == 0 and missing_validation == 0 and latest_train_label is not None
              and earliest_validation_forecast is not None
              and latest_train_label <= earliest_validation_forecast)
    return {"passed": bool(passed), "missing_train_label_times": missing_train,
            "missing_validation_forecast_times": missing_validation,
            "latest_train_label_available_at": latest_train_label.isoformat() if latest_train_label else None,
            "earliest_validation_forecast_at": earliest_validation_forecast.isoformat() if earliest_validation_forecast else None}


def _walk_forward_admission(target_rows, event_times, target_temporal=None):
    """Build one immutable whole-match split shared by all target masks."""
    # event_times contains only results whose source availability timestamp is
    # known. The split is shared by all targets; missing target labels are masks.
    ordered = sorted(event_times, key=lambda event_id: (event_times[event_id], str(event_id)))
    digest_input = "\n".join(f"{event_id}|{event_times[event_id].isoformat()}" for event_id in ordered)
    digest = hashlib.sha256(digest_input.encode()).hexdigest()
    manifest_path = os.getenv("TENNIS_TRAINING_MANIFEST_PATH") or f"docs/research/tennis_training_manifest_{digest[:12]}.json"
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("dataset_digest") != digest:
            raise RuntimeError("manifest path already contains a different dataset digest")
    else:
        holdout_start = int(len(ordered) * 0.8)
        development, holdout = ordered[:holdout_start], ordered[holdout_start:]
        initial_end = int(len(development) * 0.6)
        validation = development[initial_end:]
        edges = [round(i * len(validation) / 4) for i in range(5)]
        folds = [{"fold": i + 1,
                  "train_event_ids": [str(x) for x in development[:initial_end + edges[i]]],
                  "validation_event_ids": [str(x) for x in validation[edges[i]:edges[i + 1]]]} for i in range(4)]
        manifest = {"manifest_version": "tennis_shared_walk_forward_v1",
                    "created_at": datetime.now(timezone.utc).isoformat(), "dataset_digest": digest,
                    "ordered_event_ids": [str(x) for x in ordered],
                    "event_times": {str(x): event_times[x].isoformat() for x in ordered},
                    "development_event_ids": [str(x) for x in development],
                    "holdout_event_ids": [str(x) for x in holdout], "folds": folds,
                    "holdout_quality_metrics_opened": False}
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        os.replace(manifest_path + ".tmp", manifest_path)

    def ids(values):
        return [uuid.UUID(value) for value in values if uuid.UUID(value) in event_times]

    results = {}
    target_temporal = target_temporal or {}
    for target, rows in target_rows.items():
        labels = defaultdict(set)
        for row in rows:
            if row and row[0] in event_times and row[-1] in (0, 1):
                labels[row[0]].add(row[-1])
        folds = []
        for fold in manifest["folds"]:
            train_ids, validation_ids = ids(fold["train_event_ids"]), ids(fold["validation_event_ids"])
            temporal_train_ids = [event_id for event_id in train_ids if event_id in labels]
            temporal_validation_ids = [event_id for event_id in validation_ids if event_id in labels]
            temporal = _temporal_precedence(temporal_train_ids, temporal_validation_ids,
                                            target_temporal.get(target, {}))
            folds.append({"fold": fold["fold"],
                "train": _window_summary(train_ids, event_times, labels, 500),
                "validation": _window_summary(validation_ids, event_times, labels, 100),
                "temporal_precedence": temporal})
        holdout_summary = _window_summary(ids(manifest["holdout_event_ids"]), event_times, labels, 200)
        results[target] = {"matches_with_labels_on_manifest_timeline": len(labels),
            "manifest_matches": len(manifest["ordered_event_ids"]),
            "development_matches": len(manifest["development_event_ids"]),
            "holdout": holdout_summary, "folds": folds,
            "folds_meet_minimums": bool(folds) and all(
                f["train"]["meets_match_minimum"] and f["validation"]["meets_match_minimum"]
                and f["train"]["both_classes"] and f["validation"]["both_classes"]
                and f["temporal_precedence"]["passed"] for f in folds),
            "holdout_meets_minimums": holdout_summary["meets_match_minimum"] and holdout_summary["both_classes"]}
    return results, {"path": manifest_path, "dataset_digest": digest,
        "matches": len(manifest["ordered_event_ids"]), "development_matches": len(manifest["development_event_ids"]),
        "holdout_matches": len(manifest["holdout_event_ids"]), "holdout_quality_metrics_opened": False}


def _training_gate_status(*, base_coverage_ok: bool, windows_ok: bool,
                          temporal_ok: bool, point_in_time_ok: bool,
                          manifest_result_times_certified: bool) -> tuple[str, list[str]]:
    """Fail closed unless every independent training admission check passes."""
    reasons = []
    if not base_coverage_ok:
        reasons.append("base_target_coverage_or_both_classes_missing")
    if not windows_ok:
        reasons.append("walk_forward_or_holdout_minimums_not_met")
    if not temporal_ok:
        reasons.append("training_labels_not_available_before_validation_forecasts")
    if not point_in_time_ok:
        reasons.append("odds_free_feature_availability_not_certified")
    if not manifest_result_times_certified:
        reasons.append("manifest_result_availability_not_certified")
    return ("eligible_for_training_review" if not reasons else "blocked", reasons)


def build_report() -> dict:
    now = datetime.now(timezone.utc)
    report = {
        "generated_at": now.isoformat(),
        "source_scope": "numeric Fonbet IDs; singles; each feature and label gated by its source availability time",
        "targets": {name: {"eligible_matches": 0, "labelled_matches": 0, "parsed_labels": 0,
            "training_examples": 0, "class_a": 0, "class_b": 0,
            "observed_examples": 0, "retrospective_examples": 0}
            for name in ("point", "game", "set", "match")},
        "by_tour": {}, "by_discipline": {}, "by_competition": {},
        "tour_classification_sources": {},
        "result_time_evidence": {"snapshot_rows_returned": 0, "exact_terminal_score_snapshots": 0,
            "snapshots_preceding_metadata_time": 0, "sources": {}, "unknown_valid_results": 0},
        "point_history": {"documents_total": 0, "documents_linked": 0, "source_matches_total": 0,
            "current_links": 0, "linked_fonbet_events": 0, "parsed_versions_total": 0,
            "parsed_versions_linked": 0, "confirmed_outcomes_total": 0,
            "availability_unknown_documents": 0, "availability_unknown_versions": 0},
        "feature_availability": {"contract_version": "tennis_odds_free_v1",
            "checked_examples": 0, "certified_examples": 0, "uncertified_examples": 0,
            "source_groups": {}, "resolution_counts": {}, "exclusions": {}},
        "exclusions": {},
        "training_gate": {"status": "blocked", "required_per_target": {
            "initial_train_matches": 500, "each_validation_window_matches": 100, "holdout_matches": 200,
            "both_classes": True, "point_in_time_clean": True}, "windows_evaluated": False,
            "point_in_time_certified": False, "reasons": [],
            "chronology_policy": "official_result_source_time_or_exact_previously_observed_final_score_snapshot"},
    }
    exclusions = Counter()
    with SessionLocal() as session:
        # This classification is retrospective reporting metadata only; it is
        # never added to the model's point-in-time feature vector.
        link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
        latest_links = {}
        for link in link_rows:
            latest_links[(link.fonbet_event_id, link.source_match_id)] = link
        source_ids_by_event = defaultdict(set)
        for (event_id, source_id), link in latest_links.items():
            if link.status in ("matched", "manual"):
                source_ids_by_event[event_id].add(source_id)
        category_source_ids = {source_id for source_ids in source_ids_by_event.values()
                               for source_id in source_ids}
        external_by_source_id = {}
        if category_source_ids:
            for match in session.query(ExtTennisMatch).filter(
                ExtTennisMatch.source == "flashscore",
                ExtTennisMatch.source_match_id.in_(category_source_ids),
            ).all():
                external_by_source_id[match.source_match_id] = match
        external_tour_by_event = {}
        for event_id, source_ids in source_ids_by_event.items():
            # More than one confirmed source match is not an identity-safe
            # basis for classification, even if their circuit labels happen
            # to agree.
            if len(source_ids) != 1:
                continue
            source_id = next(iter(source_ids))
            match = external_by_source_id.get(source_id)
            level = _external_tour_level(match.tour_level, match.gender) if match else "unknown"
            if level != "unknown":
                external_tour_by_event[event_id] = level

        events = session.query(Event).filter(Event.sport_code == "tennis", Event.source == "fonbet").all()
        eligible = {}
        result_labels = {}
        result_times = {}
        result_time_sources = {}
        result_snapshot_evidence = {}
        groups = {}
        for event in events:
            if not str(event.source_event_id).isdigit():
                exclusions["non_numeric_fonbet_id"] += 1
                continue
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            official = metadata.get("fonbet_official_result") or {}
            league = session.get(League, event.league_id) if event.league_id else None
            competition = str(official.get("tournament") or metadata.get("raw_tournament") or (league.name if league else "unknown"))
            discipline = _discipline(event, competition)
            external_tour = external_tour_by_event.get(event.id)
            tour = _tour(competition, metadata, external_tour)
            tour_source = _tour_source(competition, metadata, external_tour)
            if discipline != "singles":
                exclusions["doubles"] += 1
                continue
            eligible[event.id] = event
            result_times[event.id] = _utc_result_time(event)
            result_time_sources[event.id] = "fonbet_official_result_metadata" if result_times[event.id] else None
            groups[event.id] = (tour, discipline, competition)
            report["tour_classification_sources"][tour_source] = (
                report["tour_classification_sources"].get(tour_source, 0) + 1
            )
            for key, name in (("by_tour", tour), ("by_discipline", discipline), ("by_competition", competition)):
                bucket = report[key].setdefault(name, _count_bucket())
                bucket["events"] += 1
            report["targets"]["match"]["eligible_matches"] += 1
            winner, reason = _official_winner(event)
            result_labels[event.id] = winner
            if winner is None:
                exclusions[reason] += 1
                continue
            report["targets"]["match"]["labelled_matches"] += 1
            report["targets"]["match"]["parsed_labels"] += 1
            report["targets"]["match"]["class_a" if winner == 0 else "class_b"] += 1
            for key, name in (("by_tour", tour), ("by_discipline", discipline), ("by_competition", competition)):
                report[key][name]["valid_match_labels"] += 1

        snapshot_evidence, snapshot_rows_returned = _result_score_snapshot_evidence(
            session, {event_id: event for event_id, event in eligible.items() if result_labels[event_id] is not None},
        )
        report["result_time_evidence"]["snapshot_rows_returned"] = snapshot_rows_returned
        for event_id, evidence in snapshot_evidence.items():
            report["result_time_evidence"]["exact_terminal_score_snapshots"] += 1
            result_snapshot_evidence[event_id] = evidence
            metadata_time = result_times.get(event_id)
            result_times[event_id], result_time_sources[event_id] = _resolve_result_availability(
                metadata_time, evidence,
            )
            if metadata_time is not None and result_time_sources[event_id] == "fonbet_exact_final_score_snapshot":
                report["result_time_evidence"]["snapshots_preceding_metadata_time"] += 1
        report["result_time_evidence"]["unknown_valid_results"] = sum(
            result_labels[event_id] is not None and result_times.get(event_id) is None
            for event_id in eligible
        )
        report["result_time_evidence"]["sources"] = dict(Counter(
            result_time_sources[event_id] or "unavailable"
            for event_id in eligible if result_labels[event_id] is not None
        ))

        # Historical serving statistics and surface context are not yet
        # backed by point-in-time identity/source records. Record that gap
        # against each real snapshot rather than treating today's player
        # profile or the retrospective Flashscore match as historical input.
        participant_ids = {pid for event in eligible.values()
                           for pid in (event.participant_a_id, event.participant_b_id) if pid}
        profile_rows = session.query(TennisPlayerProfile).all()
        profiles_by_participant = {row.participant_id: row for row in profile_rows}
        identity_rows = session.query(TennisPlayerIdentityLink).filter(
            TennisPlayerIdentityLink.source == "flashscore",
            TennisPlayerIdentityLink.available_at <= now,
        ).order_by(TennisPlayerIdentityLink.available_at.desc(), TennisPlayerIdentityLink.id.desc()).all()
        identity_versions_by_participant = defaultdict(list)
        for row in identity_rows:
            identity_versions_by_participant[row.participant_id].append(row)
        metric_rows = session.query(TennisPlayerMetricSnapshot).order_by(
            TennisPlayerMetricSnapshot.available_at
        ).all()
        metrics_by_participant = defaultdict(list)
        for metric in metric_rows:
            metrics_by_participant[metric.participant_id].append(metric)
        # Match inference's name-based external serve-log lookup. These records
        # have no canonical player ID, so only the same normalized full name is
        # usable; surname-only matches would create identity leakage.
        serve_log_rows = session.query(ExtTennisServeLog.player_name, ExtTennisServeLog.available_at).filter(
            ExtTennisServeLog.available_at <= now
        ).all()
        serve_logs_by_name = defaultdict(list)
        for player_name, available_at in serve_log_rows:
            serve_logs_by_name[normalize_russian_player_name(player_name)].append(available_at)
        feature_availability_by_event_snapshot = {}

        def feature_availability(event_id, snapshot_id, snapshot_at):
            key = (event_id, snapshot_id)
            if key in feature_availability_by_event_snapshot:
                return feature_availability_by_event_snapshot[key]
            event = eligible[event_id]
            player_times = []
            serve_times = {"a": [], "b": []}
            player_resolution = {}
            serve_resolution = {}
            event = eligible[event_id]
            for side, participant_id in (("a", event.participant_a_id), ("b", event.participant_b_id)):
                profile = profiles_by_participant.get(participant_id)
                latest_link = next((row for row in identity_versions_by_participant.get(participant_id, [])
                                    if row.available_at <= snapshot_at), None)
                current_identities = (profile.source_identities or {}) if profile else {}
                confirmed_id = latest_link.source_player_id if latest_link and latest_link.status == "confirmed" else None
                identity_valid = bool(profile)
                if confirmed_id and current_identities.get("flashscore") not in (None, confirmed_id):
                    identity_valid = False
                if not confirmed_id and (current_identities.get("flashscore")
                        or current_identities.get("flashscore_candidates") or profile.profile_url if profile else False):
                    identity_valid = False
                metric_ids = {participant_id} if participant_id else set()
                if identity_valid and confirmed_id:
                    metric_ids = set()
                    for candidate_id, versions in identity_versions_by_participant.items():
                        candidate_link = next((row for row in versions if row.available_at <= snapshot_at), None)
                        if candidate_link and candidate_link.status == "confirmed" and candidate_link.source_player_id == confirmed_id:
                            metric_ids.add(candidate_id)
                available = [row for metric_id in metric_ids
                             for row in metrics_by_participant.get(metric_id, [])
                             if row.available_at <= snapshot_at] if identity_valid else []
                metric_available_at = max((row.available_at for row in available), default=None)
                metric_values = max(available, key=lambda row: (row.available_at, row.metrics_version)).metrics_json if available else None
                if metric_available_at is None and identity_valid:
                    # load_player_metric has a compatibility fallback to the
                    # materialized current profile when no immutable snapshot
                    # was yet available. Its own metrics_available_at remains
                    # the causal bound and must be represented by the audit.
                    eligible_profiles = [profiles_by_participant[metric_id]
                        for metric_id in metric_ids
                        if metric_id in profiles_by_participant
                        and profiles_by_participant[metric_id].metrics_available_at is not None
                        and profiles_by_participant[metric_id].metrics_available_at <= snapshot_at
                        and profiles_by_participant[metric_id].metrics_json is not None]
                    if eligible_profiles:
                        owner = max(eligible_profiles, key=lambda row: row.metrics_available_at)
                        metric_available_at, metric_values = owner.metrics_available_at, owner.metrics_json
                if metric_available_at is not None:
                    player_times.append(metric_available_at)
                    player_resolution[side] = "observed_snapshot" if available else "observed_profile_cache"
                else:
                    # Missing metrics are encoded by the model as the fixed Elo /
                    # form prior and a missingness flag. The prior is knowable at
                    # this as_of, so retain it as a contract default explicitly.
                    player_resolution[side] = "contract_default"
                metric_serve = ((metric_values or {}).get("serve") or {})
                if float(metric_serve.get("points") or 0) > 0 and metric_available_at is not None:
                    serve_times[side].append(metric_available_at)
                    serve_resolution[side] = "metric_snapshot"
                else:
                    name = normalize_russian_player_name(
                        event.participant_a_name if side == "a" else event.participant_b_name
                    )
                    since = snapshot_at - timedelta(days=365)
                    historical_logs = [value for value in serve_logs_by_name.get(name, [])
                                       if since <= value <= snapshot_at]
                    if historical_logs:
                        serve_times[side].append(max(historical_logs))
                        serve_resolution[side] = "observed_serve_log"
                    else:
                        # The serving prior and zero sample weight are explicit
                        # features; no future source value is substituted.
                        serve_resolution[side] = "contract_default"
            # Surface is only consumed by inference with its own explicit
            # availability timestamp. Historical rows without one use unknown.
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            raw_surface_at = metadata.get("surface_available_at")
            try:
                surface_at = datetime.fromisoformat(str(raw_surface_at).replace("Z", "+00:00")) if raw_surface_at else None
            except ValueError:
                surface_at = None
            if surface_at is not None and surface_at.tzinfo is None:
                surface_at = surface_at.replace(tzinfo=timezone.utc)
            surface_known = bool(surface_at and surface_at <= snapshot_at and
                                 str(metadata.get("surface") or "").casefold() in {"hard", "clay", "grass", "carpet"})
            sources = {
                "player_metrics": max(player_times).isoformat() if player_times else None,
                "serve_metrics_a": max(serve_times["a"]).isoformat() if serve_times["a"] else None,
                "serve_metrics_b": max(serve_times["b"]).isoformat() if serve_times["b"] else None,
                "event_state": snapshot_at.isoformat(),
                "event_surface": surface_at.isoformat() if surface_known else None,
            }
            contract_defaults = set()
            if "contract_default" in player_resolution.values():
                contract_defaults.add("player_metrics")
            if serve_resolution.get("a") == "contract_default":
                contract_defaults.add("serve_metrics_a")
            if serve_resolution.get("b") == "contract_default":
                contract_defaults.add("serve_metrics_b")
            if not surface_known:
                contract_defaults.add("event_surface")
            validation = _validate_feature_availability(snapshot_at, sources, contract_defaults)
            details = {**validation, "source_available_at": sources,
                       "source_resolution": {
                           "player_metrics": player_resolution,
                           "serve_metrics": serve_resolution,
                           "event_surface": "observed" if surface_known else "contract_default_unknown",
                           "event_state": "observed_snapshot",
                       }}
            feature_availability_by_event_snapshot[key] = details
            return details

        event_link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.in_(list(eligible)),
            TennisPointMatchLink.available_at <= now,
        ).all() if eligible else []
        candidate_source_ids = {link.source_match_id for link in event_link_rows}
        link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.source_match_id.in_(candidate_source_ids) if candidate_source_ids else False,
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
        latest_links = {}
        for link in link_rows:
            latest_links[link.source_match_id] = link
        links = [link for link in latest_links.values()
                 if link.fonbet_event_id in eligible and link.status in ("matched", "manual")]
        report["point_history"]["current_links"] = len(links)
        report["point_history"]["linked_fonbet_events"] = len({link.fonbet_event_id for link in links})
        orientation = {}
        source_ids_by_event = defaultdict(set)
        link_ids_by_event_source = {}
        link_available_at_by_event_source = {}
        for link in links:
            source_ids_by_event[link.fonbet_event_id].add(link.source_match_id)
            value = (link.evidence_json or {}).get("external_home_is_fonbet_a")
            orientation[(link.fonbet_event_id, link.source_match_id)] = value if value in (True, False) else None
            link_ids_by_event_source[(link.fonbet_event_id, link.source_match_id)] = str(link.id)
            link_available_at_by_event_source[(link.fonbet_event_id, link.source_match_id)] = link.available_at.isoformat()
        source_ids = list({source_id for values in source_ids_by_event.values() for source_id in values})
        archived_versions = session.query(
            TennisPointHistoryVersion.source_match_id,
            TennisPointHistoryVersion.available_at,
        ).filter(
            TennisPointHistoryVersion.retrieved_at <= now,
        ).yield_per(1000)
        source_matches_seen = set()
        for source_match_id, available_at in archived_versions:
            source_matches_seen.add(source_match_id)
            report["point_history"]["parsed_versions_total"] += 1
            report["point_history"]["availability_unknown_versions"] += int(available_at is None)
        report["point_history"]["source_matches_total"] = len(source_matches_seen)
        versions_all = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).yield_per(250)
        report["point_history"]["documents_total"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.retrieved_at <= now,
        ).count()
        report["point_history"]["availability_unknown_documents"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.retrieved_at <= now, TennisPointDocument.available_at.is_(None)
        ).count()
        all_latest_by_source_set = {}
        for version in versions_all:
            if not is_authoritative_point_history_version(version.events_json, version.quality_json):
                continue
            set_numbers = {version.quality_json.get("set_number")} if isinstance(version.quality_json, dict) and version.quality_json.get("set_number") else set()
            set_numbers.update(item.get("set") for item in (version.events_json or []) if item.get("set") is not None)
            for set_no in set_numbers or {None}:
                all_latest_by_source_set[(version.source_match_id, set_no)] = version
        raw_outcomes = set()
        for (source_id, set_no), version in all_latest_by_source_set.items():
            for item in version.events_json or []:
                if set_no is not None and item.get("set") != set_no:
                    continue
                if item.get("confirmed") and item.get("winner") in (0, 1):
                    raw_outcomes.add((source_id, set_no, tuple(item.get("game_score") or []), item.get("point_index"),
                        tuple(item.get("state_before") or []), bool(item.get("tie_break")), item.get("winner")))
        report["point_history"]["confirmed_outcomes_total"] = len(raw_outcomes)
        versions = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.source_match_id.in_(source_ids),
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).yield_per(250) if source_ids else []
        report["point_history"]["documents_linked"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.source_match_id.in_(source_ids) if source_ids else False,
            TennisPointDocument.retrieved_at <= now,
        ).count()
        latest = {}
        for version in versions:
            report["point_history"]["parsed_versions_linked"] += 1
            if not is_authoritative_point_history_version(version.events_json, version.quality_json):
                continue
            matching_events = [event_id for event_id, ids in source_ids_by_event.items() if version.source_match_id in ids]
            for event_id in matching_events:
                set_numbers = {version.quality_json.get("set_number")} if isinstance(version.quality_json, dict) and version.quality_json.get("set_number") else set()
                set_numbers.update(item.get("set") for item in (version.events_json or []) if item.get("set") is not None)
                for set_no in set_numbers:
                    latest[(event_id, version.source_match_id, set_no)] = version

        # Exact live-state joins: ambiguous repeated score states and unknown side order are excluded.
        event_ids_with_history = {event_id for event_id, _, _ in latest}
        event_ids_with_snapshots = set()
        representative_snapshots = {}
        valid_match_ids = [event_id for event_id, winner in result_labels.items() if winner is not None]
        for start in range(0, len(valid_match_ids), 200):
            batch = valid_match_ids[start:start + 200]
            # Use the event_id/observed_at index for one earliest observation
            # per event. DISTINCT ON over the entire hypertable caused a large
            # sort for an audit that needs only one snapshot per match.
            rows = session.execute(text("""
                WITH requested AS (
                    SELECT unnest(CAST(:event_ids AS uuid[])) AS event_id
                )
                SELECT snapshot.id, snapshot.observed_at, snapshot.event_id,
                       snapshot.score_detail, snapshot.sport_state,
                       snapshot.current_period
                FROM requested
                JOIN LATERAL (
                    SELECT id, observed_at, event_id, score_detail, sport_state,
                           current_period
                    FROM event_state_snapshots
                    WHERE event_id = requested.event_id
                    ORDER BY observed_at, id
                    LIMIT 1
                ) AS snapshot ON TRUE
            """), {"event_ids": batch}).all()
            for row in rows:
                event_ids_with_snapshots.add(row.event_id)
                representative_snapshots[row.event_id] = row
        snapshots_by_event = defaultdict(list)
        ids_to_read = list(event_ids_with_history)
        for start in range(0, len(ids_to_read), 200):
            batch = ids_to_read[start:start + 200]
            rows = session.query(EventStateSnapshot).filter(EventStateSnapshot.event_id.in_(batch)).order_by(
                EventStateSnapshot.event_id, EventStateSnapshot.observed_at, EventStateSnapshot.id
            ).yield_per(5000)
            for row in rows:
                state = _snapshot_state(row)
                if state is not None:
                    snapshots_by_event[row.event_id].append((state, row.id, row.observed_at))

        parsed_label_keys = {name: set() for name in ("point", "game", "set", "match")}
        aligned_label_keys = {name: set() for name in ("point", "game", "set", "match")}
        provenance = {name: Counter() for name in ("point", "game", "set")}
        example_provenance = {}

        def record_example(target, identity, event_id, label, snapshot_id, snapshot_at,
                           version=None, source_id=None, item=None):
            key = (target, identity)
            if key in example_provenance:
                return
            source_label_times = [result_times.get(event_id)]
            if version is not None:
                source_label_times.append(version.available_at)
                linked_at = link_available_at_by_event_source.get((event_id, source_id))
                if linked_at:
                    try:
                        source_label_times.append(datetime.fromisoformat(linked_at.replace("Z", "+00:00")))
                    except ValueError:
                        pass
            known_label_times = [value for value in source_label_times if value is not None]
            label_available_at = max(known_label_times) if known_label_times else None
            example_provenance[key] = {
                "target": target, "fonbet_event_id": str(event_id),
                "fonbet_source_event_id": str(eligible[event_id].source_event_id),
                "snapshot_id": str(snapshot_id), "snapshot_observed_at": snapshot_at.isoformat(),
                "source_match_id": str(source_id) if source_id else None,
                "point_document_id": str(version.document_id) if version else None,
                "point_version_id": str(version.id) if version else None,
                "match_link_id": link_ids_by_event_source.get((event_id, source_id)) if source_id else None,
                "match_link_available_at": link_available_at_by_event_source.get((event_id, source_id)) if source_id else None,
                "set_number": item.get("set") if item else None,
                "game_score": item.get("game_score") if item else None,
                "point_index": item.get("point_index") if item else None,
                "source_point_server_known": item.get("server") in (0, 1) if item else None,
                "label": int(label),
                "label_available_at": label_available_at.isoformat() if label_available_at else None,
                "result_time_source": result_time_sources.get(event_id),
                "result_time_snapshot_evidence": ({
                    **result_snapshot_evidence[event_id],
                    "observed_at": result_snapshot_evidence[event_id]["observed_at"].isoformat(),
                } if event_id in result_snapshot_evidence else None),
                "retrospective": bool(version.retrospective) if version else False,
                "feature_availability": feature_availability(event_id, snapshot_id, snapshot_at),
            }
        for (event_id, source_id, set_no), version in latest.items():
            event = eligible.get(event_id)
            side_order = orientation.get((event_id, source_id))
            if event is None:
                continue
            category = groups[event_id]
            selected = {"by_tour": category[0], "by_discipline": category[1], "by_competition": category[2]}
            source_events = version.events_json or []
            terminal_qualities = {"terminal_from_game_result", "terminal_from_tiebreak_result"}
            confirmed = [item for item in source_events if item.get("confirmed") and item.get("winner") in (0, 1)
                         and item.get("quality") not in terminal_qualities]
            terminal_games = [item for item in source_events
                              if item.get("confirmed") and item.get("winner") in (0, 1)
                              and item.get("quality") == "terminal_from_game_result"]
            terminal_tiebreaks = [item for item in source_events
                                   if item.get("confirmed") and item.get("winner") in (0, 1)
                                   and item.get("quality") == "terminal_from_tiebreak_result"]
            for item in confirmed:
                identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []),
                    item.get("point_index"), tuple(item.get("state_before") or []), item.get("tie_break", False))
                parsed_label_keys["point"].add(identity)
                provenance["point"]["retrospective" if version.retrospective else "observed"] += 1
            for item in terminal_games:
                game_identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []))
                parsed_label_keys["game"].add(game_identity)
                provenance["game"]["retrospective" if version.retrospective else "observed"] += 1
            for item in terminal_tiebreaks:
                game_identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []))
                parsed_label_keys["game"].add(game_identity)
                provenance["game"]["retrospective" if version.retrospective else "observed"] += 1
            terminal_sets = terminal_games + terminal_tiebreaks
            for item in terminal_sets:
                score = item.get("game_score")
                if isinstance(score, list) and len(score) == 2 and _is_finished_set(int(score[0]), int(score[1])):
                    parsed_label_keys["set"].add((event_id, source_id, item.get("set")))
                    provenance["set"]["retrospective" if version.retrospective else "observed"] += 1
            if side_order is None:
                exclusions["point_side_order_unknown"] += len(confirmed)
                continue
            source_point_events = confirmed
            signatures = Counter(_source_point_key(item, side_order) for item in source_point_events)
            for item in source_point_events:
                signature = _source_point_key(item, side_order)
                if signature is None or signatures[signature] != 1:
                    continue
                next_winner = item.get("winner") if side_order else 1 - item.get("winner")
                for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                    if _snapshot_matches_source_point(snapshot_state, signature):
                        identity = (event_id, source_id, set_no, signature, next_winner)
                        aligned_label_keys["point"].add(identity)
                        record_example("point", identity, event_id, next_winner, snapshot_id,
                                       snapshot_at, version, source_id, item)

            # Set and match labels are attached only to real Fonbet snapshots in the event.
            for item in terminal_sets:
                score = item.get("game_score")
                if not isinstance(score, list) or len(score) != 2:
                    continue
                winner = item.get("winner") if side_order else 1 - item.get("winner")
                after = list(_orient(score, side_order))
                before = after.copy()
                before[winner] -= 1
                if min(before) >= 0:
                    game_key = (item.get("set"), tuple(before))
                    for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                        if snapshot_state[0] == game_key[0] and snapshot_state[1] == game_key[1]:
                            identity = (event_id, source_id, game_key, winner)
                            aligned_label_keys["game"].add(identity)
                            record_example("game", identity, event_id, winner, snapshot_id,
                                           snapshot_at, version, source_id, item)
                if _is_finished_set(int(score[0]), int(score[1])):
                    for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                        if snapshot_state[0] == item.get("set"):
                            identity = (event_id, source_id, item.get("set"), winner)
                            aligned_label_keys["set"].add(identity)
                            record_example("set", identity, event_id, winner, snapshot_id,
                                           snapshot_at, version, source_id, item)

        for target in ("point", "game", "set"):
            report["targets"][target]["parsed_labels"] = len(parsed_label_keys[target])
            report["targets"][target]["training_examples"] = len(aligned_label_keys[target])
            report["targets"][target]["labelled_matches"] = len({row[0] for row in aligned_label_keys[target]})
            report["targets"][target]["retrospective_examples"] = provenance[target]["retrospective"]
            report["targets"][target]["observed_examples"] = provenance[target]["observed"]
        for event_id in valid_match_ids:
            if event_id in event_ids_with_snapshots:
                identity = (event_id, result_labels[event_id])
                aligned_label_keys["match"].add(identity)
                snapshot = representative_snapshots[event_id]
                source_id = next(iter(sorted(source_ids_by_event.get(event_id, []))), None)
                example_provenance[("match", identity)] = {
                    "target": "match", "fonbet_event_id": str(event_id),
                    "fonbet_source_event_id": str(eligible[event_id].source_event_id),
                    "snapshot_id": str(snapshot.id), "snapshot_observed_at": snapshot.observed_at.isoformat(),
                    "source_match_id": source_id,
                    "point_document_id": None, "point_version_id": None,
                    "match_link_id": link_ids_by_event_source.get((event_id, source_id)) if source_id else None,
                    "match_link_available_at": link_available_at_by_event_source.get((event_id, source_id)) if source_id else None,
                    "set_number": None, "game_score": None, "point_index": None,
                    "label": int(result_labels[event_id]),
                    "label_available_at": result_times[event_id].isoformat()
                        if result_times.get(event_id) else None,
                    "result_time_source": result_time_sources.get(event_id),
                    "result_time_snapshot_evidence": ({
                        **result_snapshot_evidence[event_id],
                        "observed_at": result_snapshot_evidence[event_id]["observed_at"].isoformat(),
                    } if event_id in result_snapshot_evidence else None),
                    "retrospective": False,
                    "feature_availability": feature_availability(event_id, snapshot.id, snapshot.observed_at),
                }
        report["targets"]["match"]["training_examples"] = len(aligned_label_keys["match"])
        report["targets"]["match"]["labelled_matches"] = len({row[0] for row in aligned_label_keys["match"]})
        for target in ("point", "game", "set", "match"):
            label_keys = aligned_label_keys[target]
            report["targets"][target]["class_a"] = sum(row[-1] == 0 for row in label_keys)
            report["targets"][target]["class_b"] = sum(row[-1] == 1 for row in label_keys)
        report["exclusions"] = dict(sorted(exclusions.items()))
        for target in ("point", "game", "set", "match"):
            report["targets"][target]["eligible_matches"] = len(eligible)
        for key in ("by_tour", "by_discipline", "by_competition"):
            for bucket in report[key].values():
                bucket["point_examples"] = 0
                bucket["game_examples"] = 0
                bucket["set_examples"] = 0
        for target, field in (("point", "point_examples"), ("game", "game_examples"), ("set", "set_examples")):
            for row in aligned_label_keys[target]:
                event_id = row[0]
                tour_name, discipline_name, competition_name = groups[event_id]
                for key, name in (("by_tour", tour_name), ("by_discipline", discipline_name), ("by_competition", competition_name)):
                    report[key][name][field] += 1
        target_rows = {name: aligned_label_keys[name] for name in ("point", "game", "set", "match")}
        target_temporal = defaultdict(dict)
        for row in example_provenance.values():
            target = row["target"]
            event_id = uuid.UUID(row["fonbet_event_id"])
            temporal = target_temporal[target].setdefault(event_id, {"label_available_at": None, "forecast_at": None})
            label_at = row.get("label_available_at")
            forecast_at = row.get("snapshot_observed_at")
            if label_at:
                value = datetime.fromisoformat(label_at.replace("Z", "+00:00"))
                current = temporal["label_available_at"]
                if current is None or value > current:
                    temporal["label_available_at"] = value
            if forecast_at:
                value = datetime.fromisoformat(forecast_at.replace("Z", "+00:00"))
                current = temporal["forecast_at"]
                if current is None or value < current:
                    temporal["forecast_at"] = value
        manifest_event_times = {
            key: value for key, value in result_times.items()
            if value is not None and result_labels.get(key) is not None
        }
        window_report, manifest_metadata = _walk_forward_admission(
            target_rows, manifest_event_times, target_temporal)
        report["training_gate"]["walk_forward"] = window_report
        report["training_gate"]["manifest"] = manifest_metadata
        example_rows = [example_provenance[key] for key in sorted(example_provenance, key=lambda item: (item[0], repr(item[1])))]
        feature_report = report["feature_availability"]
        feature_checks = [row.get("feature_availability", {}) for row in example_rows]
        feature_report["checked_examples"] = len(feature_checks)
        feature_report["certified_examples"] = sum(bool(item.get("certified")) for item in feature_checks)
        feature_report["uncertified_examples"] = feature_report["checked_examples"] - feature_report["certified_examples"]
        for check in feature_checks:
            source_times = check.get("source_available_at", {})
            resolution = check.get("source_resolution", {})
            defaulted_sources = set(check.get("contract_defaulted_sources", []))
            for group_name, resolution_value in resolution.items():
                if isinstance(resolution_value, dict):
                    for side, side_resolution in resolution_value.items():
                        key = f"{group_name}.{side}:{side_resolution}"
                        feature_report["resolution_counts"][key] = feature_report["resolution_counts"].get(key, 0) + 1
                else:
                    key = f"{group_name}:{resolution_value}"
                    feature_report["resolution_counts"][key] = feature_report["resolution_counts"].get(key, 0) + 1
            for name in PIT_FEATURE_SOURCE_GROUPS:
                group = feature_report["source_groups"].setdefault(
                    name, {"available": 0, "contract_defaulted": 0, "missing_or_unverifiable": 0})
                if name in defaulted_sources:
                    group["contract_defaulted"] += 1
                elif source_times.get(name) is None:
                    group["missing_or_unverifiable"] += 1
                else:
                    group["available"] += 1
            for name in check.get("missing_sources", []) + check.get("future_sources", []):
                feature_report["exclusions"][name] = feature_report["exclusions"].get(name, 0) + 1
        examples_digest = hashlib.sha256("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"))
            for row in example_rows).encode("utf-8")).hexdigest()
        examples_path = f"docs/research/tennis_training_examples_{examples_digest[:12]}.jsonl"
        os.makedirs(os.path.dirname(examples_path), exist_ok=True)
        temporary_examples_path = examples_path + ".tmp"
        with open(temporary_examples_path, "w", encoding="utf-8") as handle:
            for row in example_rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary_examples_path, examples_path)
        report["training_examples_manifest"] = {
            "path": examples_path, "examples": len(example_rows), "sha256": examples_digest,
        }
        undated = sum(value is None for value in result_times.values())
        report["training_gate"]["chronology_timestamp_missing_matches"] = undated
        report["training_gate"]["result_availability_unknown_matches"] = sum(
            result_labels.get(event_id) in (0, 1) and result_times.get(event_id) is None
            for event_id in eligible)
        report["training_gate"]["unknown_result_time_policy"] = (
            "exclude_from_manifest; use exact previously observed final-score snapshot when available")
        # Only labelled matches with source-observed result times enter the
        # split. Missing-time matches remain visible as exclusions, not a
        # blanket blocker for otherwise clean candidate windows.
        report["training_gate"]["manifest_result_availability_certified"] = all(
            result_times.get(event_id) is not None
            and result_time_sources.get(event_id) in {
                "fonbet_official_result_metadata", "fonbet_exact_final_score_snapshot"
            }
            for event_id in manifest_event_times
        )
        report["training_gate"]["unknown_result_time_excluded_matches"] = sum(
            result_labels.get(event_id) in (0, 1) and result_times.get(event_id) is None
            for event_id in eligible
        )
        report["training_gate"]["scheduled_time_used_as_chronology"] = sum(
            source == "scheduled_start_at" for source in result_time_sources.values())
        report["training_gate"]["result_time_sources"] = report["result_time_evidence"]["sources"]
        report["training_gate"]["windows_evaluated"] = True
        windows_ok = all(item["folds_meet_minimums"] and item["holdout_meets_minimums"] for item in window_report.values())
        temporal_ok = all(
            bool(item["folds"]) and all(fold["temporal_precedence"]["passed"] for fold in item["folds"])
            for item in window_report.values())
        base_coverage_ok = all(
            report["targets"][name]["labelled_matches"] >= 500
            and report["targets"][name]["class_a"] > 0 and report["targets"][name]["class_b"] > 0
            for name in ("point", "game", "set", "match")
        )
        report["training_gate"]["base_coverage_status"] = "sufficient_for_window_assessment" if base_coverage_ok else "insufficient"
        # This audit now checks feature source times for every emitted
        # example. Missing historical source provenance is a blocking failure.
        report["training_gate"]["point_in_time_certified"] = (
            feature_report["checked_examples"] > 0
            and feature_report["uncertified_examples"] == 0
        )
        status, reasons = _training_gate_status(
            base_coverage_ok=base_coverage_ok,
            windows_ok=windows_ok,
            temporal_ok=temporal_ok,
            point_in_time_ok=report["training_gate"]["point_in_time_certified"],
            manifest_result_times_certified=report["training_gate"]["manifest_result_availability_certified"],
        )
        report["training_gate"]["status"] = status
        report["training_gate"]["reasons"] = reasons
    return report
