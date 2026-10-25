"""Parser for the Match Charting Project's retrospective CSV files.

The project contains selectively charted matches, not a complete match feed.
Nothing emitted here is eligible to be treated as live observations.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections.abc import Iterator
from typing import Any

PARSER_VERSION = "match_charting_project_csv_v1"
MATCH_FIELDS = {"match_id", "Player 1", "Player 2", "Date", "Tournament", "Round", "Surface"}
POINT_FIELDS = {"match_id", "Pt", "Set1", "Set2", "Gm1", "Gm2", "Pts", "Gm#", "Svr", "PtWinner"}


def _digest(row: dict[str, str]) -> str:
    payload = "\x1f".join(f"{key}={row.get(key, '')}" for key in sorted(row))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_rows(body: bytes, required: set[str]) -> csv.DictReader:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_csv_encoding") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("unexpected_csv_header")
    return reader


def parse_match_metadata(body: bytes, *, gender: str, diagnostics: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return immutable source observations; deliberately no winner/result."""
    if gender not in {"m", "w"}:
        raise ValueError("invalid_gender")
    reader = _read_rows(body, MATCH_FIELDS)
    output: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for line_number, row in enumerate(reader, start=2):
        match_id = (row.get("match_id") or "").strip()
        player1 = (row.get("Player 1") or "").strip()
        player2 = (row.get("Player 2") or "").strip()
        event_date = (row.get("Date") or "").strip()
        # The upstream file contains an occasional malformed legacy row where
        # an unquoted comma shifts every column. Reject that row while keeping
        # the rest of the raw source reproducible.
        if (not match_id or not player1 or not player2
                or not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", event_date)
                or None in row):
            if diagnostics is not None:
                diagnostics.append({"line": line_number, "reason": "malformed_match_metadata_row", "match_id": match_id or None})
            continue
        row_hash = _digest(row)
        if match_id in seen:
            if seen[match_id] == row_hash:
                continue
            raise ValueError("conflicting_duplicate_match_metadata_id")
        seen[match_id] = row_hash
        output.append({
            "source_match_id": match_id,
            "observation_hash": row_hash,
            "payload": {
                "source": "match_charting_project", "retrospective": True,
                "charting_sample": True, "gender": gender,
                "player_1": player1, "player_2": player2,
                "date": (row.get("Date") or "").strip(),
                "tournament": (row.get("Tournament") or "").strip(),
                "round": (row.get("Round") or "").strip(),
                "surface": (row.get("Surface") or "").strip(),
                "metadata": {key: value for key, value in row.items() if key not in {"Player 1", "Player 2"}},
                "winner": None, "score": None,
            },
        })
    return output


def _score(raw: str, *, tie_break: bool = False) -> tuple[int | str, int | str] | None:
    match = re.fullmatch(r"\s*(AD|\d+|0|15|30|40)\s*[-:]\s*(AD|\d+|0|15|30|40)\s*", raw, re.I)
    if not match:
        return None
    values: list[int | str] = []
    for value in match.groups():
        value = value.upper()
        if value == "AD":
            values.append("AD")
        elif not tie_break:
            values.append(value)
        else:
            values.append(int(value))
    return values[0], values[1]


def _is_tiebreak(score: tuple[int | str, int | str], games: tuple[int, int]) -> bool:
    # A normal 6-6 game can still have 0/15/30/40/AD scoring. Only numeric
    # point scores beyond that range establish a tiebreak state.
    return games == (6, 6) and any(isinstance(value, int) and value not in {0, 15, 30, 40} for value in score)


def _infer_regular_winner(before: tuple[int | str, int | str], after: tuple[int | str, int | str]) -> int | None:
    order = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
    changed = [side for side in (0, 1) if before[side] != after[side]]
    if len(changed) != 1:
        return None
    side = changed[0]
    other = 1 - side
    if before[side] == "40" and after[side] == "AD" and before[other] == "40":
        return side
    if before[side] == "AD" and after[side] == "40" and before[other] == "40":
        return other
    if before[side] in order and after[side] in order and order[after[side]] == order[before[side]] + 1:
        return side
    return None


def _iter_point_groups(body: bytes) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    reader = _read_rows(body, POINT_FIELDS)
    current_id: str | None = None
    rows: list[dict[str, Any]] = []
    closed: set[str] = set()
    for row in reader:
        match_id = (row.get("match_id") or "").strip()
        if not match_id:
            raise ValueError("missing_point_match_id")
        if current_id is not None and match_id != current_id:
            closed.add(current_id)
            yield current_id, rows
            rows = []
        if match_id in closed:
            raise ValueError("noncontiguous_match_point_rows")
        current_id = match_id
        rows.append(row)
    if current_id is not None:
        yield current_id, rows


