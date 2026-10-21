"""Point-in-time label audit for numeric Fonbet singles tennis events."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
import hashlib
import json
import os
import uuid
from sqlalchemy import text

from db.connection import SessionLocal
from db.models import Event, EventStateSnapshot, ExtTennisMatch, League, TennisPointDocument, TennisPointHistoryVersion, TennisPointMatchLink


def _is_finished_set(a: int, b: int) -> bool:
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def _tour(text: str, metadata: dict | None = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
    explicit = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                         ("tour", "category", "circuit", "age_group", "tournament_type"))
    value = f"{text} {explicit}".casefold()
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
    if server not in (0, 1):
        return None
    server = server if external_home_is_a else 1 - server
    return item.get("set"), tuple(games), tuple(point), server, is_tiebreak


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


def _has_result_availability(event: Event) -> bool:
    metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
    official = metadata.get("fonbet_official_result") or {}
    raw = official.get("available_at") or official.get("observed_at")
    if not isinstance(raw, str):
        return False
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


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


def _walk_forward_admission(target_rows, event_times):
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
    for target, rows in target_rows.items():
        labels = defaultdict(set)
        for row in rows:
            if row and row[0] in event_times and row[-1] in (0, 1):
                labels[row[0]].add(row[-1])
        folds = []
        for fold in manifest["folds"]:
            train_ids, validation_ids = ids(fold["train_event_ids"]), ids(fold["validation_event_ids"])
            folds.append({"fold": fold["fold"],
                "train": _window_summary(train_ids, event_times, labels, 500),
                "validation": _window_summary(validation_ids, event_times, labels, 100)})
        holdout_summary = _window_summary(ids(manifest["holdout_event_ids"]), event_times, labels, 200)
        results[target] = {"matches_with_labels_on_manifest_timeline": len(labels),
            "manifest_matches": len(manifest["ordered_event_ids"]),
            "development_matches": len(manifest["development_event_ids"]),
            "holdout": holdout_summary, "folds": folds,
            "folds_meet_minimums": bool(folds) and all(
                f["train"]["meets_match_minimum"] and f["validation"]["meets_match_minimum"]
                and f["train"]["both_classes"] and f["validation"]["both_classes"] for f in folds),
            "holdout_meets_minimums": holdout_summary["meets_match_minimum"] and holdout_summary["both_classes"]}
    return results, {"path": manifest_path, "dataset_digest": digest,
        "matches": len(manifest["ordered_event_ids"]), "development_matches": len(manifest["development_event_ids"]),
        "holdout_matches": len(manifest["holdout_event_ids"]), "holdout_quality_metrics_opened": False}


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
        "point_history": {"documents_total": 0, "documents_linked": 0, "source_matches_total": 0,
            "current_links": 0, "linked_fonbet_events": 0, "parsed_versions_total": 0,
            "parsed_versions_linked": 0, "confirmed_outcomes_total": 0,
            "availability_unknown_documents": 0, "availability_unknown_versions": 0},
        "exclusions": {},
        "training_gate": {"status": "blocked", "required_per_target": {
            "initial_train_matches": 500, "each_validation_window_matches": 100, "holdout_matches": 200,
            "both_classes": True, "point_in_time_clean": True}, "windows_evaluated": False,
            "point_in_time_certified": False, "reasons": [],
            "chronology_policy": "official_result.available_at_or_observed_at_only"},
    }
    exclusions = Counter()
    with SessionLocal() as session:
        events = session.query(Event).filter(Event.sport_code == "tennis", Event.source == "fonbet").all()
        eligible = {}
        result_labels = {}
        result_times = {}
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
            tour = _tour(competition, metadata)
            if discipline != "singles":
                exclusions["doubles"] += 1
                continue
            eligible[event.id] = event
            result_times[event.id] = _utc_result_time(event)
            groups[event.id] = (tour, discipline, competition)
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
        archived_versions = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.retrieved_at <= now,
        ).order_by(TennisPointHistoryVersion.retrieved_at, TennisPointHistoryVersion.id).all()
        report["point_history"]["parsed_versions_total"] = len(archived_versions)
        report["point_history"]["availability_unknown_versions"] = sum(row.available_at is None for row in archived_versions)
        report["point_history"]["source_matches_total"] = len({row.source_match_id for row in archived_versions})
        versions_all = session.query(TennisPointHistoryVersion).filter(
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).all()
        report["point_history"]["documents_total"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.retrieved_at <= now,
        ).count()
        report["point_history"]["availability_unknown_documents"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.retrieved_at <= now, TennisPointDocument.available_at.is_(None)
        ).count()
        all_latest_by_source_set = {}
        for version in versions_all:
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
        ).order_by(TennisPointHistoryVersion.available_at, TennisPointHistoryVersion.id).all() if source_ids else []
        report["point_history"]["parsed_versions_linked"] = len(versions)
        report["point_history"]["documents_linked"] = session.query(TennisPointDocument).filter(
            TennisPointDocument.source_match_id.in_(source_ids) if source_ids else False,
            TennisPointDocument.retrieved_at <= now,
        ).count()
        latest = {}
        for version in versions:
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
                "label": int(label),
                "label_available_at": _utc_result_time(eligible[event_id]).isoformat()
                    if _utc_result_time(eligible[event_id]) else None,
                "retrospective": bool(version.retrospective) if version else False,
            }
        for (event_id, source_id, set_no), version in latest.items():
            event = eligible.get(event_id)
            side_order = orientation.get((event_id, source_id))
            if event is None:
                continue
            category = groups[event_id]
            selected = {"by_tour": category[0], "by_discipline": category[1], "by_competition": category[2]}
            source_events = version.events_json or []
            confirmed = [item for item in source_events if item.get("confirmed") and item.get("winner") in (0, 1)]
            terminal_games = [item for item in confirmed if item.get("quality") == "terminal_from_game_result"]
            for item in confirmed:
                identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []),
                    item.get("point_index"), tuple(item.get("state_before") or []), item.get("tie_break", False))
                parsed_label_keys["point"].add(identity)
                provenance["point"]["retrospective" if version.retrospective else "observed"] += 1
            for item in terminal_games:
                game_identity = (event_id, source_id, item.get("set"), tuple(item.get("game_score") or []))
                parsed_label_keys["game"].add(game_identity)
                provenance["game"]["retrospective" if version.retrospective else "observed"] += 1
                score = item.get("game_score")
                if isinstance(score, list) and len(score) == 2 and _is_finished_set(int(score[0]), int(score[1])):
                    parsed_label_keys["set"].add((event_id, source_id, item.get("set")))
                    provenance["set"]["retrospective" if version.retrospective else "observed"] += 1
            if side_order is None:
                exclusions["point_side_order_unknown"] += len(confirmed)
                continue
            source_point_events = [item for item in confirmed if item.get("quality") != "terminal_from_game_result"]
            signatures = Counter(_source_point_key(item, side_order) for item in source_point_events)
            for item in source_point_events:
                signature = _source_point_key(item, side_order)
                if signature is None or signatures[signature] != 1:
                    continue
                next_winner = item.get("winner") if side_order else 1 - item.get("winner")
                for snapshot_state, snapshot_id, snapshot_at in snapshots_by_event.get(event_id, []):
                    if snapshot_state == signature:
                        identity = (event_id, source_id, set_no, signature, next_winner)
                        aligned_label_keys["point"].add(identity)
                        record_example("point", identity, event_id, next_winner, snapshot_id,
                                       snapshot_at, version, source_id, item)

            # Set and match labels are attached only to real Fonbet snapshots in the event.
            for item in terminal_games:
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
                    "label_available_at": _utc_result_time(eligible[event_id]).isoformat()
                        if _utc_result_time(eligible[event_id]) else None,
                    "retrospective": False,
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
        window_report, manifest_metadata = _walk_forward_admission(
            target_rows, {key: value for key, value in result_times.items()
                          if value is not None and result_labels.get(key) is not None})
        report["training_gate"]["walk_forward"] = window_report
        report["training_gate"]["manifest"] = manifest_metadata
        example_rows = [example_provenance[key] for key in sorted(example_provenance, key=lambda item: (item[0], repr(item[1])))]
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
            not _has_result_availability(event) for event in eligible.values())
        report["training_gate"]["scheduled_time_used_as_chronology"] = sum(
            value is not None and not _has_result_availability(eligible[event_id])
            for event_id, value in result_times.items())
        report["training_gate"]["windows_evaluated"] = True
        windows_ok = all(item["folds_meet_minimums"] and item["holdout_meets_minimums"] for item in window_report.values())
        base_coverage_ok = all(
            report["targets"][name]["labelled_matches"] >= 500
            and report["targets"][name]["class_a"] > 0 and report["targets"][name]["class_b"] > 0
            for name in ("point", "game", "set", "match")
        )
        report["training_gate"]["base_coverage_status"] = "sufficient_for_window_assessment" if base_coverage_ok else "insufficient"
        reasons = report["training_gate"]["reasons"]
        if not base_coverage_ok:
            reasons.append("base_target_coverage_or_both_classes_missing")
        if not windows_ok:
            reasons.append("walk_forward_or_holdout_minimums_not_met")
        # This audit establishes label/split chronology. Certification of the
        # full feature vector is a separate gate and remains closed until every
        # feature has a verified source timestamp in the dataset builder.
        report["training_gate"]["point_in_time_certified"] = False
        reasons.append("odds_free_feature_availability_not_certified")
        if report["training_gate"]["result_availability_unknown_matches"]:
            reasons.append("fonbet_result_source_availability_missing")
        report["training_gate"]["status"] = (
            "eligible_for_training_review"
            if base_coverage_ok and windows_ok and report["training_gate"]["point_in_time_certified"]
            else "blocked"
        )
    return report
