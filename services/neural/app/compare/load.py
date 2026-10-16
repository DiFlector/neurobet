"""Build comparison rows from Fonbet and from the external match table."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from tennis_adapter.external_names import fold, surname_and_initial

from .candidates.rating import HistoryMatch
from .rows import CompareRow, no_vig


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def alias_map(session: Session) -> Dict[str, str]:
    rows = session.execute(
        text(
            """
            SELECT a.alias, p.canonical_name
            FROM participant_aliases a
            JOIN participants p ON p.id = a.participant_id
            WHERE a.source = 'fonbet' AND a.link_confidence IS NOT NULL
            """
        )
    ).fetchall()
    return {fold(alias): canonical for alias, canonical in rows}


def _key(name: str, aliases: Dict[str, str]) -> str:
    folded = fold(name or "")
    return aliases.get(folded, folded)


def _canonical_serve_names(session: Session) -> Dict[str, str]:
    """Surname|initial -> display name, only when that pair has one canonical player."""
    rows = session.execute(
        text(
            """
            SELECT display_name, surname_key, initial
            FROM ext_tennis_canonical_players
            WHERE surname_key <> ''
            """
        )
    ).fetchall()
    grouped: Dict[str, set] = {}
    for display_name, surname, initial in rows:
        grouped.setdefault(f"{surname}|{initial or ''}", set()).add(display_name)
    return {key: next(iter(names)) for key, names in grouped.items() if len(names) == 1}


def load_history(session: Session, aliases: Dict[str, str]) -> List[HistoryMatch]:
    history = []
    rows = session.execute(
        text(
            """
            SELECT available_at, winner_name, loser_name, surface, tour_level,
                   serve_won_winner, serve_won_loser, winner_rank, loser_rank
            FROM ext_tennis_canonical_matches
            """
        )
    ).fetchall()
    for available, winner, loser, surface, level, serve_w, serve_l, rank_w, rank_l in rows:
        history.append(
            HistoryMatch(
                available_at=_utc(available),
                winner=winner,
                loser=loser,
                surface=surface or "unknown",
                tour_level=level or "unknown",
                serve_points_winner=40.0 if serve_w else 0.0,
                serve_won_winner=40.0 * float(serve_w or 0),
                serve_points_loser=40.0 if serve_l else 0.0,
                serve_won_loser=40.0 * float(serve_l or 0),
                winner_rank=rank_w,
                loser_rank=rank_l,
            )
        )
    serves = session.execute(
        text(
            """
            SELECT player_name, available_at, serve_points, serve_points_won
            FROM ext_tennis_serve_logs
            """
        )
    ).fetchall()
    serve_names = _canonical_serve_names(session)
    for name, available, points, won in serves:
        canonical = serve_names.get("|".join(surname_and_initial(name)), name)
        history.append(
            HistoryMatch(
                available_at=_utc(available),
                winner=canonical,
                loser=canonical,
                surface="unknown",
                tour_level="unknown",
                serve_points_winner=float(points),
                serve_won_winner=float(won),
            )
        )
    fonbet = session.execute(
        text(
            """
            SELECT finished_at, participant_a_name, participant_b_name, metadata_json, current_score
            FROM events
            WHERE sport_code = 'tennis' AND status IN ('finished', 'completed') AND finished_at IS NOT NULL
            """
        )
    ).fetchall()
    for finished, name_a, name_b, meta, score in fonbet:
        if not name_a or not name_b or "/" in name_a or "/" in name_b:
            continue
        label = _fonbet_label(name_a, name_b, meta or {}, score or {})
        if label is None:
            continue
        winner, loser = (name_a, name_b) if label == 1 else (name_b, name_a)
        history.append(
            HistoryMatch(
                available_at=_utc(finished),
                winner=_key(winner, aliases),
                loser=_key(loser, aliases),
                surface=(meta or {}).get("surface") or "unknown",
                tour_level="fonbet",
            )
        )
    return history


def load_external_prematch(session: Session, limit_from: str = "2018-01-01") -> List[CompareRow]:
    rows = session.execute(
        text(
            """
            SELECT canonical_key, played_at, available_at, tour_level, tournament, surface,
                   best_of, round_name, gender, winner_name, loser_name, odds_winner, odds_loser,
                   winner_rank, loser_rank
            FROM ext_tennis_canonical_matches
            WHERE odds_winner > 1 AND odds_loser > 1 AND played_at >= :start
            ORDER BY played_at
            """
        ),
        {"start": limit_from},
    ).fetchall()
    built = []
    for item in rows:
        (
            match_id, played, available, level, _tournament, surface, best_of, round_name,
            gender, winner, loser, odds_w, odds_l, rank_w, rank_l,
        ) = item
        if fold(winner) <= fold(loser):
            player_a, player_b, label = winner, loser, 1
            odds_a, odds_b = float(odds_w), float(odds_l)
            rank_a, rank_b = rank_w, rank_l
        else:
            player_a, player_b, label = loser, winner, 0
            odds_a, odds_b = float(odds_l), float(odds_w)
            rank_a, rank_b = rank_l, rank_w
        played_at = _utc(played)
        available_at = _utc(available)
        row = CompareRow(
            event_id=str(match_id),
            observed_at=played_at,
            started_at=played_at,
            finished_at=available_at,
            phase="prematch",
            label=label,
            odds_a=odds_a,
            odds_b=odds_b,
            market_prob_a=no_vig(odds_a, odds_b),
            surface=surface or "unknown",
            tour_level=level or "unknown",
            round_name=round_name or "",
            best_of=int(best_of or 3),
            gender=gender or "unknown",
            player_a=player_a,
            player_b=player_b,
            sets_to_win=3 if int(best_of or 3) >= 5 else 2,
        )
        if rank_a and rank_b:
            row.rank_diff = (1.0 / rank_a) - (1.0 / rank_b)
        built.append(row)
    return built


def load_fonbet(session: Session, phase: str, aliases: Dict[str, str]) -> List[CompareRow]:
    events = session.execute(
        text(
            """
            SELECT id::text, participant_a_name, participant_b_name, scheduled_start_at, finished_at,
                   metadata_json, current_score
            FROM events
            WHERE sport_code = 'tennis' AND status IN ('finished', 'completed')
              AND source_event_id ~ '^[0-9]+$'
            ORDER BY scheduled_start_at
            """
        )
    ).fetchall()
    rows: List[CompareRow] = []
    for event_id, name_a, name_b, started, finished, meta, score in events:
        if not name_a or not name_b or "/" in name_a or "/" in name_b:
            continue
        label = _fonbet_label(name_a, name_b, meta or {}, score or {})
        if label is None or finished is None:
            continue
        start = _utc(started or finished)
        finish = _utc(finished)
        if phase == "prematch":
            quote = _opening_odds(session, event_id, start)
            if quote is None:
                continue
            observed, odds_a, odds_b = quote
            rows.append(_fonbet_row(event_id, observed, start, finish, "prematch", label, odds_a, odds_b, name_a, name_b, meta or {}, aliases))
            continue
        for observed, odds_a, odds_b, board in _live_points(session, event_id, start, finish):
            row = _fonbet_row(event_id, observed, start, finish, "live", label, odds_a, odds_b, name_a, name_b, meta or {}, aliases)
            row.sets_a = board["sets_a"]
            row.sets_b = board["sets_b"]
            row.games_a = board["games_a"]
            row.games_b = board["games_b"]
            row.points_a = board["points_a"]
            row.points_b = board["points_b"]
            row.server = board["server"]
            rows.append(row)
    return rows


def _fonbet_row(event_id, observed, start, finish, phase, label, odds_a, odds_b, name_a, name_b, meta, aliases) -> CompareRow:
    tournament = meta.get("raw_tournament") or ""
    level = "itf" if tournament.startswith("World Tennis") else "atp_wta" if tournament.startswith(("ATP", "WTA")) else "unknown"
    gender = "w" if "Женщин" in tournament or tournament.startswith("WTA") else "m" if "Мужчин" in tournament or tournament.startswith("ATP") else "unknown"
    return CompareRow(
        event_id=event_id,
        observed_at=_utc(observed),
        started_at=start,
        finished_at=finish,
        phase=phase,
        label=label,
        odds_a=odds_a,
        odds_b=odds_b,
        market_prob_a=no_vig(odds_a, odds_b),
        surface=(meta.get("surface") or "unknown").lower(),
        tour_level=level,
        gender=gender,
        player_a=_key(name_a, aliases),
        player_b=_key(name_b, aliases),
        sets_to_win=2,
    )


def _opening_odds(session: Session, event_id: str, start: datetime):
    quotes = session.execute(
        text(
            """
            SELECT observed_at, outcome, odds
            FROM odds_snapshots
            WHERE event_id = :event_id AND market_type = 'match_winner'
            ORDER BY observed_at
            """
        ),
        {"event_id": event_id},
    ).fetchall()
    seen_a = None
    seen_b = None
    for observed, outcome, odds in quotes:
        if _utc(observed) > start + timedelta(minutes=2):
            break
        price = float(odds)
        if price <= 1.0:
            continue
        if outcome in ("player_a", "1"):
            seen_a = price
        elif outcome in ("player_b", "2"):
            seen_b = price
        if seen_a and seen_b:
            return observed, seen_a, seen_b
    return None


def _live_points(session: Session, event_id: str, start: datetime, finish: datetime):
    states = session.execute(
        text(
            """
            SELECT observed_at, score_detail
            FROM event_state_snapshots
            WHERE event_id = :event_id
            ORDER BY observed_at
            """
        ),
        {"event_id": event_id},
    ).fetchall()
    if len(states) > 12:
        picked = []
        for slot in range(12):
            index = int(round(slot * (len(states) - 1) / 11))
            if not picked or picked[-1] != index:
                picked.append(index)
        states = [states[index] for index in picked]
    points = []
    for observed, detail in states:
        moment = _utc(observed)
        if moment >= finish:
            continue
        odds = _odds_as_of(session, event_id, moment)
        if odds is None:
            continue
        points.append((moment, odds[0], odds[1], _board(detail or {})))
    return points


def _odds_as_of(session: Session, event_id: str, moment: datetime):
    rows = session.execute(
        text(
            """
            SELECT outcome, odds
            FROM odds_snapshots
            WHERE event_id = :event_id AND market_type = 'match_winner' AND observed_at <= :moment
            ORDER BY observed_at DESC
            LIMIT 20
            """
        ),
        {"event_id": event_id, "moment": moment},
    ).fetchall()
    odds_a = odds_b = None
    for outcome, odds in rows:
        price = float(odds)
        if price <= 1.0:
            continue
        if outcome in ("player_a", "1") and odds_a is None:
            odds_a = price
        elif outcome in ("player_b", "2") and odds_b is None:
            odds_b = price
        if odds_a and odds_b:
            return odds_a, odds_b
    return None


def _points(token) -> int:
    mapping = {"0": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4}
    return mapping.get(str(token or "0").upper(), 0)


def _board(detail: dict) -> dict:
    sets = detail.get("sets") or []
    game = detail.get("current_game") or {}
    stats = detail.get("stats") or {}
    sets_a = int(stats.get("sets_a") or 0)
    sets_b = int(stats.get("sets_b") or 0)
    games_a = games_b = 0
    if sets:
        last = sets[-1]
        games_a = int(last.get("games_a") or 0)
        games_b = int(last.get("games_b") or 0)
    server = detail.get("server") or ""
    if server == "player_a":
        server = "a"
    elif server == "player_b":
        server = "b"
    else:
        server = ""
    return {
        "sets_a": sets_a,
        "sets_b": sets_b,
        "games_a": games_a,
        "games_b": games_b,
        "points_a": _points(game.get("points_a")),
        "points_b": _points(game.get("points_b")),
        "server": server,
    }


def _fonbet_label(name_a: str, name_b: str, meta: dict, score: dict) -> Optional[int]:
    winner = meta.get("winner") or meta.get("participant_winner") or (score or {}).get("winner")
    official = meta.get("fonbet_official_result")
    if isinstance(official, dict) and official.get("winner"):
        winner = official.get("winner")
    if winner in ("player_a", name_a, "1"):
        return 1
    if winner in ("player_b", name_b, "2"):
        return 0
    sets_a = (score or {}).get("sets_a")
    sets_b = (score or {}).get("sets_b")
    if sets_a is None or sets_b is None or sets_a == sets_b:
        return None
    return 1 if int(sets_a) > int(sets_b) else 0
