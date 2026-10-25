"""TennisExplorer result-page parsing with conservative result acceptance.

This source is a historical supplement. A daily page is never promoted to a
complete player-history coverage claim, and all timestamps describe fetch time.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from urllib.parse import urljoin, urlparse, parse_qs

from lxml import html


BASE_URL = "https://www.tennisexplorer.com"
PARSER_VERSION = "tennisexplorer_results_v1"
PROFILE_PARSER_VERSION = "tennisexplorer_profile_v3"
_MATCH_ID = re.compile(r"(?:\?|&)id=(\d+)")
_PROFILE_YEAR_TABLE = re.compile(r"^matches-(\d{4})-(\d+)-data$")
_PROFILE_YEAR_PAGE_SELECTOR = re.compile(r"^matches-(\d{4})-(\d+)$")
_PROFILE_SLUG = re.compile(r"/player/([^/]+)/?")
_SET_SCORE = re.compile(r"(?<!\d)(\d{1,2})(?:\((\d{1,2})\))?\s*-\s*(\d{1,2})(?:\((\d{1,2})\))?(?!\d)")


def _text(node) -> str:
    return " ".join(" ".join(node.itertext()).split())


def _gender_from_context(tournament_url: str, tournament: str) -> str:
    context = f"{tournament_url} {tournament}".casefold()
    if any(token in context for token in ("wta", "women", "female", "itf-w")):
        return "w"
    if any(token in context for token in ("atp", "men", "challenger", "itf-m")):
        return "m"
    return "unknown"


def _tour_from_context(tournament_url: str, tournament: str) -> str:
    context = f"{tournament_url} {tournament}".casefold()
    if "challenger" in context:
        return "challenger"
    if "itf" in context or "future" in context:
        return "itf"
    if "wta" in context or "women" in context:
        return "wta"
    if "atp" in context or "men" in context:
        return "atp"
    return "unknown"


def _player_link(row):
    links = []
    for anchor in row.xpath('.//a[contains(@href,"/player/")]'):
        href = urlparse(anchor.get("href", ""))
        path = href.path.strip("/")
        if path.startswith("player/"):
            links.append((path.removeprefix("player/"), _text(anchor)))
    # Doubles rows link two people; do not collapse them into a singles player.
    return links[0] if len(links) == 1 else None


def _cell_text(cell) -> str:
    text = " ".join(cell.itertext()).strip()
    return re.sub(r"\s+", " ", text)


def _set_cell_score(cell) -> str | None:
    value = _cell_text(cell)
    if not value:
        return None
    # TennisExplorer renders tie-break points in a superscript, e.g. 6⁹.
    superscripts = cell.xpath(".//sup/text()")
    if superscripts:
        base = _text(cell)
        suffix = str(superscripts[0]).strip()
        if suffix and base.endswith(suffix):
            base = base[: -len(suffix)].strip()
        if base.isdigit() and suffix.isdigit():
            return f"{base}({suffix})"
    return value if re.fullmatch(r"\d+(?:\(\d+\))?", value) else None


def _row_result(row) -> int | None:
    cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," result ")]')
    if len(cells) != 1:
        return None
    value = _cell_text(cells[0])
    try:
        return int(value)
    except ValueError:
        return None


def _row_sets(row) -> list[str]:
    cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," score ")]')
    values = [_set_cell_score(cell) for cell in cells]
    return [value for value in values if value is not None]


def _has_rejection_marker(row, opponent_row) -> bool:
    combined = f"{_text(row)} {_text(opponent_row)}".casefold()
    if any(term in combined for term in ("walkover", "w/o", "retired", "abandoned", "cancelled", "canceled")):
        return True
    attrs = " ".join(str(v) for node in (row, opponent_row) for v in node.xpath(".//@title|.//@alt")).casefold()
    return any(term in attrs for term in ("walkover", "retired", "abandoned", "cancelled", "canceled"))


def parse_results_page(document: bytes | str, played_on: date, *, fetched_at: datetime | None = None) -> list[dict]:
    """Extract only completed singles with unambiguous match/player source IDs."""
    if isinstance(document, bytes):
        root = html.fromstring(document)
    else:
        root = html.fromstring(document.encode("utf-8"))
    title = _text(root.xpath("//title")[0]).casefold() if root.xpath("//title") else ""
    if "tennis explorer" not in title or "result" not in title:
        raise ValueError("not_a_tennisexplorer_results_page")
    fetched = fetched_at or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    fetched = fetched.astimezone(timezone.utc)
    event_day = datetime.combine(played_on, time.min, tzinfo=timezone.utc)
    output: list[dict] = []

    for table in root.xpath('//table[contains(concat(" ",normalize-space(@class)," ")," result ")]'):
        tournament = ""
        tournament_url = ""
        rows = table.xpath(".//tr")
        index = 0
        while index < len(rows):
            row = rows[index]
            classes = set((row.get("class") or "").split())
            if "head" in classes:
                link = row.xpath('.//a[contains(@href,"/")][1]')
                tournament = _text(link[0]) if link else ""
                tournament_url = link[0].get("href", "") if link else ""
                index += 1
                continue

            match_links = row.xpath('.//a[contains(@href,"match-detail")]/@href')
            first = _player_link(row)
            if not match_links or first is None or index + 1 >= len(rows):
                index += 1
                continue
            opponent_row = rows[index + 1]
            second = _player_link(opponent_row)
            if second is None or _has_rejection_marker(row, opponent_row):
                index += 2
                continue
            match_id_match = _MATCH_ID.search(match_links[0])
            first_sets, second_sets = _row_result(row), _row_result(opponent_row)
            first_score, second_score = _row_sets(row), _row_sets(opponent_row)
            if (not match_id_match or first_sets is None or second_sets is None
                    or first_sets == second_sets or max(first_sets, second_sets) < 2
                    or len(first_score) < 2 or len(first_score) != len(second_score)):
                index += 2
                continue

            tour = _tour_from_context(tournament_url, tournament)
            gender = _gender_from_context(tournament_url, tournament)
            if tour == "unknown" or gender == "unknown":
                index += 2
                continue
            first_won = first_sets > second_sets
            winner, loser = (first, second) if first_won else (second, first)
            winner_sets, loser_sets = (first_score, second_score) if first_won else (second_score, first_score)
            score = " ".join(f"{a}-{b}" for a, b in zip(winner_sets, loser_sets))
            time_cell = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," time ")]/text()')
            match_url = urljoin(BASE_URL, match_links[0])
            output.append({
                "source_match_id": match_id_match.group(1),
                "source_url": match_url,
                "played_at": event_day,
                "available_at": fetched,
                "event_date_precision": "provider_local_day",
                "event_time_local": str(time_cell[0]).strip() if time_cell else None,
                "tour_level": tour,
                "gender": gender,
                "tournament": tournament[:255],
                "surface": "unknown",
                "round_name": "",
                "best_of": 5 if max(first_sets, second_sets) >= 3 else 3,
                "winner_name": winner[1][:255],
                "loser_name": loser[1][:255],
                "winner_player_id": winner[0][:64],
                "loser_player_id": loser[0][:64],
                "score": score[:64],
                "stats": {"source": "tennis_explorer", "retrospective": True,
                          "event_date_precision": "provider_local_day", "score_rows": [first_score, second_score],
                          "result_sets": [first_sets, second_sets], "match_status": "completed_result",
                          "profile_urls": [urljoin(BASE_URL, "/player/" + first[0] + "/"),
                                           urljoin(BASE_URL, "/player/" + second[0] + "/")]},
            })
            index += 2
    # One date page can contain mirrored or repeated rows. Keep one result per provider match id.
    unique: dict[str, dict] = {}
    for item in output:
        unique.setdefault(item["source_match_id"], item)
    return list(unique.values())


def _score_cell_text(cell) -> str:
    """Preserve tie-break superscripts as `6-7(5)` instead of concatenating them."""
    parts: list[str] = []

    def visit(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            if child.tag.lower() == "sup":
                parts.extend(("(", _text(child), ")"))
            else:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(cell)
    return " ".join("".join(parts).split())


def _profile_score(cell) -> tuple[str, int, int] | None:
    value = _score_cell_text(cell)
    sets = _SET_SCORE.findall(value)
    normalized = _SET_SCORE.sub(" ", value)
    if len(sets) < 2 or re.sub(r"[\s,;]+", "", normalized):
        return None
    left_wins = sum(int(left) > int(right) for left, _, right, _ in sets)
    right_wins = sum(int(right) > int(left) for left, _, right, _ in sets)
    if left_wins == right_wins or max(left_wins, right_wins) < 2:
        return None
    score = " ".join(
        f"{left}{f'({left_tb})' if left_tb else ''}-{right}{f'({right_tb})' if right_tb else ''}"
        for left, left_tb, right, right_tb in sets
    )
    return score[:64], left_wins, right_wins


def _profile_tour(href: str, tournament_name: str = "") -> tuple[str, str]:
    """Infer tour from linked category, or from a linkless tournament label."""
    path = urlparse(href).path.casefold()
    context = f"{path} {tournament_name}".casefold()
    if "itf-women" in context or "itf-w" in context:
        return "itf", "w"
    if "itf-men" in context or "itf-m" in context:
        return "itf", "m"
    if "challenger-women" in context:
        return "challenger", "w"
    if "challenger-men" in context:
        return "challenger", "m"
    if "wta-women" in context or "wta-w" in context:
        return "wta", "w"
    if "atp-men" in context or "atp-m" in context:
        return "atp", "m"
    if "challenger" in context:
        return "challenger", "unknown"
    category = re.search(r"(?:^|\W)([mw])\s?\d{2,3}(?:\W|$)", context)
    if category:
        return "itf", "w" if category.group(1) == "w" else "m"
    if "itf" in context or "future" in context:
        return "itf", "unknown"
    if "wta" in context or "women" in context:
        return "wta", "w"
    if "atp" in context or "men" in context:
        return "atp", "m"
    return "unknown", "unknown"


def parse_profile_page(document: bytes | str, profile_url: str, *, fetched_at: datetime | None = None) -> dict:
    """Parse completed singles from a TennisExplorer `annual=all` player page.

    The result describes matches visible in this response. It deliberately
    does not certify full-career completeness; that requires separately
    verifying the provider's accessible-history boundary for the profile.
    """
    root = html.fromstring(document if isinstance(document, bytes) else document.encode("utf-8"))
    title = _text(root.xpath("//title")[0]).casefold() if root.xpath("//title") else ""
    slug_match = _PROFILE_SLUG.search(urlparse(profile_url).path)
    if "tennis explorer" not in title or not slug_match:
        raise ValueError("not_a_tennisexplorer_player_profile")
    source_player_id = slug_match.group(1)
    fetched = fetched_at or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    fetched = fetched.astimezone(timezone.utc)

    profile_text = _text(root.xpath('//table[contains(concat(" ",normalize-space(@class)," ")," plDetail ")]')[0]) \
        if root.xpath('//table[contains(concat(" ",normalize-space(@class)," ")," plDetail ")]') else _text(root)
    sex = re.search(r"\bsex\s*:\s*(man|woman)\b", profile_text, re.I)
    profile_gender = {"man": "m", "woman": "w"}.get(sex.group(1).casefold()) if sex else "unknown"

    matches: list[dict] = []
    rows_seen = 0
    rows_rejected = 0
    years: set[int] = set()
    page_blocks: dict[int, set[int]] = {}
    rows_by_year: dict[int, int] = {}
    data_containers = []
    for container in root.xpath('//div[starts-with(@id,"matches-")]'):
        year_match = _PROFILE_YEAR_TABLE.match(container.get("id", ""))
        if not year_match:
            continue
        year = int(year_match.group(1))
        page = int(year_match.group(2))
        years.add(year)
        page_blocks.setdefault(year, set()).add(page)
        data_containers.append((year, page, container))

    selector_pages: dict[int, set[int]] = {}
    for selector in root.xpath('//*[starts-with(@id,"matches-")]'):
        selector_match = _PROFILE_YEAR_PAGE_SELECTOR.match(selector.get("id", ""))
        if selector_match:
            selector_pages.setdefault(int(selector_match.group(1)), set()).add(int(selector_match.group(2)))

    data_containers.sort(key=lambda item: (item[0], item[1]))
    for year, _page, container in data_containers:
        for row in container.xpath('.//tr[.//a[contains(@href,"match-detail")]]'):
            rows_seen += 1
            rows_by_year[year] = rows_by_year.get(year, 0) + 1
            player_cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," t-name ")]')
            player_links = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," t-name ")]//a[contains(@href,"/player/")]')
            match_links = row.xpath('.//a[contains(@href,"match-detail")]/@href')
            time_cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," time ")]')
            score_cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," tl ")]')
            # In the profile layout, singles have two linked names separated
            # by a dash. A doubles result is a single row with a slash-joined
            # team; never turn that row into a player's singles history.
            doubles_display = len(player_cells) == 1 and "/" in _text(player_cells[0])
            if (len(player_cells) != 1 or doubles_display or len(player_links) != 2
                    or not match_links or not time_cells or not score_cells):
                rows_rejected += 1
                continue
            player_ids = []
            for anchor in player_links:
                match = _PROFILE_SLUG.search(urlparse(anchor.get("href", "")).path)
                player_ids.append((match.group(1), _text(anchor)) if match else ("", _text(anchor)))
            if any(not pid for pid, _ in player_ids) or source_player_id not in {p[0] for p in player_ids}:
                rows_rejected += 1
                continue
            match_id = _MATCH_ID.search(match_links[0])
            score = _profile_score(score_cells[0])
            if not match_id or score is None:
                rows_rejected += 1
                continue
            played_text = _text(time_cells[0])
            date_match = re.match(r"^(\d{1,2})\.(\d{1,2})\.", played_text)
            if not date_match:
                rows_rejected += 1
                continue
            try:
                played_on = date(year, int(date_match.group(2)), int(date_match.group(1)))
            except ValueError:
                rows_rejected += 1
                continue
            previous_heads = row.xpath('preceding-sibling::tr[contains(concat(" ",normalize-space(@class)," ")," head ")][1]')
            tournament_head = previous_heads[0] if previous_heads else None
            tournament_anchor = tournament_head.xpath('.//a[1]') if tournament_head is not None else []
            tournament = (_text(tournament_anchor[0]) if tournament_anchor
                          else _text(tournament_head) if tournament_head is not None else "")
            tournament_url = tournament_anchor[0].get("href", "") if tournament_anchor else ""
            tour, tournament_gender = _profile_tour(tournament_url, tournament)
            gender = tournament_gender if tournament_gender != "unknown" else profile_gender
            if tour == "unknown" or gender == "unknown":
                rows_rejected += 1
                continue
            round_cells = row.xpath('.//td[contains(concat(" ",normalize-space(@class)," ")," round ")]')
            left_wins, right_wins = score[1], score[2]
            winner, loser = (player_ids[0], player_ids[1]) if left_wins > right_wins else (player_ids[1], player_ids[0])
            matches.append({
                "source": "tennis_explorer",
                "source_match_id": match_id.group(1),
                "source_player_id": source_player_id,
                "source_url": urljoin(BASE_URL, match_links[0]),
                "profile_url": urljoin(BASE_URL, profile_url),
                "played_at": datetime.combine(played_on, time.min, tzinfo=timezone.utc),
                "available_at": fetched,
                "event_date_precision": "provider_local_day",
                "tour_level": tour,
                "gender": gender,
                "tournament": tournament[:255],
                "surface": "unknown",
                "round_name": _text(round_cells[0])[:32] if round_cells else "",
                "best_of": 5 if max(left_wins, right_wins) >= 3 else 3,
                "winner_name": winner[1][:255],
                "loser_name": loser[1][:255],
                "winner_player_id": winner[0][:64],
                "loser_player_id": loser[0][:64],
                "score": score[0],
                "stats": {"source": "tennis_explorer", "retrospective": True,
                          "event_date_precision": "provider_local_day",
                          "history_scope": "accessible_profile_snapshot"},
            })

    pagination_missing_pages: dict[int, list[int]] = {}
    pagination_selector_without_data: dict[int, list[int]] = {}
    pagination_data_without_selector: dict[int, list[int]] = {}
    page_numbers_by_year: dict[int, list[int]] = {}
    for year in sorted(set(selector_pages) | set(page_blocks)):
        selectors = selector_pages.get(year, set())
        blocks = page_blocks.get(year, set())
        page_numbers_by_year[year] = sorted(blocks)
        present_markers = selectors | blocks
        if present_markers:
            missing = sorted(set(range(1, max(present_markers) + 1)) - present_markers)
            if missing:
                pagination_missing_pages[year] = missing
        absent_blocks = sorted(selectors - blocks)
        if absent_blocks:
            pagination_selector_without_data[year] = absent_blocks
        unselected_blocks = sorted(blocks - selectors) if selectors else []
        if unselected_blocks:
            pagination_data_without_selector[year] = unselected_blocks
    pagination_complete = not (
        pagination_missing_pages or pagination_selector_without_data or pagination_data_without_selector
    )

    unique: dict[str, dict] = {}
    for item in matches:
        unique.setdefault(item["source_match_id"], item)
    return {
        "source_player_id": source_player_id,
        "gender": profile_gender,
        "available_at": fetched,
        "parser_version": PROFILE_PARSER_VERSION,
        "years": sorted(years),
        "pages_by_year": {str(year): pages for year, pages in page_numbers_by_year.items()},
        "rows_by_year": {str(year): rows for year, rows in sorted(rows_by_year.items())},
        "pagination_missing_pages": {str(year): pages for year, pages in pagination_missing_pages.items()},
        "pagination_selector_without_data": {
            str(year): pages for year, pages in pagination_selector_without_data.items()
        },
        "pagination_data_without_selector": {
            str(year): pages for year, pages in pagination_data_without_selector.items()
        },
        "pagination_complete": pagination_complete,
        "rows_seen": rows_seen,
        "rows_rejected": rows_rejected,
        "matches": list(unique.values()),
        "history_scope": "accessible_profile_snapshot",
        "complete_career_history": False,
    }
