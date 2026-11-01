"""Measure recoverable PBP coverage on complete saved Fonbet tapes, read-only.

This source-linked cohort scan writes exact matching snapshot keys and label
evidence. Feature PIT and chronological training admission remain independent.
One completed event is the resume unit; no global JSONB corpus scan is needed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
from types import SimpleNamespace

from sqlalchemy import text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from db.connection import SessionLocal
from db.models import (Event, EventStateSnapshot, TennisPointDocument,
                       TennisPointHistoryVersion, TennisPointMatchLink)
from tennis_adapter.point_history import is_authoritative_point_history_version
from tennis_label_audit import (
    _official_set_scores, _point_events_for_alignment, _snapshot_game_state,
    _history_versions_by_id,
    _load_direct_api_histories,
    _snapshot_matches_source_point, _snapshot_set_number, _snapshot_state,
    _source_point_key, _utc_result_time, _validated_terminal_game,
)

POLICY = "confirmed_pre_game_score_v1"


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_witness_cohort(completed, output_dir):
    """Small exact-key verification input; the full matched tapes stay intact."""
    rows = []
    counts = Counter()
    for event in sorted(completed, key=lambda row: row["fonbet_event_id"]):
        rows.append({"fonbet_event_id": event["fonbet_event_id"]})
        seen = set()
        with Path(event["index_path"]).open() as handle:
            for line in handle:
                row = json.loads(line)
                if not row.get("complete_point_state"):
                    continue
                new_targets = set(row["candidate_labels"]) - seen
                if new_targets:
                    rows.append(row)
                    counts.update(new_targets)
                    seen.update(new_targets)
                if seen == {"point", "game", "set"}:
                    break
    path = output_dir / "witness_snapshot_index.jsonl"
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temporary.replace(path)
    return {"path": str(path), "sha256": _digest(path), "rows": len(rows),
            "matches_with_complete_state_by_target": dict(counts),
            "purpose": "feature_PIT_verification_only_full_tapes_preserved_in_event_files"}


def _write_expanded_cohort(baseline_index, witness_index, output_dir):
    """Retain every baseline key, including result witnesses, and add new keys."""
    events, keys = set(), {}
    baseline_key_count = 0
    for index, source_path in enumerate((Path(baseline_index), Path(witness_index))):
        with source_path.open() as handle:
            for line in handle:
                row = json.loads(line)
                event_id = row["fonbet_event_id"]
                events.add(event_id)
                for source in (row, row.get("result_time_snapshot_evidence") or {}):
                    snapshot_id = source.get("snapshot_id")
                    raw_at = source.get("snapshot_observed_at") or source.get("observed_at")
                    if not snapshot_id or not raw_at:
                        continue
                    observed_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
                    if observed_at.tzinfo is None:
                        raise ValueError("snapshot_index_timestamp_without_timezone")
                    key = (snapshot_id, observed_at.astimezone(timezone.utc).isoformat())
                    if key in keys and keys[key] != event_id:
                        raise ValueError("snapshot_index_conflicting_event_id")
                    keys[key] = event_id
        if index == 0:
            baseline_key_count = len(keys)
    path = output_dir / "expanded_snapshot_index.jsonl"
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        for event_id in sorted(events):
            handle.write(json.dumps({"fonbet_event_id": event_id}, sort_keys=True) + "\n")
        for (snapshot_id, at), event_id in sorted(keys.items()):
            handle.write(json.dumps({"fonbet_event_id": event_id, "snapshot_id": snapshot_id,
                                     "snapshot_observed_at": at}, sort_keys=True) + "\n")
    temporary.replace(path)
    return {"path": str(path), "sha256": _digest(path), "unique_events": len(events),
            "snapshot_keys": len(keys), "baseline_snapshot_keys": baseline_key_count,
            "added_snapshot_keys": len(keys) - baseline_key_count}


def _session():
    session = SessionLocal()
    session.execute(text("SET TRANSACTION READ ONLY"))
    session.execute(text("SET LOCAL statement_timeout=30000"))
    return session


def _bounded_read(loader, limit):
    retries = 0
    while True:
        try:
            return loader(limit), limit, retries
        except SQLAlchemyError as exc:
            if "statement timeout" not in str(getattr(exc, "orig", "")) or limit <= 100:
                raise
            limit = max(100, limit // 2)
            retries += 1


def _snapshot_page(event, cutoff, cursor, limit):
    with _session() as session:
        query = session.query(EventStateSnapshot.id, EventStateSnapshot.observed_at,
            EventStateSnapshot.event_id, EventStateSnapshot.current_period,
            EventStateSnapshot.score_detail, EventStateSnapshot.sport_state).filter(
                EventStateSnapshot.event_id == event.id,
                EventStateSnapshot.sport_code == "tennis", EventStateSnapshot.status == "live",
                EventStateSnapshot.observed_at >= event.first_seen_at,
                EventStateSnapshot.observed_at <= cutoff)
        if cursor:
            query = query.filter(tuple_(EventStateSnapshot.observed_at, EventStateSnapshot.id)
                                 > tuple_(*cursor))
        return query.order_by(EventStateSnapshot.observed_at, EventStateSnapshot.id).limit(limit).all()


def _label_maps(versions, orientation, scores, linked_at, result_at):
    points, games = defaultdict(list), defaultdict(list)
    latest = {}
    for version in sorted(versions, key=lambda v: (v.available_at, str(v.id))):
        if not is_authoritative_point_history_version(version.events_json, version.quality_json):
            continue
        sets = {row.get("set") for row in version.events_json or [] if type(row.get("set")) is int}
        if (version.quality_json or {}).get("set_number"):
            sets.add(version.quality_json["set_number"])
        for number in sets:
            latest[number] = version
    for number, version in latest.items():
        rows = [row for row in _point_events_for_alignment(version.events_json or [], version.parser_version)
                if row.get("set") == number]
        available = max(value for value in (version.available_at, linked_at, result_at) if value is not None)
        evidence = {"point_version_id": str(version.id), "point_document_id": str(version.document_id),
                    "label_available_at": available.isoformat()}
        confirmed = [row for row in rows if row.get("confirmed") is True
                     and type(row.get("winner")) is int and row["winner"] in (0, 1)
                     and row.get("quality") not in {"terminal_from_game_result", "terminal_from_tiebreak_result", "confirmed_game_result"}]
        signatures = Counter(_source_point_key(row, orientation) for row in confirmed)
        for row in confirmed:
            signature = _source_point_key(row, orientation)
            if signature is None or signatures[signature] != 1:
                continue
            label = row["winner"] if orientation else 1 - row["winner"]
            key = (signature[0], signature[1], signature[2], signature[4])
            points[key].append((signature, {**evidence, "label": label}))
        for row in rows:
            terminal = _validated_terminal_game(row, orientation, scores)
            if terminal is not None:
                set_no, before, winner, _ = terminal
                games[(set_no, before)].append({**evidence, "label": winner})
    return points, games, len(latest)


def _one_label(candidates, snapshot_at):
    candidates = [row for row in candidates
                  if snapshot_at < datetime.fromisoformat(row["label_available_at"])]
    if not candidates or len({row["label"] for row in candidates}) != 1:
        return None
    return min(candidates, key=lambda row: (row["label_available_at"], row.get("point_version_id", "")))


def scan_event(event, link, *, cutoff, page_size, baseline_keys, output_dir, version_ids):
    scores, reason = _official_set_scores(event)
    source = getattr(link, 'source', 'flashscore')
    result = {"fonbet_event_id": str(event.id), "fonbet_source_event_id": event.source_event_id,
              "source": source, "source_match_id": getattr(link,'source_raw_id',link.source_match_id),
              "match_link_id": str(link.id) if source == 'flashscore' else None,
              "history_link_version_id": str(link.id) if source == 'api_tennis' else None,
              "link_available_at": link.available_at.isoformat(), "snapshots_scanned": 0,
              "aligned_examples": {target: 0 for target in ("point", "game", "set")},
              "baseline_aligned_examples": {target: 0 for target in ("point", "game", "set")}}
    index_path = output_dir / "events" / f"{event.id}.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if scores is None:
        result["reason"] = reason
        index_path.write_text("")
    else:
        with _session() as session:
            versions = list(_history_versions_by_id(session, sorted(version_ids, key=str)))
        result_at = _utc_result_time(event)
        if result_at is not None and result_at > cutoff:
            result["reason"] = "result_after_cutoff"
            index_path.write_text("")
        else:
            points, games, sets = _label_maps(versions, link.evidence_json["external_home_is_fonbet_a"],
                                            scores, link.available_at, result_at)
            result.update(authoritative_sets=sets, result_time_known=result_at is not None)
            cursor = None
            # Snapshot JSON is larger than the metadata index rows. Keep a
            # smaller payload bound and retry timed-out pages at a lower size.
            effective_page_size = min(page_size, 500)
            result["snapshot_query_retries"] = 0
            temporary = index_path.with_suffix(".tmp")
            with temporary.open("w") as handle:
                while True:
                    rows, effective_page_size, retries = _bounded_read(
                        lambda limit: _snapshot_page(event, cutoff, cursor, limit), effective_page_size)
                    result["snapshot_query_retries"] += retries
                    result["effective_page_size"] = effective_page_size
                    if not rows:
                        break
                    for row in rows:
                        result["snapshots_scanned"] += 1
                        labels = {}
                        state = _snapshot_state(row)
                        game_state = _snapshot_game_state(row)
                        if state is not None:
                            key = (state[0], state[1], state[2], state[4])
                            candidates = [label for signature, label in points.get(key, [])
                                          if _snapshot_matches_source_point(state, signature)]
                            label = _one_label(candidates, row.observed_at)
                            if label:
                                labels["point"] = label
                        if game_state is not None:
                            label = _one_label(games.get(game_state, []), row.observed_at)
                            if label:
                                labels["game"] = label
                        number = _snapshot_set_number(row)
                        if (result_at is not None and row.observed_at < result_at
                                and number is not None and 1 <= number <= len(scores)
                                and game_state is not None and game_state[1] != tuple(scores[number - 1])
                                and all(value <= final for value, final in zip(game_state[1], scores[number - 1]))):
                            labels["set"] = {"label": int(scores[number - 1][1] > scores[number - 1][0]),
                                             "label_available_at": result_at.isoformat(), "source": "fonbet_official_result"}
                        if labels:
                            is_baseline = (str(row.id), row.observed_at.isoformat()) in baseline_keys
                            for target in labels:
                                result["aligned_examples"][target] += 1
                                result["baseline_aligned_examples"][target] += int(is_baseline)
                            index_row = {"fonbet_event_id": str(event.id), "snapshot_id": str(row.id),
                                "snapshot_observed_at": row.observed_at.isoformat(),
                                "candidate_labels": labels, "match_link_id": result['match_link_id'],
                                "history_link_version_id": result['history_link_version_id'], "source": source,
                                "complete_point_state": state is not None}
                            handle.write(json.dumps(index_row, sort_keys=True) + "\n")
                    cursor = (rows[-1].observed_at, rows[-1].id)
                    if len(rows) < effective_page_size:
                        break
            temporary.replace(index_path)
            result["reason"] = "aligned_candidate" if any(result["aligned_examples"].values()) else "no_exact_state_match"
    result.update(index_path=str(index_path), index_sha256=_digest(index_path))
    return result


def run(*, cutoff, output_dir, baseline_index, resume=False, page_size=5000, source='flashscore', event_ids=None):
    if cutoff.tzinfo is None or not 1 <= page_size <= 5000:
        raise ValueError("invalid_cutoff_or_page_size")
    if source == 'api_tennis' and not event_ids:
        raise ValueError("api_tennis_requires_explicit_event_ids")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_index = Path(baseline_index)
    config = {"cutoff": cutoff.isoformat(), "policy": POLICY, "baseline_sha256": _digest(baseline_index),
              "page_size": page_size, "version_lookup": "confirmed_source_match_and_document_v2", "source": source,
              "event_ids": sorted(str(value) for value in (event_ids or []))}
    baseline_keys = set()
    for line in baseline_index.read_text().splitlines():
        row = json.loads(line)
        if row.get("snapshot_id"):
            at = datetime.fromisoformat(row["snapshot_observed_at"].replace("Z", "+00:00"))
            baseline_keys.add((row["snapshot_id"], at.isoformat()))
    cp_path = output_dir / "checkpoint.json"
    if cp_path.exists():
        checkpoint = json.loads(cp_path.read_text())
        if not resume or checkpoint["config"] != config:
            raise ValueError("checkpoint_requires_resume_with_identical_input")
        for row in checkpoint["completed"]:
            if _digest(Path(row["index_path"])) != row["index_sha256"]:
                raise ValueError("checkpoint_artifact_digest_mismatch")
    else:
        checkpoint = {"config": config, "completed": []}
    with _session() as session:
        if source == 'api_tennis':
            # Constrain by Fonbet event IDs first. The history table's composite
            # index starts with fonbet_event_id; scanning all source versions by
            # parser_version can exceed the bounded query timeout.
            requested_ids = list(event_ids or [])
            event_query = session.query(Event).filter(Event.created_at <= cutoff,
                Event.source == "fonbet", Event.sport_code == "tennis")
            if requested_ids:
                event_query = event_query.filter(Event.id.in_(requested_ids))
            events = event_query.all()
            ids = session.query(TennisPointHistoryVersion.fonbet_event_id).filter(
                TennisPointHistoryVersion.fonbet_event_id.in_([event.id for event in events]),
                TennisPointHistoryVersion.parser_version == 'api_tennis_pbp_link_v2',
                TennisPointHistoryVersion.available_at <= cutoff).distinct().all() if events else []
            event_set = {row[0] for row in ids}
            events = [event for event in events if event.id in event_set]
            contexts = _load_direct_api_histories(session, {event.id:event for event in events}, cutoff)
            by_event, version_ids_by_source = defaultdict(list), defaultdict(list)
            for context in contexts:
                version = context['version']
                link = SimpleNamespace(id=version.id, fonbet_event_id=version.fonbet_event_id,
                    source_match_id=context['reference'], source_raw_id=context['source_match_id'],
                    available_at=version.available_at, source='api_tennis',
                    evidence_json={'external_home_is_fonbet_a':True})
                by_event[version.fonbet_event_id].append(link)
                version_ids_by_source[context['reference']].append(version.id)
            events = [event for event in events if event.id in by_event]
        else:
            by_event, events, version_ids_by_source = _flashscore_sources(session, cutoff)
    done = {row["fonbet_event_id"] for row in checkpoint["completed"]}
    status = "complete"
    try:
        for event in sorted(events, key=lambda row: str(row.id)):
            if str(event.id) in done or len(by_event[event.id]) != 1:
                continue
            result = scan_event(event, by_event[event.id][0], cutoff=cutoff, page_size=page_size,
                                baseline_keys=baseline_keys, output_dir=output_dir,
                                version_ids=version_ids_by_source[by_event[event.id][0].source_match_id])
            checkpoint["completed"].append(result)
            _json(cp_path, checkpoint)
            print(json.dumps({"completed": len(checkpoint["completed"]), "event": event.source_event_id,
                              "examples": result["aligned_examples"]}), flush=True)
    except (TimeoutError, SQLAlchemyError) as exc:
        status = "incomplete"
        checkpoint["last_error"] = {"type": type(exc).__name__,
            "statement": str(getattr(exc, "statement", ""))[:250],
            "message": str(getattr(exc, "orig", exc))[:180]}
        _json(cp_path, checkpoint)
    report = {"status": status, "scope": "confirmed_oriented_source_links_only", "source": source,
              "alignment_policy": POLICY, "cutoff": cutoff.isoformat(),
              "source_linked_events": len(by_event), "candidate_events": len(events),
              "ambiguous_multi_source_events": sum(len(value) != 1 for value in by_event.values()),
              "completed_events": len(checkpoint["completed"]),
              "last_error": checkpoint.get("last_error") if status == "incomplete" else None,
              "snapshots_scanned": sum(row["snapshots_scanned"] for row in checkpoint["completed"]),
              "training_gate": {"status": "blocked", "reason": "coverage_scan_only_not_feature_or_temporal_admission"},
              "targets": {}}
    for target in ("point", "game", "set"):
        full = [row["fonbet_event_id"] for row in checkpoint["completed"] if row["aligned_examples"][target]]
        baseline = [row["fonbet_event_id"] for row in checkpoint["completed"] if row["baseline_aligned_examples"][target]]
        report["targets"][target] = {"full_tape_matches": len(full), "same_policy_baseline_matches": len(baseline),
                                    "added_by_full_tape": len(set(full) - set(baseline)),
                                    "event_ids": full, "new_event_ids": sorted(set(full) - set(baseline))}
    report["reason_counts"] = dict(Counter(row["reason"] for row in checkpoint["completed"]))
    report["events"] = checkpoint["completed"]
    report["witness_cohort"] = _write_witness_cohort(checkpoint["completed"], output_dir)
    report["expanded_cohort"] = _write_expanded_cohort(
        baseline_index, Path(report["witness_cohort"]["path"]), output_dir)
    _json(output_dir / "report.json", report)
    return report


def _flashscore_sources(session, cutoff):
    all_links = session.query(TennisPointMatchLink).filter(
        TennisPointMatchLink.available_at <= cutoff).order_by(
            TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
    latest = {link.source_match_id: link for link in all_links}
    links = [link for link in latest.values() if link.status in ("matched", "manual")
             and isinstance((link.evidence_json or {}).get("external_home_is_fonbet_a"), bool)]
    by_event = defaultdict(list)
    for link in links:
        by_event[link.fonbet_event_id].append(link)
    events = session.query(Event).filter(Event.id.in_(list(by_event)), Event.created_at <= cutoff,
                                         Event.source == "fonbet", Event.sport_code == "tennis").all()
    # A confirmed source link may have moved to another Fonbet event
    # version. Match raw source IDs through the link, not the historical
    # version's old fonbet_event_id. Load JSON only by these exact PKs.
    keys = session.query(TennisPointHistoryVersion.id, TennisPointHistoryVersion.source_match_id).join(
        TennisPointDocument, TennisPointDocument.id == TennisPointHistoryVersion.document_id).filter(
            TennisPointHistoryVersion.source_match_id.in_([link.source_match_id for link in links]),
            TennisPointDocument.source == "flashscore",
            TennisPointHistoryVersion.available_at <= cutoff,
            TennisPointDocument.available_at <= cutoff,
        ).all()
    version_ids_by_source = defaultdict(list)
    for version_id, source_id in keys:
        version_ids_by_source[source_id].append(version_id)
    return by_event, events, version_ids_by_source


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--baseline-index", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--source", choices=("flashscore", "api_tennis"), default="flashscore")
    parser.add_argument("--event-id", action="append", default=[], help="Bound API history lookup to explicit Fonbet event UUIDs")
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--max-runtime-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.source == "api_tennis" and not args.event_id:
        parser.error("--source api_tennis requires one or more --event-id values")
    if not 1 <= args.max_runtime_seconds <= 900:
        parser.error("max-runtime-seconds must be in 1..900")
    def budget_expired(_signum, _frame):
        raise TimeoutError("coverage_scan_runtime_timeout")
    signal.signal(signal.SIGALRM, budget_expired)
    signal.alarm(args.max_runtime_seconds)
    try:
        report = run(cutoff=datetime.fromisoformat(args.cutoff.replace("Z", "+00:00")),
                     output_dir=args.output_dir, baseline_index=args.baseline_index,
                     resume=args.resume, page_size=args.page_size, source=args.source, event_ids=args.event_id)
    finally:
        signal.alarm(0)
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, sort_keys=True))
    raise SystemExit(0 if report["status"] == "complete" else 2)
