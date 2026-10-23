"""Load external tennis history into PostgreSQL.

Sources live under OnlineData/raw. Priority when the same match appears twice:
raw/ten season files, raw/odds/atp_tennis.csv, raw/tml, the sqlite Sackmann dump
in raw/sqlite, then charted serve logs in raw/charting. raw/odds/atp_matches_2000.csv
and raw/sackmann match files repeat the sqlite dump and are not loaded again.
Rankings come from the sqlite dump and from raw/sackmann/atp_rankings_current.csv
when it extends the dump. The working copy is OnlineData/canonical, built by
scripts/build_canonical_tennis.py.

A result is visible only at available_at. ten/ uses the listed start plus six
hours. A file that only has the tournament week becomes visible at the end of
that week. A file with a calendar day becomes visible at the end of that day.

License of the Sackmann / Tennis Abstract files: CC BY-NC-SA 4.0.
"""

import csv
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sports" / "tennis" / "src"))

from tennis_adapter.external_names import dedupe_token  # noqa: E402

DATA = ROOT / "OnlineData" / "raw"
SURFACES = {"hard", "clay", "grass", "carpet"}
SQLITE_LEVELS = {"S", "C", "A", "G", "M", "D", "R", "L", "F", "U", "15", "25"}
INSERT_MATCH = """
INSERT INTO ext_tennis_matches (
    id, source, source_match_id, played_at, available_at, tour_level, tournament,
    surface, best_of, round_name, gender, winner_name, loser_name,
    winner_player_id, loser_player_id, winner_rank, loser_rank, score,
    odds_winner, odds_loser, serve_won_winner, serve_won_loser,
    return_won_winner, return_won_loser, stats
) VALUES %s
ON CONFLICT (source, source_match_id) DO NOTHING
"""
INSERT_PLAYER = """
INSERT INTO ext_tennis_players (id, source, source_player_id, full_name, gender, hand, ioc)
VALUES %s
ON CONFLICT (source, source_player_id) DO NOTHING
"""
INSERT_RANK = """
INSERT INTO ext_tennis_rankings (tour, ranking_date, source_player_id, rank, points)
VALUES %s
ON CONFLICT (tour, ranking_date, source_player_id) DO NOTHING
"""
INSERT_SERVE = """
INSERT INTO ext_tennis_serve_logs (
    id, source, source_match_id, player_name, source_event_at, available_at,
    availability_provenance, source_confidence, retrospective,
    surface, serve_points, serve_points_won
) VALUES %s
ON CONFLICT (source, source_match_id, player_name) DO NOTHING
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

    host = os.environ.get("IMPORT_PGHOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5435")
    return psycopg2.connect(
        host=host,
        port=port,
        dbname=os.environ.get("POSTGRES_DB", "neurobet"),
        user=os.environ.get("POSTGRES_USER", "neurobet"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def from_yyyymmdd(value, end_of_week: bool = False):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    text = f"{number:08d}"
    try:
        day = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if not (datetime(1968, 1, 1, tzinfo=timezone.utc) <= day <= datetime(2027, 1, 1, tzinfo=timezone.utc)):
        return None
    played = day
    available = day + timedelta(days=7 if end_of_week else 1)
    return played, available


def surface_of(value: str) -> str:
    text = (value or "").strip().lower()
    if text in SURFACES:
        return text
    return "unknown"


def rank_of(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > 5000:
        return None
    return number


def odds_of(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        return None
    return number


def ratio(won, points):
    try:
        total = float(points)
        made = float(won)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return max(0.0, min(1.0, made / total))


def percent_ratio(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > 100:
        return None
    return number / 100.0


class SeenMatches:
    def __init__(self):
        self.keys = set()

    def remember(self, winner: str, loser: str, surface: str, played: datetime) -> None:
        left, right = sorted((dedupe_token(winner), dedupe_token(loser)))
        if not left or not right:
            return
        year, week, _day = played.isocalendar()
        for shift in (-1, 0, 1):
            self.keys.add((left, right, surface, year, week + shift))

    def known(self, winner: str, loser: str, surface: str, played: datetime) -> bool:
        left, right = sorted((dedupe_token(winner), dedupe_token(loser)))
        year, week, _day = played.isocalendar()
        return (left, right, surface, year, week) in self.keys


def chunks(rows, size=2000):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def write_rows(cur, sql, rows) -> int:
    from psycopg2.extras import execute_values

    count = 0
    for batch in chunks(rows):
        execute_values(cur, sql, batch, page_size=1000)
        count += len(batch)
    return count


def player_row(source, player_id, name, gender, hand="", ioc=""):
    if not player_id or not name:
        return None
    return (str(uuid.uuid4()), source, str(player_id)[:64], str(name)[:255], gender, (hand or None), (ioc or None))


def match_row(**fields):
    return (
        str(uuid.uuid4()),
        fields["source"],
        str(fields["source_match_id"])[:128],
        fields["played_at"],
        fields["available_at"],
        fields.get("tour_level") or "unknown",
        (fields.get("tournament") or "")[:255],
        fields.get("surface") or "unknown",
        int(fields.get("best_of") or 3),
        (fields.get("round_name") or "")[:32],
        fields.get("gender") or "unknown",
        fields["winner_name"][:255],
        fields["loser_name"][:255],
        fields.get("winner_player_id"),
        fields.get("loser_player_id"),
        fields.get("winner_rank"),
        fields.get("loser_rank"),
        (fields.get("score") or "")[:64],
        fields.get("odds_winner"),
        fields.get("odds_loser"),
        fields.get("serve_won_winner"),
        fields.get("serve_won_loser"),
        fields.get("return_won_winner"),
        fields.get("return_won_loser"),
        __import__("psycopg2").extras.Json(fields.get("stats") or {}),
    )


def import_ten(seen: SeenMatches):
    players, matches = [], []
    for path in sorted((DATA / "ten").glob("*-season.csv")):
        gender = "w" if "wta" in path.name else "m"
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if (row.get("status") or "").upper() != "FINISHED":
                    continue
                try:
                    started = datetime.fromtimestamp(int(row["date_timestamp"]), tz=timezone.utc)
                except (TypeError, ValueError, KeyError, OSError):
                    continue
                code = str(row.get("winner_code") or "")
                if code == "1":
                    winner, loser = row.get("home_name"), row.get("away_name")
                    winner_id, loser_id = row.get("home_id"), row.get("away_id")
                    winner_rank, loser_rank = rank_of(row.get("home_rank")), rank_of(row.get("away_rank"))
                    odds_w, odds_l = odds_of(row.get("home_odds_match_winner")), odds_of(row.get("away_odds_match_winner"))
                    serve_w = percent_ratio(row.get("home_service_points_won_perc"))
                    serve_l = percent_ratio(row.get("away_service_points_won_perc"))
                    ret_w = percent_ratio(row.get("home_return_points_won_perc"))
                    ret_l = percent_ratio(row.get("away_return_points_won_perc"))
                elif code == "2":
                    winner, loser = row.get("away_name"), row.get("home_name")
                    winner_id, loser_id = row.get("away_id"), row.get("home_id")
                    winner_rank, loser_rank = rank_of(row.get("away_rank")), rank_of(row.get("home_rank"))
                    odds_w, odds_l = odds_of(row.get("away_odds_match_winner")), odds_of(row.get("home_odds_match_winner"))
                    serve_w = percent_ratio(row.get("away_service_points_won_perc"))
                    serve_l = percent_ratio(row.get("home_service_points_won_perc"))
                    ret_w = percent_ratio(row.get("away_return_points_won_perc"))
                    ret_l = percent_ratio(row.get("home_return_points_won_perc"))
                else:
                    continue
                if not winner or not loser or "/" in winner or "/" in loser:
                    continue
                surface = surface_of(row.get("surface"))
                level = (row.get("tour_type_human") or "unknown").lower().replace(" ", "_")
                seen.remember(winner, loser, surface, started)
                matches.append(match_row(
                    source="ten",
                    source_match_id=row.get("match_id") or f"{path.name}-{len(matches)}",
                    played_at=started,
                    available_at=started + timedelta(hours=6),
                    tour_level=level[:32],
                    tournament=row.get("tournament") or "",
                    surface=surface,
                    best_of=3,
                    round_name=row.get("round") or "",
                    gender=gender,
                    winner_name=winner,
                    loser_name=loser,
                    winner_player_id=str(winner_id or "")[:64] or None,
                    loser_player_id=str(loser_id or "")[:64] or None,
                    winner_rank=winner_rank,
                    loser_rank=loser_rank,
                    score=f"{row.get('home_set_score') or ''}-{row.get('away_set_score') or ''}",
                    odds_winner=odds_w,
                    odds_loser=odds_l,
                    serve_won_winner=serve_w,
                    serve_won_loser=serve_l,
                    return_won_winner=ret_w,
                    return_won_loser=ret_l,
                ))
                for pid, name in ((winner_id, winner), (loser_id, loser)):
                    item = player_row("ten", pid, name, gender)
                    if item:
                        players.append(item)
    return players, matches


def import_atp_odds(seen: SeenMatches):
    path = DATA / "odds" / "atp_tennis.csv"
    matches = []
    if not path.exists():
        return matches
    with path.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            try:
                played = datetime.strptime(row["Date"][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, KeyError):
                continue
            winner = (row.get("Winner") or "").strip()
            left, right = (row.get("Player_1") or "").strip(), (row.get("Player_2") or "").strip()
            if winner not in (left, right) or not left or not right:
                continue
            loser = right if winner == left else left
            if "/" in winner or "/" in loser:
                continue
            surface = surface_of(row.get("Surface"))
            if seen.known(winner, loser, surface, played):
                continue
            if winner == left:
                odds_w, odds_l = odds_of(row.get("Odd_1")), odds_of(row.get("Odd_2"))
                rank_w, rank_l = rank_of(row.get("Rank_1")), rank_of(row.get("Rank_2"))
            else:
                odds_w, odds_l = odds_of(row.get("Odd_2")), odds_of(row.get("Odd_1"))
                rank_w, rank_l = rank_of(row.get("Rank_2")), rank_of(row.get("Rank_1"))
            seen.remember(winner, loser, surface, played)
            try:
                best_of = int(float(row.get("Best of") or 3))
            except ValueError:
                best_of = 3
            matches.append(match_row(
                source="atp_odds",
                source_match_id=f"{row['Date']}-{index}",
                played_at=played,
                available_at=played + timedelta(days=1),
                tour_level=(row.get("Series") or "atp")[:32],
                tournament=row.get("Tournament") or "",
                surface=surface,
                best_of=best_of,
                round_name=row.get("Round") or "",
                gender="m",
                winner_name=winner,
                loser_name=loser,
                winner_rank=rank_w,
                loser_rank=rank_l,
                score=row.get("Score") or "",
                odds_winner=odds_w,
                odds_loser=odds_l,
            ))
    return matches


def _sackmann_row(row, source, gender, end_of_week, seen: SeenMatches, source_match_id: str):
    winner, loser = (row.get("winner_name") or "").strip(), (row.get("loser_name") or "").strip()
    if not winner or not loser or "/" in winner or "/" in loser:
        return None
    parsed = from_yyyymmdd(row.get("tourney_date"), end_of_week=end_of_week)
    if parsed is None:
        return None
    played, available = parsed
    surface = surface_of(row.get("surface"))
    if seen.known(winner, loser, surface, played):
        return None
    seen.remember(winner, loser, surface, played)
    def _points(first, second, total):
        try:
            return ratio((float(first or 0) + float(second or 0)), total) if total else None
        except (TypeError, ValueError):
            return None

    serve_w = _points(row.get("w_1stWon"), row.get("w_2ndWon"), row.get("w_svpt"))
    serve_l = _points(row.get("l_1stWon"), row.get("l_2ndWon"), row.get("l_svpt"))
    try:
        best_of = int(float(row.get("best_of") or 3))
    except (TypeError, ValueError):
        best_of = 3
    level = "wta" if gender == "w" else "atp"
    name = source
    if "challenger" in source_match_id or (row.get("tourney_level") or "") in ("C", "challenger"):
        level = "wta_challenger" if gender == "w" else "challenger"
    if "quali" in source_match_id:
        level = "qualifying"
    return match_row(
        source=name,
        source_match_id=source_match_id,
        played_at=played,
        available_at=available,
        tour_level=level,
        tournament=row.get("tourney_name") or "",
        surface=surface,
        best_of=best_of,
        round_name=row.get("round") or "",
        gender=gender,
        winner_name=winner,
        loser_name=loser,
        winner_player_id=str(row.get("winner_id") or "")[:64] or None,
        loser_player_id=str(row.get("loser_id") or "")[:64] or None,
        winner_rank=rank_of(row.get("winner_rank")),
        loser_rank=rank_of(row.get("loser_rank")),
        score=row.get("score") or "",
        serve_won_winner=serve_w,
        serve_won_loser=serve_l,
    ), player_row("tml" if source == "tml" else source, row.get("winner_id"), winner, gender, row.get("winner_hand"), row.get("winner_ioc")), player_row("tml" if source == "tml" else source, row.get("loser_id"), loser, gender, row.get("loser_hand"), row.get("loser_ioc"))


def import_tml(seen: SeenMatches, flush):
    players, matches = [], []
    folder = DATA / "tml"
    for path in sorted(folder.glob("*.csv")):
        if path.name in ("ATP_Database.csv", "atp_matches_amateur.csv"):
            continue
        gender = "w" if "wta" in path.name else "m"
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                built = _sackmann_row(
                    row, "tml", gender, True, seen,
                    f"{path.stem}-{row.get('tourney_id')}-{row.get('match_num')}-{row.get('winner_id')}",
                )
                if built is None:
                    continue
                match, left, right = built
                matches.append(match)
                if left:
                    players.append(left)
                if right:
                    players.append(right)
                if len(matches) >= 5000:
                    flush(players, matches)
                    players, matches = [], []
    flush(players, matches)
    return


def import_sqlite(seen: SeenMatches, flush):
    path = DATA / "sqlite" / "database.sqlite"
    players, matches = [], []
    if not path.exists():
        return players, matches
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    cursor = connection.execute(
        """
        SELECT tourney_id, tourney_name, surface, tourney_level, tourney_date, match_num,
               winner_id, winner_name, winner_hand, winner_ioc, loser_id, loser_name, loser_hand, loser_ioc,
               score, best_of, round, winner_rank, loser_rank,
               w_svpt, w_1stWon, w_2ndWon, l_svpt, l_1stWon, l_2ndWon, winner1_name
        FROM matches
        """
    )
    for row in cursor:
        if row["winner1_name"]:
            continue
        level = str(row["tourney_level"] or "")
        if level not in SQLITE_LEVELS:
            continue
        mapping = {key: row[key] for key in row.keys()}
        built = _sackmann_row(
            mapping, "sackmann_sqlite", "m", True, seen,
            f"{row['tourney_id']}-{row['match_num']}-{row['winner_id']}",
        )
        if built is None:
            continue
        match, left, right = built
        matches.append(match)
        if left:
            players.append(left)
        if right:
            players.append(right)
        if len(matches) >= 5000:
            flush(players, matches)
            players, matches = [], []
    flush(players, matches)
    players = []
    for row in connection.execute("SELECT player_id, name_first, name_last, hand, ioc FROM players"):
        name = f"{row[1] or ''} {row[2] or ''}".strip()
        item = player_row("sackmann_sqlite", row[0], name, "m", row[3], row[4])
        if item:
            players.append(item)
        if len(players) >= 5000:
            flush(players, [])
            players = []
    flush(players, [])
    connection.close()
    return


def import_rankings(flush_ranks):
    rows = []
    path = DATA / "sqlite" / "database.sqlite"
    if path.exists():
        connection = sqlite3.connect(path)
        for ranking_date, rank, player, points in connection.execute(
            "SELECT ranking_date, rank, player, points FROM rankings WHERE ranking_date >= 20000101"
        ):
            parsed = from_yyyymmdd(ranking_date, end_of_week=False)
            if parsed is None or rank is None or player is None:
                continue
            try:
                rank_number = int(rank)
            except (TypeError, ValueError):
                continue
            rows.append(("atp", parsed[0], str(player), rank_number, points))
            if len(rows) >= 10000:
                flush_ranks(rows)
                rows = []
        connection.close()
        flush_ranks(rows)
        rows = []
    current = DATA / "sackmann" / "atp_rankings_current.csv"
    if current.exists():
        with current.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for parts in reader:
                if len(parts) < 3:
                    continue
                parsed = from_yyyymmdd(parts[0], end_of_week=False)
                if parsed is None:
                    continue
                if parsed[0] < datetime(2024, 5, 28, tzinfo=timezone.utc):
                    continue
                try:
                    rows.append(("atp", parsed[0], parts[2], int(parts[1]), None))
                except ValueError:
                    continue
                if len(rows) >= 10000:
                    flush_ranks(rows)
                    rows = []
    flush_ranks(rows)


def import_charting():
    logs = []
    ingested_at = datetime.now(timezone.utc)
    root = DATA / "charting"
    for gender, match_name, serve_name in (
        ("m", "charting-m-matches.csv", "charting-m-stats-ServeBasics.csv"),
        ("w", "charting-w-matches.csv", "charting-w-stats-ServeBasics.csv"),
    ):
        dates = {}
        match_path = root / match_name
        if not match_path.exists():
            continue
        with match_path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                parsed = from_yyyymmdd(row.get("Date"), end_of_week=False)
                if parsed is None:
                    continue
                dates[row.get("match_id")] = (parsed[0], surface_of(row.get("Surface")))
        serve_path = root / serve_name
        if not serve_path.exists():
            continue
        with serve_path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if (row.get("row") or "") != "Total":
                    continue
                meta = dates.get(row.get("match_id"))
                if meta is None:
                    continue
                try:
                    points = float(row.get("pts") or 0)
                    won = float(row.get("pts_won") or 0)
                except ValueError:
                    continue
                if points <= 0 or not row.get("player"):
                    continue
                logs.append((
                    str(uuid.uuid4()),
                    "charting",
                    str(row["match_id"])[:160],
                    row["player"][:255],
                    meta[0],
                    ingested_at,
                    "database_ingest_time",
                    "source_reported",
                    True,
                    meta[1],
                    points,
                    won,
                ))
    return logs


def main() -> None:
    load_env()
    seen = SeenMatches()
    connection = connect()
    cursor = connection.cursor()
    print("ten season files", flush=True)
    players, matches = import_ten(seen)
    print(f"  rows players={len(players)} matches={len(matches)}", flush=True)
    write_rows(cursor, INSERT_PLAYER, players)
    write_rows(cursor, INSERT_MATCH, matches)
    connection.commit()
    print("atp_tennis.csv odds", flush=True)
    odds_matches = import_atp_odds(seen)
    print(f"  matches={len(odds_matches)}", flush=True)
    write_rows(cursor, INSERT_MATCH, odds_matches)
    connection.commit()
    def flush_people(people, matches):
        if people:
            write_rows(cursor, INSERT_PLAYER, people)
        if matches:
            write_rows(cursor, INSERT_MATCH, matches)
        connection.commit()

    print("tml-data", flush=True)
    import_tml(seen, flush_people)
    print("sqlite sackmann dump", flush=True)
    import_sqlite(seen, flush_people)
    print("rankings from 2000", flush=True)

    def flush_ranks(ranks):
        if ranks:
            write_rows(cursor, INSERT_RANK, ranks)
            connection.commit()

    import_rankings(flush_ranks)
    print("charting serve logs", flush=True)
    logs = import_charting()
    print(f"  rows={len(logs)}", flush=True)
    write_rows(cursor, INSERT_SERVE, logs)
    connection.commit()
    cursor.execute("SELECT source, count(*) FROM ext_tennis_matches GROUP BY 1 ORDER BY 2 DESC")
    print("stored matches:")
    for source, count in cursor.fetchall():
        print(f"  {source}: {count}")
    cursor.close()
    connection.close()
    print("skipped as duplicates: raw/odds/atp_matches_2000.csv, raw/sackmann/atp_matches_*.csv")


if __name__ == "__main__":
    main()
