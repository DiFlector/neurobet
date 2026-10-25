"""Link Fonbet tennis names to canonical players when the match is unambiguous.

Doubles and several canonical players with the same surname and initial are left
unlinked. Confidence is stored on participant_aliases.link_confidence.
Manual pairs live in sports/tennis/data/fonbet_name_overrides.csv.
"""

import csv
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sports" / "tennis" / "src"))

from tennis_adapter.external_names import choose_link, fold, initial_matches, surname_and_initial  # noqa: E402

OVERRIDES = ROOT / "sports" / "tennis" / "data" / "fonbet_name_overrides.csv"


def canonical_match_dates_query() -> str:
    """Return only survivor matches when building player activity windows."""
    return """
        SELECT winner_player_id::text, played_at FROM ext_tennis_canonical_matches
        WHERE winner_player_id IS NOT NULL AND duplicate_of_id IS NULL
        UNION ALL
        SELECT loser_player_id::text, played_at FROM ext_tennis_canonical_matches
        WHERE loser_player_id IS NOT NULL AND duplicate_of_id IS NULL
    """


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("IMPORT_PGHOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5435"),
        dbname=os.environ.get("POSTGRES_DB", "neurobet"),
        user=os.environ.get("POSTGRES_USER", "neurobet"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def distance(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > 1:
        return 9
    previous = list(range(len(right) + 1))
    for i, ch in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ch != other)))
        previous = current
    return previous[-1]


def gender_of(tournament: str) -> str:
    text = tournament or ""
    folded = text.lower()
    if "женщин" in folded or folded.startswith("wta"):
        return "w"
    if "мужчин" in folded or folded.startswith("atp") or "челлендж" in folded:
        return "m"
    return "unknown"


def level_of(tournament: str, name: str) -> str:
    if "/" in (name or ""):
        return "doubles"
    text = tournament or ""
    if text.startswith("World Tennis"):
        return "itf"
    if "Челлендж" in text or "125" in text:
        return "challenger"
    if text.startswith("ATP") or text.startswith("WTA") or text.startswith("Кубок"):
        return "atp_wta"
    if not text:
        return "unknown"
    return "other"


def load_overrides() -> dict:
    found = {}
    if not OVERRIDES.exists():
        return found
    with OVERRIDES.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("fonbet_name") or "").strip()
            player_id = (row.get("canonical_player_id") or "").strip()
            if name and player_id:
                found[fold(name)] = player_id
    return found