def parse_point_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize charted states while retaining uncertainty and source gaps."""
    events: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    previous: dict[tuple[int, int], tuple[int | str, int | str]] = {}
    row_hashes: list[str] = []
    sets: set[int] = set()
    for row_number, row in enumerate(rows, start=1):
        row_hashes.append(_digest(row))
        try:
            set_a, set_b = int(row["Set1"]), int(row["Set2"])
            game_a, game_b = int(row["Gm1"]), int(row["Gm2"])
            game_number = int(row["Gm#"])
            point_number = int(row["Pt"])
        except (KeyError, TypeError, ValueError):
            gaps.append({"row": row_number, "reason": "invalid_point_coordinates"})
            continue
        if min(set_a, set_b, game_a, game_b, game_number, point_number) < 0 or game_number == 0 or point_number == 0:
            gaps.append({"row": row_number, "reason": "out_of_range_point_coordinates"})
            continue
        try:
            games = (int(row["Gm1"]), int(row["Gm2"]))
        except (KeyError, TypeError, ValueError):
            gaps.append({"row": row_number, "reason": "invalid_point_coordinates"})
            continue
        # Determine tiebreak score notation from numeric point states. A 6-6
        # ordinary game can still contain advantage scoring.
        raw_score = row.get("Pts") or ""
        tie_score = _score(raw_score, tie_break=True)
        tie_break = tie_score is not None and _is_tiebreak(tie_score, games)
        score = _score(raw_score, tie_break=tie_break)
        winner_raw = (row.get("PtWinner") or "").strip()
        server_raw = (row.get("Svr") or "").strip()
        if score is None or winner_raw not in {"1", "2"}:
            gaps.append({"row": row_number, "reason": "invalid_score_or_winner"})
            continue
        set_number = max(set_a, set_b) + 1
        sets.add(set_number)
        key = (set_number, game_number)
        before = previous.get(key, (0, 0) if tie_break else ("0", "0"))
        if tie_break and not all(isinstance(value, int) for value in before):
            # The first visible tie-break score is commonly 1-0; there is no
            # explicit 0-0 row in the file. Anchor that sequence at 0-0.
            before = (0, 0)
        winner = int(winner_raw) - 1
        if tie_break:
            changed = [side for side in (0, 1) if score[side] == before[side] + 1 and score[1 - side] == before[1 - side]]
        else:
            winner_inferred = _infer_regular_winner(before, score)
            changed = [winner_inferred] if winner_inferred is not None else []
        confirmed = changed == [winner]
        if not confirmed:
            gaps.append({"row": row_number, "set": set_number, "game": game_number,
                         "reason": "reported_winner_score_transition_mismatch"})
        event = {
            "set": set_number, "game_number": game_number, "game_score": list(games),
            "point_index": point_number - 1, "state_before": list(before),
            "state_after": list(score), "point_score": list(score),
            "server": int(server_raw) - 1 if server_raw in {"1", "2"} else None,
            "winner": winner, "confirmed": confirmed, "tie_break": tie_break,
            "retrospective": True, "quality": "confirmed" if confirmed else "ambiguous",
            "shot_sequence": (row.get("1st") or "") + (";" + row["2nd"] if row.get("2nd") else ""),
            "notes": (row.get("Notes") or "").strip(),
        }
        events.append(event)
        previous[key] = score
    quality = {
        "provider": "match_charting_project", "sampled_matches": 1,
        "event_count": len(events), "confirmed_points": sum(e["confirmed"] for e in events),
        "ambiguous_points": sum(not e["confirmed"] for e in events),
        "set_numbers": sorted(sets), "row_hash": hashlib.sha256("".join(row_hashes).encode()).hexdigest(),
        "gaps": gaps, "complete": False, "source_declares_complete": False,
        "completeness": "selected_match_chart",
    }
    return events, quality


def iter_point_documents(body: bytes) -> Iterator[dict[str, Any]]:
    """Yield one append-only document per charted match from the grouped file."""
    for match_id, rows in _iter_point_groups(body):
        events, quality = parse_point_rows(rows)
        yield {"source_match_id": match_id, "events": events, "quality": quality,
               "content_hash": quality["row_hash"]}
