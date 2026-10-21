"""Write a point/game/set/match target coverage snapshot for the tennis audit."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from db.connection import SessionLocal
from db.models import Event, League, TennisPointHistoryVersion


def _finished_set(score):
    if not isinstance(score, list) or len(score) != 2: return False
    a, b = score
    high, low = max(a, b), min(a, b)
    return (high >= 6 and high - low >= 2) or (high == 7 and low == 6)


def build_report() -> dict:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_scope": "numeric Fonbet tennis event IDs; external histories are features only",
        "targets": {k: {"eligible_events": 0, "labelled_events": 0, "labels": 0,
                         "observed_events": 0, "retrospective_events": 0} for k in ("point", "game", "set", "match")},
        "training_label_alignment": {"point": 0, "game": 0, "set": 0, "match": 0,
            "status": "not_verified", "reason": "point documents do not carry per-point source timestamps; exact Fonbet state alignment must be implemented before using these parsed outcomes as training examples"},
        "by_competition": {},
    }
    with SessionLocal() as session:
        events = session.query(Event).filter(Event.sport_code == "tennis", Event.source == "fonbet").all()
        eligible = {event.id: event for event in events if str(event.source_event_id).isdigit()}
        categories = {}
        for event in eligible.values():
            league = session.get(League, event.league_id) if event.league_id else None
            categories[event.id] = league.name if league and league.name else "unknown"
            row = report["by_competition"].setdefault(categories[event.id], {k: 0 for k in ("events", "point_labels", "game_labels", "set_labels", "match_labels")})
            row["events"] += 1
            report["targets"]["match"]["eligible_events"] += 1
            if event.finished_at is not None:
                report["targets"]["match"]["labelled_events"] += 1
                report["targets"]["match"]["labels"] += 1
                row["match_labels"] += 1
        versions = session.query(TennisPointHistoryVersion).filter(TennisPointHistoryVersion.fonbet_event_id.in_(list(eligible))).all() if eligible else []
        latest_by_event_set = {}
        for version in sorted(versions, key=lambda v: v.retrieved_at):
            set_numbers = {item.get("set") for item in (version.events_json or []) if item.get("set") is not None}
            for set_no in set_numbers or {None}:
                latest_by_event_set[(version.fonbet_event_id, set_no)] = version
        target_events = {name: set() for name in ("point", "game", "set")}
        provenance = {name: {"observed": set(), "retrospective": set()} for name in ("point", "game", "set")}
        aggregate_labels = defaultdict(set)
        for (event_id, _), version in latest_by_event_set.items():
            event = eligible.get(event_id)
            if event is None: continue
            key = categories[event_id]
            items = version.events_json or []
            confirmed = [item for item in items if item.get("confirmed") and item.get("winner") in (0, 1)]
            games = [item for item in confirmed if item.get("quality") == "terminal_from_game_result"]
            completed_sets = set()
            for item in games:
                if _finished_set(item.get("game_score")) and item.get("set") is not None:
                    completed_sets.add(item["set"])
            if confirmed:
                target_events["point"].add(event_id); aggregate_labels["point"].update((event_id, i) for i, _ in enumerate(confirmed))
                report["by_competition"][key]["point_labels"] += len(confirmed)
            if games:
                target_events["game"].add(event_id); aggregate_labels["game"].update((event_id, i) for i, _ in enumerate(games))
                report["by_competition"][key]["game_labels"] += len(games)
            if completed_sets:
                target_events["set"].add(event_id); aggregate_labels["set"].update((event_id, no) for no in completed_sets)
                report["by_competition"][key]["set_labels"] += len(completed_sets)
            labels_for_version = {"point": bool(confirmed), "game": bool(games), "set": bool(completed_sets)}
            source_group = "retrospective" if version.retrospective else "observed"
            for target_name, has_labels in labels_for_version.items():
                if has_labels: provenance[target_name][source_group].add(event_id)
        for name in ("point", "game", "set"):
            item = report["targets"][name]
            item["eligible_events"] = len(eligible)
            item["labelled_events"] = len(target_events[name])
            item["labels"] = len(aggregate_labels[name])
            item["observed_events"] = len(provenance[name]["observed"])
            item["retrospective_events"] = len(provenance[name]["retrospective"])
    return report


def build_report() -> dict:
    from tennis_label_audit import build_report as build_label_audit
    return build_label_audit()


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
