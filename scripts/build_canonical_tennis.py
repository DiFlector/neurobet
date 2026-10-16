"""Collapse raw ext_tennis_matches into one row per match.

Raw rows stay in place. Ratings and the comparison harness read
ext_tennis_canonical_matches. A compact copy is written to OnlineData/canonical.
"""

import csv
import gzip
import json
import os
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sports" / "tennis" / "src"))
sys.path.insert(0, str(ROOT / "services" / "neural"))

from tennis_adapter.external_names import surname_and_initial  # noqa: E402
from app.compare.canonical import (  # noqa: E402
    SACKMANN_SOURCES,
    RawMatch,
    cluster_matches,
    coarse_level,
    normalize_surface,
    pick_odds,
    pick_serve,
    side_key,
)

PLAYER_NS = uuid.UUID("6f1d8c2e-9a4b-5d7e-8c1a-0b2e4f6a8d10")
MATCH_NS = uuid.UUID("a31c0e74-5b29-4f6d-9e80-1c7d2a4b6e90")
OUT = ROOT / "OnlineData" / "canonical"


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


def _utc(moment):
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def load_matches(connection) -> list:
    cursor = connection.cursor(name="canonical_matches")
    cursor.itersize = 10000
    cursor.execute(
        """
        SELECT source, source_match_id, played_at, available_at, tour_level, tournament, surface,
               best_of, round_name, gender, winner_name, loser_name, winner_player_id, loser_player_id,
               winner_rank, loser_rank, score, odds_winner, odds_loser, serve_won_winner, serve_won_loser,
               return_won_winner, return_won_loser
        FROM ext_tennis_matches
        """
    )
    rows = []
    for item in cursor:
        rows.append(
            RawMatch(
                source=item[0],
                source_match_id=item[1],
                played_at=_utc(item[2]),
                available_at=_utc(item[3]),
                tour_level=item[4] or "unknown",
                tournament=item[5] or "",
                surface=normalize_surface(item[6]),
                best_of=int(item[7] or 3),
                round_name=item[8] or "",
                gender=item[9] or "unknown",
                winner_name=item[10] or "",
                loser_name=item[11] or "",
                winner_player_id=item[12],
                loser_player_id=item[13],
                winner_rank=item[14],
                loser_rank=item[15],
                score=item[16] or "",
                odds_winner=item[17],
                odds_loser=item[18],
                serve_won_winner=item[19],
                serve_won_loser=item[20],
                return_won_winner=item[21],
                return_won_loser=item[22],
            )
        )
    cursor.close()
    return rows


