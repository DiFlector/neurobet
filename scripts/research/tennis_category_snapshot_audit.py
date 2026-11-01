"""Read-only point-in-time coverage and category consistency audit for tennis."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import re

from db.connection import SessionLocal
from db.models import Event, League
from db.models.external_tennis import (
    ExtTennisMatch, TennisPointHistoryVersion, TennisPointMatchLink,
)


def _category(text_value: str) -> str:
    value = str(text_value or "").casefold()
    if any(token in value for token in ("junior", "juniors", "юниор", "юнош", "u18", "u16", "u14", "boys", "girls")) \
            or re.search(r"\bj(?:30|60|100|200|300|500)\b", value):
        return "junior"
    if "challenger" in value or "челленджер" in value:
        return "challenger"
    if re.search(r"\bitf\b|\b[wm](15|25|35|50|60|75|100|125)\b|futures|фьючерс", value):
        return "itf"
    if re.search(r"\b(?:wta|women)\b", value) or "женщ" in value or "жен." in value:
        return "wta"
    if re.search(r"\b(?:atp|men|grand slam)\b", value) or "мужчин" in value or "муж." in value:
        return "atp"
    return "unknown"


def _source_category(level: str, gender: str) -> str:
    level, gender = str(level or "").casefold(), str(gender or "").casefold()
    if level == "atp_wta":
        return {"male": "atp", "female": "wta"}.get(gender, "unknown")
    if level == "challenger" and gender == "female":
        return "wta"
    return level if level in {"atp", "wta", "challenger", "itf", "junior"} else "unknown"


def _explicit_reference(tournament: str) -> tuple[str | None, str | None]:
    value = str(tournament or "").casefold()
    category = _category(value)
    if category == "unknown":
        return None, None
    gender = None
    if any(token in value for token in ("girls", "жен.")) or "женщ" in value or re.search(r"\b(?:women|wta)\b", value):
        gender = "female"
    elif any(token in value for token in ("boys", "муж.")) or "мужчин" in value or re.search(r"\b(?:men|atp)\b", value):
        gender = "male"
    else:
        age_code = re.search(r"\b([mw])(15|25|35|50|60|75|100|125)\b", value)
        if age_code:
            gender = "male" if age_code.group(1) == "m" else "female"
    return category, gender


def build_report() -> dict:
    now = datetime.now(timezone.utc)
    report: dict = {
        "generated_at": now.isoformat(),
        "scope": "read-only stored Fonbet events, Flashscore match rows, and point-history snapshots",
        "fonbet_categories": {},
        "pbp_versions_by_category": {},
        "flashscore_latest_category_snapshots": {},
        "flashscore_explicit_marker_consistency": {},
        "flashscore_category_mismatch_examples": [],
        "flashscore_gender_mismatch_examples": [],
        "limitations": [
            "explicit tournament-name markers check stored category consistency, not independent official adjudication",
            "archived source-page human review is required to certify category correctness",
            "Fonbet event competition names are not source-timed in the current event census",
        ],
    }
    with SessionLocal() as session:
        events = session.query(Event).filter(
            Event.sport_code == "tennis", Event.source == "fonbet",
        ).all()
        league_ids = {event.league_id for event in events if event.league_id}
        leagues = {row.id: row.name for row in session.query(League).filter(League.id.in_(league_ids)).all()} if league_ids else {}

        links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all()
        latest_link = {}
        for link in links:
            latest_link.setdefault((link.fonbet_event_id, link.source_match_id), link)
        linked_ids = defaultdict(set)
        linked_events_by_source = defaultdict(set)
        for (event_id, source_id), link in latest_link.items():
            if link.status in {"matched", "manual"}:
                linked_ids[event_id].add(source_id)
                linked_events_by_source[source_id].add(event_id)
        external = {}
        source_ids = {source_id for ids in linked_ids.values() for source_id in ids}
        if source_ids:
            for row in session.query(ExtTennisMatch).filter(
                ExtTennisMatch.source == "flashscore",
                ExtTennisMatch.source_match_id.in_(source_ids),
            ).all():
                external[row.source_match_id] = row

        event_category = {}
        category_source_counts = Counter()
        eligible_event_count = 0
        for event in events:
            metadata = event.metadata_json if isinstance(event.metadata_json, dict) else {}
            official = metadata.get("fonbet_official_result") if isinstance(metadata.get("fonbet_official_result"), dict) else {}
            competition = " ".join(str(x or "") for x in (
                official.get("tournament"), metadata.get("raw_tournament"), leagues.get(event.league_id),
            ))
            if str(event.source_event_id or "").isdigit() and "/" not in " ".join((event.participant_a_name or "", event.participant_b_name or "", competition)):
                eligible_event_count += 1
            source_ids = linked_ids.get(event.id, set())
            flash_tournament = ""
            if len(source_ids) == 1:
                flash_tournament = external.get(next(iter(source_ids))).tournament if next(iter(source_ids)) in external else ""
            explicit_meta = " ".join(str(official.get(key) or metadata.get(key) or "") for key in
                                     ("tour", "category", "circuit", "age_group", "tournament_type"))
            category = _category(" ".join((competition, explicit_meta, flash_tournament)))
            source = "fonbet_metadata" if _category(explicit_meta) != "unknown" else (
                "flashscore_archive" if _category(flash_tournament) != "unknown" else
                "fonbet_competition_name" if _category(competition) != "unknown" else "unknown"
            )
            event_category[event.id] = category
            category_source_counts[source] += 1
        report["fonbet_categories"] = {
            "events": len(events), "numeric_single_events": eligible_event_count,
            "category_source_counts": dict(category_source_counts),
            "by_category": dict(Counter(event_category.values())),
            "itf_or_junior_events": sum(value in {"itf", "junior"} for value in event_category.values()),
            "unknown_events": sum(value == "unknown" for value in event_category.values()),
        }

        pbp_rows = session.query(
            TennisPointHistoryVersion.fonbet_event_id,
            TennisPointHistoryVersion.source_match_id,
            TennisPointHistoryVersion.availability_status,
            TennisPointHistoryVersion.retrospective,
            TennisPointHistoryVersion.parser_version,
        ).filter(
            TennisPointHistoryVersion.available_at.is_not(None),
            TennisPointHistoryVersion.available_at <= now,
        ).all()
        pbp_by_category = defaultdict(lambda: {
            "versions": 0, "linked_events": set(), "observed_versions": 0,
            "retrospective_versions": 0, "unknown_availability_versions": 0,
            "source_matches": set(), "parser_versions": Counter(),
        })
        for event_id, source_match_id, availability_status, retrospective, parser_version in pbp_rows:
            if event_id is None and len(linked_events_by_source.get(source_match_id, set())) == 1:
                event_id = next(iter(linked_events_by_source[source_match_id]))
            category = event_category.get(event_id, "unknown")
            bucket = pbp_by_category[category]
            bucket["versions"] += 1
            bucket["source_matches"].add(source_match_id)
            if event_id:
                bucket["linked_events"].add(str(event_id))
            bucket["observed_versions"] += int(not retrospective)
            bucket["retrospective_versions"] += int(bool(retrospective))
            bucket["unknown_availability_versions"] += int(availability_status == "unknown_legacy")
            bucket["parser_versions"][parser_version] += 1
        report["pbp_versions_by_category"] = {
            category: {**{key: value for key, value in bucket.items() if key not in {"linked_events", "source_matches", "parser_versions"}},
                       "linked_events": len(bucket["linked_events"]),
                       "source_matches": len(bucket["source_matches"]),
                       "parser_versions": dict(bucket["parser_versions"])}
            for category, bucket in pbp_by_category.items()
        }

        flash_rows = session.query(
            ExtTennisMatch.source_match_id, ExtTennisMatch.tour_level, ExtTennisMatch.gender,
            ExtTennisMatch.tournament, ExtTennisMatch.played_at,
        ).filter(ExtTennisMatch.source == "flashscore").all()
        by_category = Counter()
        marker_confusion = Counter()
        category_marker_evaluated = 0
        category_marker_correct = 0
        gender_marker_evaluated = 0
        gender_marker_correct = 0
        gender_confusion = Counter()
        mismatch_examples = []
        gender_mismatch_examples = []
        itf_junior_known = Counter()
        flash_seasons = defaultdict(set)
        for source_match_id, level, gender, tournament, played_at in flash_rows:
            predicted = _source_category(level, gender)
            by_category[predicted] += 1
            if played_at:
                flash_seasons[predicted].add(played_at.year)
            expected, expected_gender = _explicit_reference(tournament or "")
            if expected:
                category_marker_evaluated += 1
                category_marker_correct += int(predicted == expected)
                marker_confusion[(expected, predicted)] += 1
                if predicted != expected and len(mismatch_examples) < 50:
                    mismatch_examples.append({
                        "source_match_id": source_match_id, "tournament": tournament,
                        "expected_from_explicit_name": expected, "stored_category": predicted,
                        "raw_tour_level": level, "gender": gender,
                    })
            if expected_gender:
                gender_marker_evaluated += 1
                stored_gender = str(gender or "").casefold()
                gender_marker_correct += int(stored_gender == expected_gender)
                gender_confusion[(expected_gender, stored_gender)] += 1
                if stored_gender != expected_gender and len(gender_mismatch_examples) < 100:
                    gender_mismatch_examples.append({
                        "source_match_id": source_match_id, "tournament": tournament,
                        "expected_from_explicit_name": expected_gender,
                        "stored_gender": stored_gender, "raw_tour_level": level,
                    })
            if expected in {"itf", "junior"}:
                itf_junior_known[(expected, predicted)] += 1
        report["flashscore_latest_category_snapshots"] = {
            "source": "current ext_tennis_matches materialization; one row per source match key",
            "unique_matches_with_snapshot": len(flash_rows),
            "latest_snapshot_categories": dict(by_category),
            "seasons_by_category": {key: sorted(value) for key, value in flash_seasons.items()},
            "itf_or_junior": by_category["itf"] + by_category["junior"],
            "unknown": by_category["unknown"],
        }
        report["flashscore_explicit_marker_consistency"] = {
            "category": {
                "evaluated_matches": category_marker_evaluated,
                "matched_explicit_name_marker": category_marker_correct,
                "accuracy_on_marker_subset": round(category_marker_correct / category_marker_evaluated, 6) if category_marker_evaluated else None,
                "confusion_expected_predicted": {f"{a}->{b}": n for (a, b), n in marker_confusion.items()},
                "gender_confusion_expected_stored": {f"{a}->{b}": n for (a, b), n in gender_confusion.items()},
                "itf_junior_expected_predicted": {f"{a}->{b}": n for (a, b), n in itf_junior_known.items()},
            },
            "gender": {
                "evaluated_matches": gender_marker_evaluated,
                "matched_explicit_name_marker": gender_marker_correct,
                "accuracy_on_marker_subset": round(gender_marker_correct / gender_marker_evaluated, 6) if gender_marker_evaluated else None,
            },
        }
        report["flashscore_category_mismatch_examples"] = mismatch_examples
        report["flashscore_gender_mismatch_examples"] = gender_mismatch_examples
    return report


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2, default=list))