def main() -> None:
    load_env()
    connection = connect()
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT id::text, display_name, surname_key, initial, gender
        FROM ext_tennis_canonical_players
        """
    )
    players = {}
    by_key = defaultdict(dict)
    surnames = defaultdict(set)
    for player_id, display_name, surname, initial, gender in cursor.fetchall():
        players[player_id] = display_name
        if not surname:
            continue
        by_key[(surname, initial or "", gender or "unknown")][player_id] = display_name
        surnames[surname].add((initial or "", gender or "unknown"))
    if not players:
        raise SystemExit("Канонические игроки пусты. Сначала запустите build_canonical_tennis.py.")
    cursor.execute(canonical_match_dates_query())
    dates = defaultdict(set)
    for player_id, played in cursor.fetchall():
        moment = played if played.tzinfo else played.replace(tzinfo=timezone.utc)
        dates[player_id].add(moment.date())

    buckets = defaultdict(list)
    for surname in surnames:
        buckets[(surname[:2], len(surname))].append(surname)
    overrides = load_overrides()

    def options_for(raw_name: str, gender: str, moment: datetime, with_initial: bool):
        surname, initial = surname_and_initial(raw_name)
        if not surname or (with_initial and not initial):
            return []
        pool = []
        for length in (len(surname) - 1, len(surname), len(surname) + 1):
            pool.extend(buckets.get((surname[:2], length), []))
        window_start = (moment - timedelta(days=45)).date()
        window_end = (moment + timedelta(days=45)).date()
        genders = [gender] if gender in ("m", "w") else ["m", "w", "unknown"]
        found = []
        for other in pool:
            gap = 0 if other == surname else distance(surname, other)
            if gap > (0 if len(surname) < 6 else 1):
                continue
            for other_initial, other_gender in surnames[other]:
                if other_gender not in genders and other_gender != "unknown":
                    continue
                if with_initial and not initial_matches(initial, other_initial):
                    continue
                if not with_initial and other_initial == "":
                    continue
                for player_id in by_key.get((other, other_initial, other_gender), {}):
                    played = dates.get(player_id, set())
                    if any(window_start <= day <= window_end for day in played):
                        found.append((player_id, 0.92 if gap == 0 else 0.75))
        return found

    cursor.execute(
        """
        SELECT id::text, participant_a_name, participant_b_name, scheduled_start_at,
               COALESCE(metadata_json->>'raw_tournament', '')
        FROM events
        WHERE sport_code = 'tennis' AND status IN ('finished', 'completed')
        """
    )
    events = cursor.fetchall()
    linked_names = {}
    unmatched = Counter()
    coverage = defaultdict(lambda: {"matches": 0, "both": 0, "one": 0, "none": 0})
    for _event_id, name_a, name_b, started, tournament in events:
        level = level_of(tournament, name_a or "")
        if level == "doubles":
            coverage["doubles"]["matches"] += 1
            continue
        moment = started or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        gender = gender_of(tournament)
        resolved = []
        for raw_name in (name_a, name_b):
            override_id = overrides.get(fold(raw_name or ""))
            if override_id and override_id in players:
                resolved.append((raw_name, override_id, players[override_id], 0.99))
                linked_names[fold(raw_name)] = (players[override_id], 0.99)
                continue
            named = options_for(raw_name or "", gender, moment, True)
            surname_only = options_for(raw_name or "", gender, moment, False) if not surname_and_initial(raw_name or "")[1] else []
            choice = choose_link(named, surname_only)
            if choice is None:
                resolved.append(None)
                if raw_name:
                    unmatched[raw_name] += 1
                continue
            player_id, confidence = choice
            resolved.append((raw_name, player_id, players[player_id], confidence))
            linked_names[fold(raw_name)] = (players[player_id], confidence)
        coverage[level]["matches"] += 1
        hits = sum(item is not None for item in resolved)
        if hits == 2:
            coverage[level]["both"] += 1
        elif hits == 1:
            coverage[level]["one"] += 1
        else:
            coverage[level]["none"] += 1

    for alias, (canonical, confidence) in linked_names.items():
        cursor.execute(
            """
            SELECT id FROM participants
            WHERE sport_code = 'tennis' AND canonical_name = %s
            LIMIT 1
            """,
            (canonical[:255],),
        )
        row = cursor.fetchone()
        if row:
            participant_id = row[0]
        else:
            participant_id = str(uuid.uuid4())
            cursor.execute(
                """
                INSERT INTO participants (id, sport_code, canonical_name, is_team, metadata_json, created_at, updated_at)
                VALUES (%s, 'tennis', %s, false, %s::jsonb, now(), now())
                """,
                (participant_id, canonical[:255], '{"link_source":"external_tennis"}'),
            )
        cursor.execute(
            """
            SELECT id, participant_id FROM participant_aliases
            WHERE source = 'fonbet' AND alias = %s
            LIMIT 1
            """,
            (alias[:255],),
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                "UPDATE participant_aliases SET link_confidence = %s WHERE id = %s",
                (confidence, existing[0]),
            )
            cursor.execute(
                """
                UPDATE participants
                SET canonical_name = %s, updated_at = now()
                WHERE id = %s AND COALESCE(metadata_json->>'link_source', '') = 'external_tennis'
                """,
                (canonical[:255], existing[1]),
            )
            continue
        cursor.execute(
            """
            INSERT INTO participant_aliases (id, participant_id, source, alias, link_confidence, created_at)
            VALUES (%s, %s, 'fonbet', %s, %s, now())
            """,
            (str(uuid.uuid4()), participant_id, alias[:255], confidence),
        )
    connection.commit()
    print("coverage by level (singles):")
    for level, stats in sorted(coverage.items()):
        total = stats["matches"] or 1
        print(
            f"  {level}: matches={stats['matches']} both_linked={stats['both']} "
            f"({stats['both'] / total:.1%}) one_linked={stats['one']} none={stats['none']}"
        )
    print(f"aliases written or updated: {len(linked_names)}")
    print("frequent unlinked names:")
    for name, count in unmatched.most_common(25):
        print(f"  {count:4d}  {name}")
    cursor.close()
    connection.close()


if __name__ == "__main__":
    main()