def load_people(connection) -> dict:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT source, source_player_id, full_name, gender, hand, ioc
        FROM ext_tennis_players
        """
    )
    found = {}
    for source, player_id, name, gender, hand, ioc in cursor.fetchall():
        found[(source, str(player_id))] = {
            "name": name or "",
            "gender": gender or "unknown",
            "hand": hand,
            "ioc": ioc,
        }
    cursor.close()
    return found


def _longest(names: list) -> str:
    cleaned = [name.strip() for name in names if name and name.strip()]
    if not cleaned:
        return ""
    return sorted(cleaned, key=lambda name: (name.count(" "), len(name)), reverse=True)[0]


def build_players(matches: list, people: dict) -> dict:
    """Map a stable identity string to a player record. Ids repeat across rebuilds."""
    records = {}
    for match in matches:
        for name, player_id, source in (
            (match.winner_name, match.winner_player_id, match.source),
            (match.loser_name, match.loser_player_id, match.source),
        ):
            identity = identity_of(name, player_id, source, match.gender)
            if not identity:
                continue
            record = records.get(identity)
            if record is None:
                record = {
                    "identity": identity,
                    "names": set(),
                    "gender": "unknown",
                    "hand": None,
                    "ioc": None,
                    "source_ids": {},
                }
                records[identity] = record
            if name:
                record["names"].add(name)
            if match.gender in ("m", "w"):
                record["gender"] = match.gender
            if source in SACKMANN_SOURCES and player_id:
                record["source_ids"][source] = str(player_id)
                meta = people.get((source, str(player_id)))
                if meta:
                    if meta["hand"]:
                        record["hand"] = meta["hand"]
                    if meta["ioc"]:
                        record["ioc"] = meta["ioc"]
                    if meta["gender"] in ("m", "w"):
                        record["gender"] = meta["gender"]
                    if meta["name"]:
                        record["names"].add(meta["name"])
    built = {}
    for identity, record in records.items():
        display = _longest(list(record["names"]))[:255]
        surname, initial = surname_and_initial(display)
        built[identity] = {
            "id": str(uuid.uuid5(PLAYER_NS, identity)),
            "display_name": display or identity[:255],
            "gender": record["gender"],
            "hand": record["hand"],
            "ioc": record["ioc"],
            "surname_key": surname[:128],
            "initial": (initial or "")[:8],
            "source_ids": record["source_ids"],
        }
    return built


def identity_of(name: str, player_id, source: str, gender: str) -> str:
    """One person keeps one key across Sackmann, TML and the odds files.

    The key is the surname and initial. Source ids stay in source_ids, because
    TML and the sqlite dump number the same player differently.
    """
    key = side_key(name)
    initial = key.split("|", 1)[1] if "|" in key else ""
    token = gender if gender in ("m", "w") else "unknown"
    if key and initial:
        return f"name:{key}:{token}"
    if source in SACKMANN_SOURCES and player_id:
        return f"id:{player_id}"
    if key:
        return f"name:{key}:{token}"
    return ""


def preferred_level(members) -> str:
    for member in members:
        if member.source in SACKMANN_SOURCES:
            return coarse_level(member.tour_level)
    return coarse_level(members[0].tour_level)


def preferred_text(members, field: str) -> str:
    for source in ("sackmann_sqlite", "tml", "ten", "atp_odds"):
        for member in members:
            if member.source == source and getattr(member, field):
                return getattr(member, field)
    return getattr(members[0], field) or ""


def main() -> None:
    load_env()
    connection = connect()
    print("loading raw matches", flush=True)
    matches = load_matches(connection)
    people = load_people(connection)
    print(f"raw matches={len(matches)} players={len(people)}", flush=True)
    by_source = defaultdict(int)
    for match in matches:
        by_source[match.source] += 1
    print("clustering", flush=True)
    groups = cluster_matches(matches)
    print(f"canonical matches={len(groups)}", flush=True)
    players = build_players(matches, people)
    from psycopg2.extras import Json, execute_values

    player_rows = [
        (
            item["id"],
            item["display_name"],
            item["gender"],
            item["hand"],
            item["ioc"],
            item["surname_key"],
            item["initial"],
            Json(item["source_ids"]),
        )
        for item in players.values()
    ]
    match_rows = []
    level_counts = defaultdict(int)
    for group in groups:
        odds_w, odds_l, odds_source = pick_odds(group)
        serve, stats_source = pick_serve(group)
        gender = next((member.gender for member in group if member.gender in ("m", "w")), group[0].gender or "unknown")
        winner_identity = ""
        loser_identity = ""
        winner_names = []
        loser_names = []

        def _prefer(current: str, new: str) -> str:
            if new.startswith("id:") and not current.startswith("id:"):
                return new
            return current or new

        for member in group:
            winner_names.append(member.winner_name)
            loser_names.append(member.loser_name)
            winner_identity = _prefer(
                winner_identity,
                identity_of(member.winner_name, member.winner_player_id, member.source, gender),
            )
            loser_identity = _prefer(
                loser_identity,
                identity_of(member.loser_name, member.loser_player_id, member.source, gender),
            )
        winner = players.get(winner_identity)
        loser = players.get(loser_identity)
        played = min(member.played_at for member in group)
        for source in ("atp_odds", "ten"):
            dated = [member.played_at for member in group if member.source == source]
            if dated:
                played = min(dated)
                break
        available = min(member.available_at for member in group)
        if available < played:
            available = played
        parts = sorted(f"{member.source}:{member.source_match_id}" for member in group)
        import hashlib

        digest = hashlib.sha1("\n".join(parts).encode()).hexdigest()
        level = preferred_level(group)
        level_counts[level] += 1
        ranks_w = [member.winner_rank for member in group if member.winner_rank]
        ranks_l = [member.loser_rank for member in group if member.loser_rank]
        match_rows.append(
            (
                str(uuid.uuid5(MATCH_NS, digest)),
                digest,
                played,
                available,
                level,
                preferred_text(group, "tournament")[:255],
                normalize_surface(preferred_text(group, "surface")),
                max(member.best_of or 3 for member in group),
                preferred_text(group, "round_name")[:32],
                gender,
                (winner["display_name"] if winner else _longest(winner_names))[:255],
                (loser["display_name"] if loser else _longest(loser_names))[:255],
                winner["id"] if winner else None,
                loser["id"] if loser else None,
                min(ranks_w) if ranks_w else None,
                min(ranks_l) if ranks_l else None,
                preferred_text(group, "score")[:64],
                odds_w,
                odds_l,
                None if serve is None else serve.serve_won_winner,
                None if serve is None else serve.serve_won_loser,
                None if serve is None else serve.return_won_winner,
                None if serve is None else serve.return_won_loser,
                odds_source,
                stats_source,
                Json({"sources": parts, "raw_levels": sorted({member.tour_level for member in group})}),
            )
        )
    cursor = connection.cursor()
    print("writing tables", flush=True)
    cursor.execute("TRUNCATE ext_tennis_canonical_matches, ext_tennis_canonical_players")
    execute_values(
        cursor,
        """
        INSERT INTO ext_tennis_canonical_players (
            id, display_name, gender, hand, ioc, surname_key, initial, source_ids
        ) VALUES %s
        """,
        player_rows,
        page_size=1000,
    )
    execute_values(
        cursor,
        """
        INSERT INTO ext_tennis_canonical_matches (
            id, canonical_key, played_at, available_at, tour_level, tournament, surface, best_of,
            round_name, gender, winner_name, loser_name, winner_player_id, loser_player_id,
            winner_rank, loser_rank, score, odds_winner, odds_loser, serve_won_winner, serve_won_loser,
            return_won_winner, return_won_loser, odds_source, stats_source, stats
        ) VALUES %s
        """,
        match_rows,
        page_size=1000,
    )
    connection.commit()
    cursor.close()
    connection.close()
    OUT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT / "players.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "display_name", "gender", "surname_key", "initial", "hand", "ioc"])
        for item in players.values():
            writer.writerow([item["id"], item["display_name"], item["gender"], item["surname_key"], item["initial"], item["hand"] or "", item["ioc"] or ""])
    with gzip.open(OUT / "matches.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "id", "played_at", "available_at", "tour_level", "tournament", "surface", "gender",
                "winner_name", "loser_name", "score", "odds_winner", "odds_loser", "odds_source", "stats_source",
            ]
        )
        for row in match_rows:
            writer.writerow([row[0], row[2].isoformat(), row[3].isoformat(), row[4], row[5], row[6], row[9], row[10], row[11], row[16], row[17] or "", row[18] or "", row[23] or "", row[24] or ""])
    manifest = {
        "raw_matches": len(matches),
        "canonical_matches": len(match_rows),
        "canonical_players": len(player_rows),
        "raw_by_source": dict(sorted(by_source.items())),
        "canonical_by_level": dict(sorted(level_counts.items())),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
