"""Conservative parser for Tennis Abstract's dynamically loaded player fragment.

The provider fragment is a recent-results snapshot, not a career archive. The
parser keeps percentages as provider strings and never promotes them to point
counts or complete serve statistics.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

from lxml import html


BASE_URL = "https://www.tennisabstract.com/"
PARSER_VERSION = "tennis_abstract_recent_fragment_v1"
_FRAGMENT_PATH = re.compile(r"^/jsfrags/[A-Za-z0-9_-]{1,96}\.js$")


def _text(node) -> str:
    return " ".join(" ".join(node.itertext()).split())


def _name_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii").casefold()
    return " ".join(re.findall(r"[a-z]+", value))


def _name_identity(value: str) -> tuple[str, ...]:
    return tuple(sorted(_name_key(value).split()))


def discover_fragment(profile_document: bytes | str, profile_url: str, expected_name: str) -> dict:
    """Find the current jsfrags script and verify the profile page identity."""
    root = html.fromstring(profile_document if isinstance(profile_document, bytes)
                           else profile_document.encode("utf-8"))
    title = _text(root.xpath("//title")[0]) if root.xpath("//title") else ""
    match = re.match(r"^Tennis Abstract:\s*(.*?)\s+Match Results(?:,|$)", title, re.I)
    if not match:
        raise ValueError("invalid_tennis_abstract_profile_title")
    page_name = match.group(1).strip()
    # Exact normalized full-name equality only. Initial-only aliases are not
    # enough evidence to bind a provider profile to a canonical player.
    if not expected_name or _name_identity(page_name) != _name_identity(expected_name):
        raise ValueError("tennis_abstract_profile_identity_mismatch")
    scripts = []
    for raw in root.xpath("//script[@src]/@src"):
        absolute = urljoin(profile_url, raw)
        parsed = urlparse(absolute)
        if parsed.netloc.casefold() not in {"www.tennisabstract.com", "tennisabstract.com"}:
            continue
        if _FRAGMENT_PATH.fullmatch(parsed.path):
            scripts.append(absolute)
    unique_scripts = sorted(set(scripts))
    if not unique_scripts:
        raise ValueError("tennis_abstract_fragment_missing")
    if len(unique_scripts) != 1:
        raise ValueError("tennis_abstract_fragment_ambiguous")
    parsed = urlparse(unique_scripts[0])
    slug = parsed.path.rsplit("/", 1)[-1][:-3]
    return {"display_name": page_name, "source_player_id": slug,
            "profile_url": profile_url, "fragment_url": unique_scripts[0]}


def parse_recent_fragment(document: bytes | str, *, source_player_id: str,
                          expected_name: str, fetched_at: datetime | None = None) -> dict:
    """Parse the recent-results table; malformed or ambiguous rows are omitted."""
    root = html.fromstring(document if isinstance(document, bytes)
                           else document.encode("utf-8"))
    tables = root.xpath('//table[@id="recent-results"]')
    if len(tables) != 1:
        raise ValueError("tennis_abstract_recent_results_table_missing_or_ambiguous")
    fetched = fetched_at or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    fetched = fetched.astimezone(timezone.utc)
    name_key = _name_key(expected_name)
    if not name_key:
        raise ValueError("tennis_abstract_expected_name_missing")
    matches = []
    rejected = 0
    rows = tables[0].xpath("./tbody/tr | ./tr")
    for row in rows:
        cells = row.xpath("./td")
        if len(cells) < 16:
            continue  # header or secondary row, not a match
        try:
            played = datetime.strptime(_text(cells[0]), "%d-%b-%Y").date()
        except ValueError:
            rejected += 1
            continue
        tournament_links = cells[1].xpath(".//a[@href]")
        tournament = _text(cells[1])
        tournament_url = urljoin(BASE_URL, tournament_links[0].get("href")) if tournament_links else ""
        surface = _text(cells[2]).casefold()
        round_name = _text(cells[3])
        result_cell = cells[6]
        bold_names = [_text(node) for node in result_cell.xpath(".//b")]
        profile_links = result_cell.xpath('.//a[contains(@href,"player.cgi") and @href]')
        if len(bold_names) != 1 or len(profile_links) != 1:
            rejected += 1
            continue
        bold_key = _name_key(bold_names[0])
        linked_name = _text(profile_links[0])
        query = parse_qs(urlparse(profile_links[0].get("href", "")).query)
        linked_slug = (query.get("p") or [""])[0]
        # In the fragment, the bold player is the winner. The linked player is
        # the opponent; the requested player is implicit in the fragment slug.
        bold_is_target = bool(bold_key and (bold_key in name_key or name_key in bold_key))
        linked_is_target = linked_slug.casefold() == source_player_id.casefold()
        if bold_is_target == linked_is_target:  # neither or both: fail closed
            rejected += 1
            continue
        winner_name, loser_name = (bold_names[0], linked_name) if bold_is_target else (linked_name, bold_names[0])
        score = _text(cells[7])
        if not re.fullmatch(r"\d{1,2}-\d{1,2}(?:\(\d{1,2}\))?(?:\s+\d{1,2}-\d{1,2}(?:\(\d{1,2}\))?){1,4}", score):
            rejected += 1
            continue
        if not tournament or surface not in {"hard", "clay", "grass", "carpet"}:
            rejected += 1
            continue
        source_match_id = hashlib.sha256("|".join((source_player_id, played.isoformat(),
            tournament.casefold(), round_name.casefold(), winner_name.casefold(),
            loser_name.casefold(), score)).encode()).hexdigest()[:40]
        stats = {}
        for key, index in (("dominance_ratio", 8), ("ace_pct", 9), ("double_fault_pct", 10),
                           ("first_serve_in_pct", 11), ("first_serve_won_pct", 12),
                           ("second_serve_won_pct", 13), ("break_points_saved", 14),
                           ("duration", 15)):
            value = _text(cells[index])
            if value:
                stats[key] = value
        matches.append({
            "source_match_id": source_match_id,
            "source_player_id": source_player_id,
            "played_at": datetime.combine(played, datetime.min.time(), timezone.utc).isoformat(),
            "available_at": fetched.isoformat(),
            "event_date_precision": "provider_local_day",
            "tour_level": "unknown", "gender": "unknown",
            "tournament": tournament[:255], "tournament_url": tournament_url[:512],
            "surface": surface, "round_name": round_name[:32],
            "opponent_name": linked_name if bold_is_target else bold_names[0],
            "winner_name": winner_name[:255], "loser_name": loser_name[:255],
            "score": score[:64], "player_won": bold_is_target,
            "stats": stats,
            "stats_semantics": "percentages_and_provider_values; denominators_not_provided",
            "retrospective": True,
        })
    return {"matches": matches, "rows_seen": len(rows), "rows_rejected": rejected,
            "checked_at": fetched.isoformat(), "source_player_id": source_player_id,
            "history_scope": "recent_fragment_not_full_career", "complete": False,
            "serve_statistics_usable": False, "parser_version": PARSER_VERSION}
