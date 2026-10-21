"""Flashscore tennis player-history scraper and conservative feed parser.

Player history pages are read from the public profile and its ordinary sports
feed.  The current website's visible "show more" handler still calls a retired
feed shape, so pagination uses the same public feed with its current page
parameters and archives every returned response before parsing it.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import re
import time
import unicodedata
from contextlib import nullcontext
from difflib import SequenceMatcher
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from .domain_filter import DomainFilter
from .fetcher import SandboxedFetcher

logger = logging.getLogger("research.tennis_flashscore")

PARSER_VERSION = "flashscore-tennis-player-v8"
_CONFIRMED_TERMINAL_REASONS = {
    "source_terminal_empty_response_confirmed",
    "source_terminal_empty_response_count_mismatch_confirmed",
}
_PROFILE_ID_RE = re.compile(r"/player/[^/]+/([A-Za-z0-9]+)/")
_PROFILE_PARSER_VERSION_RE = re.compile(r"^flashscore-tennis-player-v\d+$")
_FEED_RE = re.compile(
    r'cjs\.initialFeeds\[\s*["\']results_s["\']\s*\]\s*=\s*'
    r"\{\s*data:\s*`(.*?)`\s*(?:,\s*(?:change|\w+)\s*:|\})",
    re.DOTALL,
)
_SET_KEYS = (("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ"))
_SURFACE_WORDS = {
    "hard": "hard", "хард": "hard", "dura": "hard", "cemento": "hard",
    "clay": "clay", "грунт": "clay", "tierra batida": "clay", "terra battuta": "clay",
    "grass": "grass", "трава": "grass", "hierba": "grass", "erba": "grass",
    "carpet": "carpet", "ковер": "carpet", "ковёр": "carpet",
}
_WALKOVER_OR_VOID = re.compile(r"\b(?:walkover|w\s*/\s*o|retired|retirement|abandoned|cancelled|canceled|postponed|void|отказ|снялся|снятие)\b", re.I)
_DISQUALIFICATION = re.compile(r"\bdisqualification\b|\bdisqualified\b|дисквалификац", re.I)
_DOUBLES = re.compile(r"\b(?:mixed\s+)?doubles\b|парн(?:ый|ая|ое|ые|ых)?", re.I)
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def _search_tokens(value: str) -> list[str]:
    from tennis_adapter.normalizer import normalize_russian_player_name

    normalized = normalize_russian_player_name(value).casefold().replace("ё", "е")
    return re.findall(r"[a-z0-9]+", normalized.translate(_CYRILLIC_TO_LATIN))


def _profile_search_queries(value: str) -> list[str]:
    """Build bounded source-spelling and name-order variants for Flashscore search."""
    from tennis_adapter.normalizer import normalize_russian_player_name

    normalized = normalize_russian_player_name(value).casefold().replace("ё", "е")
    source_words = re.findall(r"[a-zа-я0-9]+", normalized)
    tokens: list[str] = []
    initial_mask: list[bool] = []
    for word in source_words:
        parts = re.findall(r"[a-z0-9]+", word.translate(_CYRILLIC_TO_LATIN))
        tokens.extend(parts)
        initial_mask.extend([len(word) == 1] * len(parts))
    if not tokens:
        return []
    source_tokens = []
    for token in tokens:
        alternate = token
        if alternate.startswith("dzh"):
            # Flashscore commonly uses the English spelling "Johnson" for
            # Russian "Джонсон", while surname-internal "дж" is usually "j".
            alternate = "j" + alternate[3:]
            if alternate.startswith("jo"):
                alternate = "joh" + alternate[2:]
        alternate = alternate.replace("zh", "j").replace("kh", "h")
        alternate = alternate.replace("iya", "ia")
        if alternate.endswith("ovich"):
            alternate = alternate[:-1]
        elif alternate.endswith("evich"):
            alternate = alternate[:-1]
        source_tokens.append(alternate)
    initials = any(initial_mask)
    if initials:
        source_surname = [token for token, initial in zip(source_tokens, initial_mask) if not initial]
        candidates = [" ".join(tokens), " ".join(reversed(tokens))]
        if source_surname:
            candidates.append(" ".join(source_surname))
    else:
        candidates = [" ".join(tokens), " ".join(reversed(tokens)), " ".join(source_tokens)]
    return list(dict.fromkeys(query for query in candidates if query))[:3]


def _profile_search_failure_reason(search_queries: list[str], answered: set[str],
                                   failed: set[str], parse_failed: set[str]) -> str | None:
    if failed:
        return "SEARCH_HTTP_ERROR"
    if parse_failed:
        return "SEARCH_RESPONSE_PARSE_ERROR"
    missing = {" ".join(query.split()).casefold() for query in search_queries} - answered
    return "SEARCH_RESPONSE_MISSING" if missing else None


def _profile_search_exception_reason(stage: str, exception_name: str) -> str:
    """Keep browser transport failures distinct from a successfully empty search."""
    if exception_name == "TimeoutError":
        if stage == "page_open":
            return "PAGE_OPEN_TIMEOUT"
        if stage == "search_response":
            return "SEARCH_RESPONSE_MISSING"
        if stage == "search_button":
            return "SEARCH_UI_MISSING"
        if stage == "search_input":
            return "SEARCH_INPUT_MISSING"
        if stage == "search_click":
            return "SEARCH_BUTTON_INTERACTION_TIMEOUT"
        return "SEARCH_INTERACTION_TIMEOUT"
    if exception_name in {"Error", "TargetClosedError"}:
        return "SEARCH_BROWSER_ERROR"
    return f"SEARCH_TRANSPORT_{exception_name.upper()}"


def _player_name_matches(query_name: str, candidate_name: str) -> bool:
    """Match localized surname/initial displays to full names in search results."""
    query, candidate = _search_tokens(query_name), _search_tokens(candidate_name)
    if not query or not candidate:
        return False
    if "".join(query) == "".join(candidate):
        return True
    initials = {token for token in query if len(token) == 1}
    surnames = [token for token in query if len(token) > 1]
    candidate_long = [token for token in candidate if len(token) > 1]
    if not surnames or not candidate_long:
        return False
    matched = [
        token for token in surnames
        if token in candidate_long or any(SequenceMatcher(None, token, other).ratio() >= 0.78 for other in candidate_long)
    ]
    if initials:
        if not matched:
            return False
        candidate_initials = {token[0] for token in candidate_long if token not in matched}
        candidate_initials.update(token for token in candidate if len(token) == 1)
        return initials.issubset(candidate_initials)
    return len(matched) == len(surnames)


def is_exact_player_name_match(query_name: str, candidate_name: str) -> bool:
    """Return true only when both full names normalize to the same ordered tokens."""
    def tokens(value: str) -> list[str]:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
        return _search_tokens(without_marks)

    query = tokens(query_name)
    candidate = tokens(candidate_name)
    return bool(query and candidate and query == candidate)


def is_auto_confirmable_player_match(query_name: str, candidate_name: str) -> bool:
    """Accept exact full names or a unique single-surname + first-initial pair.

    Abbreviated-name auto matching is intentionally narrow: both displays must
    contain exactly two simple tokens, the full surname token must match, and
    the candidate's given name must start with the abbreviated initial. Hyphenated
    and multi-part names stay in the manual confirmation queue.
    """
    if is_exact_player_name_match(query_name, candidate_name):
        return True
    if re.search(r"[-‐‑‒–—]", query_name) or re.search(r"[-‐‑‒–—]", candidate_name):
        return False

    def tokens(value: str) -> list[str]:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
        return _search_tokens(without_marks)

    query = tokens(query_name)
    candidate = tokens(candidate_name)
    if len(query) != 2 or len(candidate) != 2:
        return False
    query_initials = [token for token in query if len(token) == 1]
    query_surnames = [token for token in query if len(token) > 1]
    if len(query_initials) != 1 or len(query_surnames) != 1:
        return False
    surname = query_surnames[0]
    initial = query_initials[0]
    candidate_surnames = [token for token in candidate if token == surname]
    candidate_given_names = [token for token in candidate if token != surname]
    return (
        len(candidate_surnames) == 1
        and len(candidate_given_names) == 1
        and len(candidate_given_names[0]) > 1
        and candidate_given_names[0].startswith(initial)
    )


def _profile_candidates_from_search_payload(payload: Any, query_name: str) -> list[dict[str, str]]:
    """Filter the search JSON emitted by Flashscore's own browser UI.

    The current UI renders suggestions outside ordinary ``<a>`` elements, so
    scanning page links silently misses valid players. Only tennis Player rows
    with a stable source ID and a matching display name are accepted.
    """
    if not isinstance(payload, list):
        return []
    candidates: dict[str, dict[str, str]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        item_type = row.get("type") or {}
        sport = row.get("sport") or {}
        if str(item_type.get("id")) != "3" or str(sport.get("id")) != "2":
            continue
        source_player_id = str(row.get("id") or "")
        slug = str(row.get("url") or "")
        name = str(row.get("name") or "").strip()
        if (not re.fullmatch(r"[A-Za-z0-9]{8}", source_player_id) or
                not re.fullmatch(r"[A-Za-z0-9-]{1,96}", slug) or
                not name or not _player_name_matches(query_name, name)):
            continue
        profile_url = f"https://www.flashscore.com/player/{slug}/{source_player_id}/results/"
        try:
            _validate_profile_url(profile_url)
        except ValueError:
            continue
        gender = str((row.get("gender") or {}).get("name") or "").casefold()
        candidates[source_player_id] = {
            "name": name,
            "profile_url": profile_url,
            "source_player_id": source_player_id,
            "gender": "female" if gender == "women" else "male" if gender == "men" else "unknown",
        }
    return list(candidates.values())


@dataclass(frozen=True)
class FlashscoreMatch:
    source_match_id: str
    played_at: datetime
    home_name: str
    away_name: str
    home_player_id: Optional[str]
    away_player_id: Optional[str]
    winner_name: str
    loser_name: str
    winner_player_id: Optional[str]
    loser_player_id: Optional[str]
    score: str
    best_of: int
    gender: str
    tour_level: str
    tournament: str
    surface: str
    round_name: str
    raw_fields: dict[str, str] = field(default_factory=dict)
    source_url: Optional[str] = None


@dataclass(frozen=True)
class FlashscoreProfile:
    source_player_id: str
    full_name: Optional[str]
    matches: tuple[FlashscoreMatch, ...]
    skipped_rows: int
    feed_count: int
    pagination_complete: bool = False
    history_scope: str = "partial"
    pagination_reason: Optional[str] = None
    terminal_probe_count: int = 0
    advertised_event_count: Optional[int] = None
    observed_unique_event_count: int = 0
    observed_unique_singles_event_count: int = 0
    skipped_row_reasons: dict[str, int] = field(default_factory=dict)
    duplicate_source_rows: int = 0
    rejected_source_matches: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ScrapeResult:
    status: str
    url: str
    retrieved_at: datetime
    content_hash: Optional[str]
    profile: Optional[FlashscoreProfile] = None
    reason: Optional[str] = None
    archived_path: Optional[str] = None
    archived_page_paths: tuple[str, ...] = ()


def _integer(value: Optional[str]) -> Optional[int]:
    if value is None or not re.fullmatch(r"\d{1,12}", value.strip()):
        return None
    return int(value)


def _feed_payloads(html: str) -> list[str]:
    """Return results feeds found in the document (Flashscore may split by tab)."""
    return [match.group(1).replace("\\`", "`") for match in _FEED_RE.finditer(html)]


def _match_urls_from_profile_html(document: str, base_url: str) -> dict[str, str]:
    """Extract canonical match links from rendered profile result rows.

    Feed rows carry stable match IDs but not the exact player slugs used by
    Flashscore's match page. Preserve the link the site rendered instead of
    trying to reconstruct it from abbreviated player names.
    """
    urls: dict[str, str] = {}
    for anchor in re.finditer(r"<a\b[^>]*?href\s*=\s*([\"'])(.*?)\1", document or "", re.I | re.S):
        href = html_lib.unescape(anchor.group(2)).strip()
        if not href:
            continue
        absolute = urljoin(base_url, href)
        allowed, _ = DomainFilter.is_allowed(absolute)
        parsed = urlparse(absolute)
        if not allowed or not re.search(r"/match/tennis/", parsed.path):
            continue
        match_ids = parse_qs(parsed.query).get("mid", [])
        if len(match_ids) != 1 or not re.fullmatch(r"[A-Za-z0-9]+", match_ids[0]):
            continue
        # Keep a stable page URL including its actual slug and locale host;
        # tabs are discovered from this page when PBP is collected.
        urls.setdefault(match_ids[0], absolute)
    return urls


def _feed_records(payload: str) -> tuple[list[tuple[dict[str, str], dict[str, str]]], int]:
    """Tokenize the Flashscore ¬/÷ feed while retaining tournament context."""
    context: dict[str, str] = {}
    current: Optional[dict[str, str]] = None
    rows: list[tuple[dict[str, str], dict[str, str]]] = []
    metadata_keys = {"ZA", "ZAF", "ZL", "ZK", "ZEE", "ZB", "ZC", "ZD", "ZE", "ZF", "ZG", "ZH", "ZJ", "ZX", "ZHS", "ZCC", "TSS", "ZAC"}
    for token in payload.split("¬"):
        is_boundary = token.startswith("~")
        token = token.lstrip("~")
        if "÷" not in token:
            continue
        key, value = token.split("÷", 1)
        if is_boundary and key == "AA":
            if current is not None:
                rows.append((current, dict(context)))
            current = {key: value}
            continue
        if is_boundary and key in metadata_keys:
            if current is not None:
                rows.append((current, dict(context)))
                current = None
            context[key] = value
            continue
        if current is None:
            context[key] = value
        else:
            current[key] = value
    if current is not None:
        rows.append((current, dict(context)))
    return rows, len(rows)


def _profile_country_id(payload: str, player_id: str) -> Optional[str]:
    """Find the country code attached to this player in their own result rows."""
    rows, _ = _feed_records(payload)
    country_ids = set()
    for fields, _context in rows:
        if fields.get("PX") == player_id and (fields.get("CA") or "").isdigit():
            country_ids.add(fields["CA"])
        if fields.get("PY") == player_id and (fields.get("CB") or "").isdigit():
            country_ids.add(fields["CB"])
    return next(iter(country_ids)) if len(country_ids) == 1 else None


def _profile_feed_page_url(player_id: str, country_id: str, page_index: int) -> str:
    """Build the current public, zero-based player-results feed URL."""
    if not re.fullmatch(r"[A-Za-z0-9]{8}", player_id) or not country_id.isdigit() or page_index < 0:
        raise ValueError("Invalid Flashscore player feed coordinates")
    return (
        "https://global.flashscore.ninja/2/x/feed/"
        f"pr_2_{country_id}_{player_id}_{page_index}_1_en_1"
    )


def _is_doubles_result(fields: dict[str, str], context: dict[str, str]) -> bool:
    """Identify team events so the provider's singles count is compared correctly."""
    event_context = " ".join((context.get("ZA", ""), context.get("ZAF", ""), context.get("ZL", "")))
    return bool(_DOUBLES.search(event_context) or "/doubles" in event_context.casefold() or
                "/" in fields.get("CX", "") or "/" in fields.get("AF", ""))


def is_doubles_player_display(name: str) -> bool:
    """Fonbet displays doubles sides as two player names separated by a slash."""
    return "/" in (name or "")


def _advertised_singles_count_matches(advertised_count: Optional[int], singles_ids: set[str]) -> bool:
    """The profile's allEventsCount excludes doubles present in paged feed bodies."""
    return advertised_count is None or int(advertised_count) == len(singles_ids)


def _page_has_pagination_progress(page_index: int, new_match_ids: set[str]) -> bool:
    """Page zero may repeat the initial feed and still be a valid first page."""
    return page_index == 0 or bool(new_match_ids)


def _profile_page_failure_reason(response_status: int, response_error: Optional[str]) -> str:
    """Keep queue contention distinct from provider/network failure in import reports."""
    error = response_error or ""
    if "FLASHSCORE_SOURCE_LOCK_TIMEOUT" in error or "FLASHSCORE_SOURCE_REQUEST_EXPIRED" in error:
        return "source_queue_timeout"
    return "pagination_network_error" if response_status == 0 else f"pagination_http_{response_status}"


def _surface(*values: str) -> str:
    text = " ".join(values).casefold()
    for word, surface in _SURFACE_WORDS.items():
        if word in text:
            return surface
    return "unknown"


def _category(*values: str) -> tuple[str, str, int]:
    text = " ".join(values).casefold()
    if any(word in text for word in ("mixed", "смешан")):
        gender = "mixed"
    elif any(word in text for word in ("women", "wta", "женщ", "жен.", "ladies")):
        gender = "female"
    elif any(word in text for word in ("men", "мужчин", "муж.", "gentlemen", "atp")):
        gender = "male"
    else:
        gender = "unknown"
    if "junior" in text or "юниор" in text:
        level = "junior"
    elif "challenger" in text or "челленджер" in text:
        level = "challenger"
    elif "itf" in text or "m15" in text or "m25" in text or "w15" in text or "w25" in text:
        level = "itf"
    elif "wta" in text or "women" in text or "atp" in text or "men" in text or "grand slam" in text or "тур" in text:
        level = "atp_wta"
    else:
        level = "unknown"
    best_of = 5 if gender == "male" and any(x in text for x in (
        "grand slam", "roland garros", "french open", "wimbledon", "australian open", "us open",
        "ролан гаррос", "уимблдон",
        "открытый чемпионат австралии", "открытый чемпионат сша",
    )) else 3
    return gender, level, best_of


def _parse_score(fields: dict[str, str]) -> tuple[list[tuple[int, int]], str]:
    sets: list[tuple[int, int]] = []
    for home_key, away_key in _SET_KEYS:
        home, away = _integer(fields.get(home_key)), _integer(fields.get(away_key))
        if home is None and away is None:
            continue
        if home is None or away is None or max(home, away) > 20:
            break
        # Stop on empty placeholders such as 0-0; they do not establish a set result.
        if home == 0 and away == 0:
            break
        sets.append((home, away))
    display = " ".join(f"{home}-{away}" for home, away in sets)
    return sets, display


def _set_winner(home: int, away: int) -> Optional[str]:
    if home == away:
        return None
    high, low = max(home, away), min(home, away)
    # A set is complete at 6-0..6-4, 7-5, 7-6, or a match tiebreak.
    complete = (high >= 6 and high - low >= 2) or (high == 7 and low == 6) or (high >= 10 and high - low >= 2)
    if not complete:
        return None
    return "home" if home > away else "away"


def _is_doubles_result(fields: dict[str, str], context: dict[str, str]) -> bool:
    """This archive currently feeds singles features; never mix team results into player form."""
    competition = " ".join((context.get("ZA", ""), context.get("ZAF", ""), context.get("ZL", "")))
    if _DOUBLES.search(competition):
        return True
    return "/" in fields.get("CX", "") or "/" in fields.get("AF", "")


def _parse_match_with_reason(fields: dict[str, str], context: dict[str, str]) -> tuple[Optional[FlashscoreMatch], Optional[str]]:
    match_id = fields.get("AA", "").strip()
    timestamp = _integer(fields.get("AD"))
    home_name, away_name = fields.get("CX", "").strip(), fields.get("AF", "").strip()
    if _is_doubles_result(fields, context):
        return None, "doubles_excluded_from_singles"
    if not match_id or timestamp is None or not home_name or not away_name or home_name == away_name:
        return None, "missing_required_fields"
    played_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if played_at.year < 1970 or played_at.year > 2100:
        return None, "timestamp_out_of_range"
    raw_text = " ".join(fields.values())
    if _WALKOVER_OR_VOID.search(raw_text) or _DISQUALIFICATION.search(raw_text):
        return None, "walkover_retirement_or_disqualification"
    sets, score = _parse_score(fields)
    if not score:
        return None, "no_completed_set_score"
    title = context.get("ZA", "")
    category = context.get("ZAF", "")
    gender, tour_level, best_of = _category(title, category, context.get("ZL", ""))
    wins = {"home": 0, "away": 0}
    for home_games, away_games in sets:
        winner = _set_winner(home_games, away_games)
        if winner:
            wins[winner] += 1
    match_winner = "home" if wins["home"] > wins["away"] else "away" if wins["away"] > wins["home"] else None
    if match_winner is None or wins[match_winner] < best_of // 2 + 1:
        return None, "score_does_not_prove_match_winner"
    round_name = ""
    lower_title = title.casefold()
    for token in ("final", "semi-final", "quarter-final", "1/2", "1/4", "qualifying", "qualification", "квалификация", "финал", "полуфинал", "четвертьфинал"):
        if token in lower_title:
            round_name = token
            break
    home_id, away_id = fields.get("PX") or None, fields.get("PY") or None
    winner_is_home = match_winner == "home"
    return FlashscoreMatch(
        source_match_id=match_id,
        played_at=played_at,
        home_name=home_name,
        away_name=away_name,
        home_player_id=home_id,
        away_player_id=away_id,
        winner_name=home_name if winner_is_home else away_name,
        loser_name=away_name if winner_is_home else home_name,
        winner_player_id=home_id if winner_is_home else away_id,
        loser_player_id=away_id if winner_is_home else home_id,
        score=score,
        best_of=best_of,
        gender=gender,
        tour_level=tour_level,
        tournament=context.get("ZK") or context.get("ZA", "").split(":", 1)[-1].strip(),
        surface=_surface(title, category, context.get("ZL", "")),
        round_name=round_name,
        raw_fields=dict(fields),
    ), None


def _parse_match(fields: dict[str, str], context: dict[str, str]) -> Optional[FlashscoreMatch]:
    """Compatibility helper returning a parsed result without its diagnostic reason."""
    result, _reason = _parse_match_with_reason(fields, context)
    return result


def _parse_profile_feeds(
    payloads: Iterable[str], profile_url: str, full_name: Optional[str] = None,
    pagination_complete: bool = False, history_scope: str = "partial",
    pagination_reason: Optional[str] = None,
) -> FlashscoreProfile:
    parsed_url = urlparse(profile_url)
    match = _PROFILE_ID_RE.search(parsed_url.path)
    if not match:
        raise ValueError("URL is not a Flashscore player profile")
    player_id = match.group(1)
    payloads = list(payloads)
    if not payloads:
        raise ValueError("results_s feed not found in page HTML")
    parsed: dict[str, FlashscoreMatch] = {}
    row_count = 0
    skipped_reasons: dict[str, int] = {}
    rows_with_ids: set[str] = set()
    duplicate_source_rows = 0
    rejected_source_matches: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        rows, count = _feed_records(payload)
        row_count += count
        for fields, context in rows:
            source_id = fields.get("AA", "").strip()
            if source_id and source_id in rows_with_ids:
                duplicate_source_rows += 1
            elif source_id:
                rows_with_ids.add(source_id)
            result, reason = _parse_match_with_reason(fields, context)
            if result is not None:
                parsed[result.source_match_id] = result
            elif reason:
                skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
                if source_id and reason != "doubles_excluded_from_singles":
                    rejected_source_matches[source_id] = {
                        "source_match_id": source_id,
                        "reason": reason,
                        "home_player_id": fields.get("PX"),
                        "away_player_id": fields.get("PY"),
                        "home_name": fields.get("CX"),
                        "away_name": fields.get("AF"),
                    }
    for source_id in parsed:
        rejected_source_matches.pop(source_id, None)
    return FlashscoreProfile(
        source_player_id=player_id,
        full_name=full_name,
        matches=tuple(sorted(parsed.values(), key=lambda item: (item.played_at, item.source_match_id))),
        skipped_rows=max(0, row_count - len(parsed)),
        feed_count=row_count,
        skipped_row_reasons=skipped_reasons,
        duplicate_source_rows=duplicate_source_rows,
        rejected_source_matches=tuple(rejected_source_matches[key] for key in sorted(rejected_source_matches)),
        pagination_complete=pagination_complete,
        history_scope=history_scope,
        pagination_reason=pagination_reason,
    )


def parse_player_profile(html: str, profile_url: str) -> FlashscoreProfile:
    """Parse finished tennis results from an archived profile HTML document."""
    name_match = re.search(r'"participantName"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', html)
    name = None
    if name_match:
        try:
            name = json.loads('"' + name_match.group(1) + '"')
        except json.JSONDecodeError:
            name = name_match.group(1)
    return _parse_profile_feeds(_feed_payloads(html), profile_url, name)


def _validate_profile_url(url: str) -> None:
    allowed, reason = DomainFilter.is_allowed(url)
    if not allowed:
        raise ValueError(f"Flashscore profile URL rejected: {reason}")
    parsed = urlparse(url)
    if not _PROFILE_ID_RE.search(parsed.path) or "/results" not in parsed.path:
        raise ValueError("Only Flashscore player results URLs are supported")


def _observation_payload(item: FlashscoreMatch) -> dict[str, Any]:
    """Fields whose changes represent a new version of the source result."""
    return {
        "played_at": item.played_at.isoformat(),
        "home_name": item.home_name,
        "away_name": item.away_name,
        "home_player_id": item.home_player_id,
        "away_player_id": item.away_player_id,
        "winner_name": item.winner_name,
        "loser_name": item.loser_name,
        "winner_player_id": item.winner_player_id,
        "loser_player_id": item.loser_player_id,
        "score": item.score,
        "best_of": item.best_of,
        "gender": item.gender,
        "tour_level": item.tour_level,
        "tournament": item.tournament,
        "surface": item.surface,
        "round_name": item.round_name,
        "raw_fields": item.raw_fields,
        "source_url": item.source_url,
    }


def _observation_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _name_key(value: Optional[str]) -> str:
    from tennis_adapter.normalizer import normalize_russian_player_name

    normalized = normalize_russian_player_name(value or "")
    return re.sub(r"[^a-zа-яё0-9]+", "", normalized.casefold().replace("ё", "е"))


def _browser_launch_options() -> dict[str, Any]:
    options: dict[str, Any] = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    proxy = os.getenv("RESEARCH_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        options["proxy"] = {"server": proxy}
    return options


_FLASHSCORE_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
_FLASHSCORE_AD_HOST_MARKERS = ("doubleclick.net", "googlesyndication.com", "googleadservices.com", "taboola.com", "outbrain.com")


def _should_abort_flashscore_request(resource_type: str, url: str) -> bool:
    """Skip decorative and advertising traffic while retaining sports data requests."""
    host = (urlparse(url).hostname or "").casefold()
    return resource_type in _FLASHSCORE_BLOCKED_RESOURCE_TYPES or any(marker in host for marker in _FLASHSCORE_AD_HOST_MARKERS)


async def _install_flashscore_request_filter_async(context) -> None:
    async def filter_route(route):
        request = route.request
        if _should_abort_flashscore_request(request.resource_type, request.url):
            await route.abort()
        else:
            await route.continue_()
    await context.route("**/*", filter_route)


def _install_flashscore_request_filter_sync(page) -> None:
    def filter_route(route):
        request = route.request
        if _should_abort_flashscore_request(request.resource_type, request.url):
            route.abort()
        else:
            route.continue_()
    page.route("**/*", filter_route)


class FlashscoreTennisImporter:
    """Fetch profiles, archive raw HTML and idempotently import parsed results."""

    def __init__(self, fetcher: Optional[SandboxedFetcher] = None, storage=None):
        self.fetcher = fetcher or SandboxedFetcher(timeout_seconds=20)
        if storage is None:
            from .storage import ResearchStorage

            storage = ResearchStorage()
        self.storage = storage

    def scrape_profile(self, url: str, stop_before_date: datetime | None = None, page_checkpoint=None,
                       resume_pages=None, source_access=None) -> ScrapeResult:
        _validate_profile_url(url)
        profile_id_match = _PROFILE_ID_RE.search(urlparse(url).path)
        profile_id = profile_id_match.group(1) if profile_id_match else "unknown-player"
        def archive_page(page_document):
            page_html = page_document.get("html") or ""
            page_hash = "sha256:" + hashlib.sha256(page_html.encode("utf-8")).hexdigest()
            path = self.storage.archive_document(profile_id, page_hash, {
                "source": "flashscore", "url": page_document.get("source_url") or url,
                "retrieved_at": page_document.get("retrieved_at"), "parser_version": PARSER_VERSION,
                "content_hash": page_hash, "page_index": page_document["page_index"],
                "match_ids": page_document.get("match_ids", []),
                "match_urls": page_document.get("match_urls", {}),
                "response_status": page_document.get("response_status"),
                "response_error": page_document.get("response_error"),
                "pagination_reason": page_document.get("pagination_reason"),
                "terminal_probe_count": page_document.get("terminal_probe_count", 0),
                "terminal_probe_status": page_document.get("terminal_probe_status"),
                "terminal_probe_error": page_document.get("terminal_probe_error"),
                "advertised_event_count": page_document.get("advertised_event_count"),
                "observed_unique_event_count": page_document.get("observed_unique_event_count"),
                "observed_unique_singles_event_count": page_document.get("observed_unique_singles_event_count"),
                "result_feeds": page_document.get("result_feeds", []), "html": page_html,
            })
            page_document["archived_path"] = path
            if page_checkpoint:
                page_checkpoint({"page_index": page_document["page_index"], "match_ids": page_document.get("match_ids", []),
                                "next_action": ("complete_accessible_section" if
                                    page_document.get("pagination_reason") == "source_terminal_empty_response_confirmed"
                                    else "fetch_next_results_feed_page"),
                                "archived_path": path, "parser_version": PARSER_VERSION,
                                "response_status": page_document.get("response_status"),
                                "advertised_event_count": page_document.get("advertised_event_count"),
                                "observed_unique_event_count": page_document.get("observed_unique_event_count"),
                                "observed_unique_singles_event_count": page_document.get("observed_unique_singles_event_count"),
                                "pagination_reason": page_document.get("pagination_reason"),
                                "terminal_probe_count": page_document.get("terminal_probe_count", 0),
                                "terminal_probe_status": page_document.get("terminal_probe_status"),
                                "terminal_probe_error": page_document.get("terminal_probe_error"),
                                "reset_checkpoint": bool(page_document.get("reset_checkpoint")),
                                "retrieved_at": page_document.get("retrieved_at"), "content_hash": page_hash})
        resume_documents = self._load_resume_documents(profile_id, resume_pages or [])
        if resume_pages and not resume_documents and page_checkpoint:
            page_checkpoint({"page_index": 0, "parser_version": PARSER_VERSION,
                             "archived_path": None, "reset_checkpoint": True,
                             "next_action": "restart_invalid_checkpoint"})
        html, status, payloads, full_name, pagination_complete, page_documents = self._fetch_rendered_profile(
            url, stop_before_date=stop_before_date, page_callback=archive_page,
            resume_documents=resume_documents, source_access=source_access)
        # available_at means the document had arrived and could be consumed,
        # not when its request was started.
        retrieved_at = datetime.now(timezone.utc)
        if html is None:
            if status == "HTTP_429_ANTI_BOT_BLOCKED":
                source_status = "rate_limited"
            elif status in ("HTTP_403_ANTI_BOT_BLOCKED", "CAPTCHA_CHALLENGE_ABORTED"):
                source_status = "blocked"
            else:
                source_status = "unavailable"
            return ScrapeResult(status=source_status, url=url, retrieved_at=retrieved_at, content_hash=None, reason=status)
        content_hash = "sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest()
        history_scope = ("overlap" if pagination_complete and stop_before_date else
                         "full_accessible" if pagination_complete else "partial")
        profile_id_match = _PROFILE_ID_RE.search(urlparse(url).path)
        profile_id = profile_id_match.group(1) if profile_id_match else "unknown-player"
        rendered_match_urls = _match_urls_from_profile_html(html, url)
        archived_path = self.storage.archive_document(
            profile_id,
            content_hash,
            {
                "source": "flashscore",
                "url": url,
                "retrieved_at": retrieved_at.isoformat(),
                "parser_version": PARSER_VERSION,
                "content_hash": content_hash,
                "match_urls": rendered_match_urls,
                "result_feeds": payloads,
                "pagination_complete": pagination_complete,
                "history_scope": history_scope,
                "pagination_reason": page_documents[-1].get("pagination_reason") if page_documents else None,
                "terminal_probe_count": page_documents[-1].get("terminal_probe_count", 0) if page_documents else 0,
                "terminal_probe_status": page_documents[-1].get("terminal_probe_status") if page_documents else None,
                "terminal_probe_error": page_documents[-1].get("terminal_probe_error") if page_documents else None,
                "advertised_event_count": page_documents[-1].get("advertised_event_count") if page_documents else None,
                "observed_unique_event_count": page_documents[-1].get("observed_unique_event_count") if page_documents else None,
                "observed_unique_singles_event_count": page_documents[-1].get("observed_unique_singles_event_count") if page_documents else None,
                "html": html,
            },
        )
        archived_page_paths = []
        for page_document in page_documents:
            if page_document.get("archived_path"):
                archived_page_paths.append(page_document["archived_path"])
                continue
            page_html = page_document.get("html") or ""
            page_hash = "sha256:" + hashlib.sha256(page_html.encode("utf-8")).hexdigest()
            page_path = self.storage.archive_document(
                profile_id,
                page_hash,
                {
                    "source": "flashscore",
                    "url": url,
                    "retrieved_at": page_document.get("retrieved_at", retrieved_at.isoformat()),
                    "parser_version": PARSER_VERSION,
                    "content_hash": page_hash,
                    "page_index": page_document["page_index"],
                    "match_ids": page_document.get("match_ids", []),
                    "advertised_event_count": page_document.get("advertised_event_count"),
                    "observed_unique_event_count": page_document.get("observed_unique_event_count"),
                    "observed_unique_singles_event_count": page_document.get("observed_unique_singles_event_count"),
                    "pagination_reason": page_document.get("pagination_reason"),
                    "terminal_probe_count": page_document.get("terminal_probe_count", 0),
                    "terminal_probe_status": page_document.get("terminal_probe_status"),
                    "terminal_probe_error": page_document.get("terminal_probe_error"),
                    "match_urls": page_document.get("match_urls", {}),
                    "result_feeds": page_document.get("result_feeds", []),
                    "html": page_html,
                },
            )
            if page_path:
                archived_page_paths.append(page_path)
        try:
            pagination_reason = next((doc.get("pagination_reason") for doc in reversed(page_documents)
                                      if doc.get("pagination_reason")), None)
            if pagination_reason is None and status != "OK":
                pagination_reason = status
            profile = _parse_profile_feeds(payloads, url, full_name, pagination_complete,
                                           history_scope, pagination_reason) if payloads else parse_player_profile(html, url)
            source_urls = dict(rendered_match_urls)
            for page_document in page_documents:
                source_urls.update(page_document.get("match_urls") or {})
            if source_urls:
                profile = replace(profile, matches=tuple(
                    replace(item, source_url=source_urls.get(item.source_match_id)) for item in profile.matches
                ))
            terminal_document = next((doc for doc in reversed(page_documents)
                                      if doc.get("pagination_reason") in _CONFIRMED_TERMINAL_REASONS), None)
            if terminal_document:
                profile = replace(
                    profile,
                    terminal_probe_count=int(terminal_document.get("terminal_probe_count") or 0),
                    advertised_event_count=terminal_document.get("advertised_event_count"),
                    observed_unique_event_count=int(terminal_document.get("observed_unique_event_count") or 0),
                    observed_unique_singles_event_count=int(terminal_document.get("observed_unique_singles_event_count") or 0),
                )
        except ValueError as exc:
            return ScrapeResult(
                status="parse_error", url=url, retrieved_at=retrieved_at,
                content_hash=content_hash, reason=str(exc), archived_path=archived_path,
            )
        archive_complete = bool(archived_path) and len(archived_page_paths) == len(page_documents)
        if not archive_complete:
            profile = replace(profile, pagination_complete=False, history_scope="partial")
        status = "available" if profile.pagination_complete else "partial"
        if not archived_path:
            status += "_archive_failed"
        return ScrapeResult(
            status=status,
            url=url,
            retrieved_at=retrieved_at,
            content_hash=content_hash,
            profile=profile,
            reason=("ARCHIVE_INCOMPLETE" if not archive_complete else
                    (profile.pagination_reason or "PAGINATION_NOT_CONFIRMED")
                    if not profile.pagination_complete else None),
            archived_path=archived_path,
            archived_page_paths=tuple(archived_page_paths),
        )

    def _load_resume_documents(self, profile_id: str, resume_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Accept only a contiguous, archived prefix from this parser and player."""
        if not resume_pages or not hasattr(self.storage, "read_document"):
            return []
        documents: dict[int, dict[str, Any]] = {}
        for item in resume_pages:
            try:
                index = int(item["page_index"])
                path = str(item["archived_path"])
                item_parser_version = str(item.get("parser_version") or "")
                if (index < 1 or index in documents or not _PROFILE_PARSER_VERSION_RE.fullmatch(item_parser_version) or
                    not path.startswith("snapshots/research/") or f"/{profile_id}/" not in path):
                    return []
                document = self.storage.read_document(path)
                if (not document or document.get("parser_version") != item_parser_version or
                    document.get("page_index") != index):
                    return []
                source_url = str(document.get("url") or "")
                if not re.search(rf"/pr_2_\d+_{re.escape(profile_id)}_{index - 1}_1_en_1$", source_url):
                    return []
                body = str(document.get("html") or "")
                digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
                if digest != item.get("content_hash") or digest != document.get("content_hash"):
                    return []
                if document.get("response_status") != 200:
                    return []
                if body not in ("", "0") and not document.get("match_ids"):
                    return []
                if body in ("", "0") and document.get("pagination_reason") not in _CONFIRMED_TERMINAL_REASONS:
                    return []
                documents[index] = {**document, "archived_path": path}
            except (KeyError, TypeError, ValueError):
                return []
        if set(documents) != set(range(1, len(documents) + 1)):
            return []
        return [documents[index] for index in sorted(documents)]

    def resolve_player_profile(self, player_name: str, excluded_source_ids: set[str] | None = None) -> dict[str, Any]:
        """Find a unique surname/initial match through Flashscore's visible search UI."""
        from html import unescape

        excluded_source_ids = {str(value) for value in (excluded_source_ids or set())}
        search_queries = _profile_search_queries(player_name)
        if not search_queries:
            return {"status": "not_found", "candidates": []}
        home_url = "https://www.flashscore.com/"
        operation_stage = {"value": "browser_launch"}
        try:
            import asyncio
            from playwright.async_api import async_playwright

            async def find_links():
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(**_browser_launch_options())
                    try:
                        context = await browser.new_context(locale="en-US", timezone_id="UTC")
                        await _install_flashscore_request_filter_async(context)
                        page = await context.new_page()
                        search_payloads: list[tuple[str, Any]] = []
                        failed_queries: set[str] = set()
                        parse_failed_queries: set[str] = set()

                        async def capture_search_response(response):
                            parsed_response = urlparse(response.url)
                            if (parsed_response.netloc.casefold() != "s.livesport.services" or
                                    parsed_response.path != "/api/v2/search/"):
                                return
                            allowed, _ = DomainFilter.is_allowed(
                                response.url, allowlist={"livesport.services"}
                            )
                            if not allowed:
                                return
                            query = (parse_qs(parsed_response.query).get("q") or [""])[0]
                            if response.status in (403, 429):
                                failed_queries.add(query.strip().casefold())
                                return
                            if response.status != 200:
                                failed_queries.add(query.strip().casefold())
                                return
                            try:
                                payload = await response.json()
                            except Exception:
                                parse_failed_queries.add(query.strip().casefold())
                                return
                            search_payloads.append((query, payload))

                        page.on("response", capture_search_response)
                        operation_stage["value"] = "page_open"
                        # Search only needs the initial document and hydrated search
                        # control; waiting for every script/analytics request to reach
                        # DOMContentLoaded causes avoidable page-open timeouts.
                        response = await page.goto(home_url, wait_until="commit", timeout=20000)
                        if response and response.status in (403, 429):
                            return "blocked", [], "HTTP_403_OR_429_BLOCKED"
                        allowed, _ = DomainFilter.is_allowed(page.url)
                        if not allowed:
                            return "blocked", [], "REDIRECT_BLOCKED"
                        # `commit` returns before Flashscore's client bundles
                        # hydrate the header. The search button is visible in that
                        # server-rendered shell but inert: clicking it at this point
                        # silently leaves the search dialog unmounted. Wait for the
                        # document lifecycle, bounded independently from navigation.
                        operation_stage["value"] = "page_hydration"
                        try:
                            await page.wait_for_function(
                                "document.readyState === 'complete'", timeout=12000
                            )
                        except Exception:
                            logger.info("Flashscore page did not reach complete state before search")
                        search_button = page.locator('button[aria-label="Search"]:visible').first
                        operation_stage["value"] = "search_button"
                        try:
                            await search_button.wait_for(state="visible", timeout=8000)
                        except Exception:
                            # Flashscore has used non-button wrappers and varying
                            # aria labels across desktop layouts. Keep the fallback
                            # bounded and require a visible, purpose-labelled control.
                            search_button = page.locator(
                                '[aria-label*="search" i]:visible, [data-testid*="search" i]:visible'
                            ).first
                            try:
                                await search_button.wait_for(state="visible", timeout=4000)
                            except Exception:
                                title = (await page.title())[:120]
                                body = (await page.locator("body").inner_text())[:240].replace("\n", " ")
                                logger.info("Flashscore search control missing title=%r url=%s body=%r",
                                            title, page.url, body)
                                return "source_unavailable", [], "SEARCH_UI_MISSING"
                        operation_stage["value"] = "search_click"
                        try:
                            await search_button.click(timeout=3000)
                        except Exception:
                            body_hint = (await page.locator("body").inner_text()).casefold()
                            if any(token in body_hint for token in (
                                "captcha", "verify you are human", "checking your browser", "attention required"
                            )):
                                return "blocked", [], "ANTI_BOT_CHALLENGE"
                            # A visible enabled control can be covered by a consent
                            # or responsive-layout layer. Retry only that known
                            # control; never force clicks through an anti-bot page.
                            if not await search_button.is_visible() or not await search_button.is_enabled():
                                raise
                            logger.info("Flashscore search control click intercepted; retrying visible control")
                            await search_button.click(timeout=2000, force=True)
                        operation_stage["value"] = "search_input"
                        search_input = page.locator(
                            'input[placeholder*="type your search here" i]:visible, '
                            'input[type="search"]:visible, input[aria-label*="search" i]:visible, '
                            '[role="searchbox"]:visible'
                        ).first
                        try:
                            await search_input.wait_for(state="visible", timeout=4000)
                        except Exception:
                            visible_fields = await page.locator(
                                'input:visible, [contenteditable="true"]:visible'
                            ).evaluate_all("els => els.map(el => ({tag:el.tagName,type:el.type||'',role:el.getAttribute('role')||'',aria:el.getAttribute('aria-label')||'',placeholder:el.getAttribute('placeholder')||'',name:el.getAttribute('name')||''}))")
                            logger.info("Flashscore search field missing title=%r fields=%s body=%r",
                                (await page.title())[:120], visible_fields[:8],
                                (await page.locator("body").inner_text())[:240].replace("\n", " "))
                            return "source_unavailable", [], "SEARCH_INPUT_MISSING"
                        candidates = []
                        answered_queries: set[str] = set()
                        for search_query in search_queries:
                            operation_stage["value"] = "search_response"
                            expected_query = " ".join(search_query.split()).casefold()
                            await search_input.fill(search_query)
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                if (any(query.strip().casefold() == expected_query for query, _ in search_payloads)
                                        or expected_query in failed_queries):
                                    break
                                await page.wait_for_timeout(100)
                            for query, payload in search_payloads:
                                if query.strip().casefold() == expected_query:
                                    answered_queries.add(expected_query)
                                    candidates.extend(
                                        candidate for candidate in _profile_candidates_from_search_payload(payload, player_name)
                                        if candidate.get("source_player_id") not in excluded_source_ids
                                    )
                            candidates = list({candidate["source_player_id"]: candidate for candidate in candidates}.values())
                            if len(candidates) == 1:
                                return "ok", candidates, None
                        operation_stage["value"] = "search_results"
                        body = (await page.locator("body").inner_text()).casefold()
                        if any(token in body for token in ("captcha", "verify you are human", "checking your browser", "attention required")):
                            return "blocked", [], "ANTI_BOT_CHALLENGE"
                        if candidates:
                            return "ok", candidates, None
                        anchors = await page.locator('a[href*="/player/"]').evaluate_all(
                            "els => els.map(a => ({href: a.href, text: (a.innerText || a.textContent || '').trim()}))"
                        )
                        # A timeout or failed search response is not evidence that
                        # the player has no Flashscore profile. Only a parsed 200
                        # response for every query variant can produce not_found.
                        failure_reason = _profile_search_failure_reason(
                            search_queries, answered_queries, failed_queries, parse_failed_queries
                        )
                        if failure_reason:
                            return ("blocked" if failure_reason == "SEARCH_HTTP_ERROR" else "source_unavailable"), [], failure_reason
                        return "ok", anchors, None
                    finally:
                        await browser.close()

            status, anchors, search_reason = asyncio.run(find_links())
        except Exception as exc:
            logger.info("Flashscore profile search unavailable for %s: %s (%s)",
                        player_name, type(exc).__name__, operation_stage["value"])
            status, anchors = "source_unavailable", []
            search_reason = _profile_search_exception_reason(operation_stage["value"], type(exc).__name__)
        if status != "ok":
            return {"status": status, "candidates": [], "reason": search_reason}
        candidates: dict[str, dict[str, str]] = {}
        for anchor in anchors:
            if anchor.get("profile_url") and anchor.get("source_player_id"):
                if anchor.get("source_player_id") in excluded_source_ids:
                    continue
                candidates[anchor["source_player_id"]] = anchor
                continue
            text = unescape(str(anchor.get("text") or "")).strip()
            href = urljoin(home_url, str(anchor.get("href") or ""))
            if not text or not _player_name_matches(player_name, text):
                continue
            if not _PROFILE_ID_RE.search(urlparse(href).path):
                continue
            profile_url = href.split("?", 1)[0].rstrip("/") + "/results/"
            try:
                _validate_profile_url(profile_url)
            except ValueError:
                continue
            player_id = _PROFILE_ID_RE.search(urlparse(profile_url).path).group(1)
            candidates[player_id] = {"name": text, "profile_url": profile_url, "source_player_id": player_id}
        found = list(candidates.values())
        if len(found) == 1:
            return {"status": "matched", "profile_url": found[0]["profile_url"], "candidate": found[0], "candidates": found}
        return {"status": "ambiguous" if found else "not_found", "candidates": found[:10],
                "reason": "MULTIPLE_PROFILE_CANDIDATES" if found else "SEARCH_COMPLETED_NO_MATCH"}

    def _fetch_rendered_profile(self, url: str, stop_before_date: datetime | None = None, page_callback=None,
                                resume_documents=None, source_access=None) -> tuple[Optional[str], str, list[str], Optional[str], bool, list[dict[str, Any]]]:
        """Load the public profile and walk its current paged results feed.

        The profile's visible button currently calls a retired ``..._s`` feed
        shape and receives ``0`` even though the profile advertises a career
        count. The current public feed uses zero-based pages without that suffix.
        """
        access = source_access or nullcontext
        initial_html: Optional[str] = None
        try:
            import asyncio
            from playwright.async_api import async_playwright

            async def load() -> tuple[str, list[str], Optional[str], bool, list[dict[str, Any]]]:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(**_browser_launch_options())
                    try:
                        context = await browser.new_context(locale="en-US", timezone_id="UTC")
                        await _install_flashscore_request_filter_async(context)
                        page = await context.new_page()
                        with access():
                            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        if response and response.status in (403, 429):
                            return "", [], f"HTTP_{response.status}_ANTI_BOT_BLOCKED", False, []
                        if response and response.status != 200:
                            return "", [], f"HTTP_{response.status}_PROFILE_UNAVAILABLE", False, []
                        redirected_allowed, redirect_reason = DomainFilter.is_allowed(page.url)
                        if not redirected_allowed:
                            return "", [], f"REDIRECT_REJECTED:{redirect_reason}", False, []
                        try:
                            await page.wait_for_function(
                                "() => Boolean(window.cjs?.initialFeeds?.['results_s']?.data)", timeout=10000
                            )
                        except Exception:
                            return "", [], "RESULTS_SECTION_NOT_LOADED", False, []
                        initial = await page.evaluate("""() => {
                          const feed = window.cjs?.initialFeeds?.['results_s'];
                          return feed ? {data: feed.data || '', allEventsCount: Number(feed.allEventsCount) || null,
                            name: window.teamPageEnvironment?.participantName || null} : null;
                        }""")
                        if not initial or not initial.get("data"):
                            return "", [], "RESULTS_SECTION_NOT_LOADED", False, []
                        initial_feed = str(initial["data"])
                        player_match = _PROFILE_ID_RE.search(urlparse(url).path)
                        player_id = player_match.group(1) if player_match else ""
                        initial_rows, _ = _feed_records(initial_feed)
                        known_match_ids: set[str] = set()
                        known_singles_match_ids: set[str] = set()
                        for fields, event_context in initial_rows:
                            if fields.get("AA"):
                                known_match_ids.add(fields["AA"])
                                if not _is_doubles_result(fields, event_context):
                                    known_singles_match_ids.add(fields["AA"])
                        country_id = _profile_country_id(initial_feed, player_id)
                        if country_id is None:
                            return "", [], "PLAYER_COUNTRY_CODE_UNRESOLVED", False, []
                        expected_count = initial.get("allEventsCount")
                        feeds = [initial_feed]
                        page_documents: list[dict[str, Any]] = []
                        saved_pages = list(resume_documents or [])
                        cutoff_epoch = int(stop_before_date.timestamp()) if stop_before_date else None
                        pagination_complete = False
                        pagination_reason = "page_limit_reached"
                        overlap_reached = False
                        paged_data_received = False
                        initial_dates = [int(v) for v in re.findall(r"(?:^|[¬~])AD÷(\d+)", initial_feed)]
                        if cutoff_epoch and any(value < cutoff_epoch for value in initial_dates):
                            overlap_reached = True
                            pagination_reason = "30_day_overlap_reached"

                        async def archive_feed_page(page_index: int, feed_url: str, body: str,
                                                    response_status: Optional[int] = None,
                                                    page_reason: Optional[str] = None,
                                                    reset_checkpoint: bool = False,
                                                    terminal_probe_count: int = 0,
                                                    terminal_probe_status: Optional[int] = None,
                                                    terminal_probe_error: Optional[str] = None) -> None:
                            rows, _ = _feed_records(body) if body and body != "0" else ([], 0)
                            ids = sorted({fields["AA"] for fields, _context in rows if fields.get("AA")})
                            known_match_ids.update(ids)
                            known_singles_match_ids.update(
                                fields["AA"] for fields, event_context in rows
                                if fields.get("AA") and not _is_doubles_result(fields, event_context)
                            )
                            if body and body != "0":
                                feeds.append(body)
                            document = {
                                "page_index": page_index,
                                "source_url": feed_url,
                                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                "html": body,
                                "result_feeds": [body] if body else [],
                                "match_ids": ids,
                                "advertised_event_count": expected_count,
                                "observed_unique_event_count": len(known_match_ids),
                                "observed_unique_singles_event_count": len(known_singles_match_ids),
                                "response_status": response_status,
                                "pagination_reason": page_reason,
                                "terminal_probe_count": terminal_probe_count,
                                "terminal_probe_status": terminal_probe_status,
                                "terminal_probe_error": terminal_probe_error,
                                "reset_checkpoint": reset_checkpoint,
                            }
                            if page_callback:
                                page_callback(document)
                            page_documents.append(document)

                        # The provider's count is a consistency signal, not a
                        # terminal marker; observed results can exceed it.
                        max_pages = min(120, max(40, ((int(expected_count or 0) + 34) // 35) + 20))

                        async def request_feed_page(feed_url: str) -> tuple[int, str, Optional[str]]:
                            response_status, body, response_error = 0, "", None
                            for request_attempt in range(3):
                                try:
                                    if source_access is None:
                                        request_delay = max(2.0, float(os.getenv("FLASHSCORE_TENNIS_REQUEST_DELAY_SECONDS", "2.0")))
                                        await page.wait_for_timeout(round(request_delay * 1000))
                                    with access():
                                        async def read_feed_response():
                                            response = await context.request.get(
                                                feed_url,
                                                headers={
                                                    "x-fsign": os.getenv("FLASHSCORE_X_FSIGN", "SW9D1eZo"),
                                                    "referer": "https://www.flashscore.com/",
                                                },
                                                timeout=20000,
                                            )
                                            return response.status, (await response.text()).strip()

                                        response_status, body = await asyncio.wait_for(
                                            read_feed_response(), timeout=25
                                        )
                                    return response_status, body, None
                                except Exception as exc:
                                    response_error = f"{type(exc).__name__}: {str(exc)[:240]}"
                                    if "FLASHSCORE_SOURCE_LOCK_TIMEOUT" in response_error or "FLASHSCORE_SOURCE_REQUEST_EXPIRED" in response_error:
                                        return 0, "", response_error
                                    if request_attempt < 2:
                                        await page.wait_for_timeout((request_attempt + 1) * 2000)
                            return response_status, body, response_error

                        if not overlap_reached:
                            # Re-read the prefix to reconstruct a complete profile
                            # snapshot after worker restart. Previously archived
                            # pages are replayed only if the current first page
                            # still has identical content.
                            for feed_page in range(0, max_pages):
                                feed_url = _profile_feed_page_url(player_id, country_id, feed_page)
                                if (feed_page > 0 and saved_pages and feed_page < len(saved_pages) and
                                    saved_pages[feed_page].get("url") != feed_url):
                                    saved_pages = []
                                if feed_page > 0 and saved_pages and feed_page < len(saved_pages):
                                    saved = saved_pages[feed_page]
                                    body = str(saved.get("html") or "")
                                    ids = set(saved.get("match_ids") or [])
                                    known_match_ids.update(ids)
                                    if body and body != "0":
                                        saved_rows, _ = _feed_records(body)
                                        known_singles_match_ids.update(
                                            fields["AA"] for fields, event_context in saved_rows
                                            if fields.get("AA") and not _is_doubles_result(fields, event_context)
                                        )
                                        feeds.append(body)
                                        paged_data_received = True
                                    page_documents.append({
                                        "page_index": feed_page + 1, "source_url": saved.get("url"),
                                        "retrieved_at": saved.get("retrieved_at"), "html": body,
                                        "result_feeds": saved.get("result_feeds") or [],
                                        "match_ids": sorted(ids), "response_status": 200,
                                        "pagination_reason": saved.get("pagination_reason"),
                                        "terminal_probe_count": saved.get("terminal_probe_count", 0),
                                        "terminal_probe_status": saved.get("terminal_probe_status"),
                                        "terminal_probe_error": saved.get("terminal_probe_error"),
                                        "advertised_event_count": saved.get("advertised_event_count"),
                                        "observed_unique_event_count": saved.get("observed_unique_event_count"),
                                        "observed_unique_singles_event_count": saved.get("observed_unique_singles_event_count"),
                                        "archived_path": saved.get("archived_path"),
                                    })
                                    if body in ("", "0"):
                                        count_matches = _advertised_singles_count_matches(
                                            expected_count, known_singles_match_ids
                                        )
                                        terminal_confirmed = (
                                            int(saved.get("terminal_probe_count") or 0) >= 2
                                            and saved.get("response_status", 200) == 200
                                        )
                                        pagination_complete = terminal_confirmed and count_matches
                                        pagination_reason = (
                                            "source_terminal_empty_response_confirmed" if pagination_complete
                                            else "source_terminal_empty_response_count_mismatch_confirmed"
                                            if terminal_confirmed else "source_terminal_empty_unconfirmed"
                                        )
                                        break
                                    continue
                                allowed, _ = DomainFilter.is_allowed(feed_url, allowlist={"flashscore.ninja"})
                                if not allowed:
                                    pagination_reason = "pagination_response_domain_rejected"
                                    break
                                response_status, body, response_error = await request_feed_page(feed_url)
                                if response_status in (403, 429):
                                    await archive_feed_page(feed_page + 1, feed_url, body, response_status,
                                                            f"HTTP_{response_status}_ANTI_BOT_BLOCKED")
                                    pagination_reason = f"HTTP_{response_status}_ANTI_BOT_BLOCKED"
                                    break
                                if response_status != 200:
                                    pagination_reason = _profile_page_failure_reason(response_status, response_error)
                                    failure = {
                                        "page_index": feed_page + 1,
                                        "source_url": feed_url,
                                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                        "html": str(response_error or body),
                                        "result_feeds": [],
                                        "match_ids": [],
                                        "response_status": response_status,
                                        "response_error": response_error,
                                        "pagination_reason": pagination_reason,
                                    }
                                    if page_callback:
                                        page_callback(failure)
                                    page_documents.append(failure)
                                    break
                                if body in ("", "0"):
                                    # HTTP 200 + "0" is not sufficient by itself: a transient or
                                    # malformed response can look identical to the feed's empty
                                    # terminal page. Confirm the same cursor once more before
                                    # declaring this accessible history complete.
                                    terminal_probe_count = 0
                                    terminal_probe_error = None
                                    terminal_probe_status = None
                                    if response_status == 200:
                                        terminal_probe_count = 1
                                        probe_status, probe_body, terminal_probe_error = await request_feed_page(feed_url)
                                        terminal_probe_status = probe_status
                                        if probe_status == 200 and probe_body in ("", "0"):
                                            terminal_probe_count = 2
                                        elif probe_status == 200 and probe_body not in ("", "0"):
                                            # A transient empty response was followed by real
                                            # records. Process those records as this page instead
                                            # of losing them or treating them as end-of-history.
                                            body = probe_body
                                            response_status = probe_status
                                            paged_data_received = True
                                            logger.warning("Flashscore empty page was not stable for %s; retaining recovered feed data", feed_url)
                                        else:
                                            terminal_probe_error = terminal_probe_error or f"HTTP_{probe_status}"
                                    if body in ("", "0"):
                                        # allEventsCount describes singles. The paged feed also
                                        # contains doubles and mixed doubles.
                                        count_matches = _advertised_singles_count_matches(
                                            expected_count, known_singles_match_ids
                                        )
                                        reason = ("empty_response_before_first_page_data" if
                                            feed_page == 0 and not paged_data_received and (expected_count or known_match_ids)
                                            else "source_terminal_empty_response_confirmed" if
                                            terminal_probe_count == 2 and count_matches
                                            else "source_terminal_empty_response_count_mismatch_confirmed" if
                                            terminal_probe_count == 2
                                            else "source_terminal_empty_unconfirmed")
                                        await archive_feed_page(feed_page + 1, feed_url, body,
                                            response_status if response_status == 200 else 0, reason,
                                            terminal_probe_count=terminal_probe_count,
                                            terminal_probe_status=terminal_probe_status,
                                            terminal_probe_error=terminal_probe_error)
                                        if terminal_probe_count == 2 and reason == "source_terminal_empty_response_confirmed":
                                            pagination_complete = True
                                            pagination_reason = reason
                                        else:
                                            pagination_reason = reason
                                            if terminal_probe_error:
                                                pagination_reason += f":{terminal_probe_error}"
                                        break
                                rows, _ = _feed_records(body)
                                response_ids = {fields.get("AA") for fields, _context in rows if fields.get("AA")}
                                if not response_ids:
                                    await archive_feed_page(feed_page + 1, feed_url, body, response_status,
                                                            "pagination_unsupported_response")
                                    pagination_reason = "pagination_unsupported_response"
                                    break
                                paged_data_received = True
                                reset_checkpoint = False
                                if feed_page == 0 and saved_pages:
                                    saved_first = str(saved_pages[0].get("html") or "")
                                    if body != saved_first:
                                        saved_pages = []
                                        reset_checkpoint = True
                                new_ids = response_ids - known_match_ids
                                await archive_feed_page(feed_page + 1, feed_url, body, response_status,
                                                        reset_checkpoint=reset_checkpoint)
                                if not _page_has_pagination_progress(feed_page, new_ids):
                                    pagination_reason = "pagination_response_no_new_match_ids"
                                    break
                                pagination_reason = "page_loaded"
                                if cutoff_epoch:
                                    response_dates = [int(v) for v in re.findall(r"(?:^|[¬~])AD÷(\d+)", body)]
                                    if any(value < cutoff_epoch for value in response_dates):
                                        overlap_reached = True
                                        pagination_reason = "30_day_overlap_reached"
                                        break
                            else:
                                pagination_reason = "page_limit_reached"
                        else:
                            pagination_complete = True
                        body_text = (await page.locator("body").inner_text()).casefold()
                        if any(token in body_text for token in (
                            "captcha", "verify you are human", "checking your browser",
                            "just a moment...", "attention required | cloudflare",
                        )):
                            return "", [], "CAPTCHA_CHALLENGE_ABORTED", False, []
                        if page_documents:
                            page_documents[-1]["pagination_reason"] = pagination_reason
                            page_documents[-1]["advertised_event_count"] = expected_count
                            page_documents[-1]["observed_unique_event_count"] = len(known_match_ids)
                            page_documents[-1]["observed_unique_singles_event_count"] = len(known_singles_match_ids)
                        page_html = await page.content()
                        name = initial.get("name") or await page.evaluate(
                            "window.teamPageEnvironment?.participantName || null"
                        )
                        return page_html, feeds, name, pagination_complete or overlap_reached, page_documents
                    finally:
                        try:
                            await asyncio.wait_for(browser.close(), timeout=10)
                        except Exception:
                            logger.warning("Timed out closing Flashscore browser for %s", url)

            page_html, feeds, name, pagination_complete, page_documents = asyncio.run(
                asyncio.wait_for(load(), timeout=300)
            )
            if page_html == "":
                return None, name or "BROWSER_FETCH_FAILED", [], None, False, []
            return page_html, "OK", feeds or _feed_payloads(page_html), name, pagination_complete, page_documents
        except Exception as exc:
            if type(exc).__name__ == "TimeoutError":
                logger.warning("Flashscore profile timed out for %s", url)
                return None, "PROFILE_TIMEOUT", [], None, False, []
            logger.warning("Flashscore browser fetch failed for %s: %s: %s", url, type(exc).__name__, str(exc)[:240])
            return None, f"BROWSER_FETCH_FAILED:{type(exc).__name__}", [], None, False, []

    def import_profiles(self, urls: Iterable[str], delay_seconds: float = 2.0,
                        owner_job_id=None, owner_job_token: str | None = None, page_checkpoint=None,
                        resume_pages=None, source_access=None) -> dict[str, Any]:
        """Fetch a bounded list sequentially and persist results without overwriting history."""
        results = []
        for index, url in enumerate(urls):
            if index and delay_seconds > 0:
                time.sleep(delay_seconds)
            result = self.scrape_profile(url, page_checkpoint=page_checkpoint,
                                         resume_pages=resume_pages, source_access=source_access)
            imported = self._persist_result(result, owner_job_id=owner_job_id,
                                            owner_job_token=owner_job_token) if result.profile else 0
            results.append({
                "url": result.url,
                "status": result.status,
                "reason": result.reason,
                "player_id": result.profile.source_player_id if result.profile else None,
                "matches_parsed": len(result.profile.matches) if result.profile else 0,
                "matches_imported": imported,
                "matches_skipped": result.profile.skipped_rows if result.profile else 0,
                "rows_skipped_by_reason": result.profile.skipped_row_reasons if result.profile else {},
                "duplicate_source_rows": result.profile.duplicate_source_rows if result.profile else 0,
                "pagination_complete": result.profile.pagination_complete if result.profile else False,
                "history_scope": result.profile.history_scope if result.profile else "partial",
                "pagination_reason": result.profile.pagination_reason if result.profile else result.reason,
                "terminal_probe_count": result.profile.terminal_probe_count if result.profile else 0,
                "advertised_event_count": result.profile.advertised_event_count if result.profile else None,
                "observed_unique_event_count": result.profile.observed_unique_event_count if result.profile else 0,
                "observed_unique_singles_event_count": result.profile.observed_unique_singles_event_count if result.profile else 0,
                "advertised_count_matches_singles": (
                    result.profile.advertised_event_count == result.profile.observed_unique_singles_event_count
                    if result.profile and result.profile.advertised_event_count is not None else None
                ),
                "content_hash": result.content_hash,
                "archived_path": result.archived_path,
                "archived_page_paths": list(result.archived_page_paths),
            })
        return {"parser_version": PARSER_VERSION, "profiles": results}

    @staticmethod
    def _persist_result(result: ScrapeResult, owner_participant_id=None, owner_job_token: str | None = None,
                        owner_job_id=None) -> int:
        if not result.profile:
            return 0
        from db.connection import SessionLocal
        from db.models.external_tennis import ExtTennisMatch, ExtTennisMatchObservation, ExtTennisPlayer, TennisPlayerRefreshJob, FlashscoreImportJob
        from .player_profiles import persist_profile_facts

        profile = result.profile
        matches_added = 0
        with SessionLocal() as session:
            if owner_job_id is not None and owner_job_token:
                owner = session.query(FlashscoreImportJob).filter_by(
                    id=owner_job_id, attempt_token=owner_job_token, status="running"
                ).filter(FlashscoreImportJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
                if owner is None:
                    return 0
            if owner_participant_id is not None and owner_job_token:
                owner = session.query(TennisPlayerRefreshJob).filter_by(
                    participant_id=owner_participant_id, attempt_token=owner_job_token, status="running"
                ).filter(TennisPlayerRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first()
                if owner is None:
                    return 0
            player_ids = {profile.source_player_id: profile.full_name}
            for item in profile.matches:
                if item.home_player_id:
                    player_ids.setdefault(item.home_player_id, item.home_name)
                if item.away_player_id:
                    player_ids.setdefault(item.away_player_id, item.away_name)
            for external_id, name in player_ids.items():
                existing = session.query(ExtTennisPlayer).filter_by(source="flashscore", source_player_id=external_id).first()
                if existing is None:
                    session.add(ExtTennisPlayer(
                        source="flashscore", source_player_id=external_id,
                        full_name=(name or f"Flashscore player {external_id}")[:255],
                    ))
            session.flush()
            for rejected in profile.rejected_source_matches:
                rejection_payload = {
                    "parse_status": "excluded_from_result_facts",
                    "reason": rejected["reason"],
                    "source_match_id": rejected["source_match_id"],
                    "source_player_id": profile.source_player_id,
                    "parser_version": PARSER_VERSION,
                    "profile_content_hash": result.content_hash,
                }
                rejection_hash = _observation_hash(rejection_payload)
                previous = session.query(ExtTennisMatchObservation).filter_by(
                    source="flashscore", source_match_id=rejected["source_match_id"],
                ).order_by(ExtTennisMatchObservation.available_at.desc()).first()
                if previous is None or previous.observation_hash != rejection_hash:
                    session.add(ExtTennisMatchObservation(
                        source="flashscore",
                        source_match_id=rejected["source_match_id"],
                        observation_hash=rejection_hash,
                        available_at=result.retrieved_at,
                        source_url=result.url,
                        source_content_hash=result.content_hash,
                        parser_version=PARSER_VERSION,
                        payload=rejection_payload,
                    ))
            observation_inserted: dict[str, bool] = {}
            for item in profile.matches:
                payload = _observation_payload(item)
                observation_hash = _observation_hash(payload)
                latest_observation = session.query(ExtTennisMatchObservation).filter_by(
                    source="flashscore", source_match_id=item.source_match_id,
                ).order_by(ExtTennisMatchObservation.available_at.desc()).first()
                is_new_observation = latest_observation is None or latest_observation.observation_hash != observation_hash
                observation_inserted[item.source_match_id] = is_new_observation
                if is_new_observation:
                    session.add(ExtTennisMatchObservation(
                        source="flashscore",
                        source_match_id=item.source_match_id,
                        observation_hash=observation_hash,
                        available_at=result.retrieved_at,
                        source_url=result.url,
                        source_content_hash=result.content_hash,
                        parser_version=PARSER_VERSION,
                        payload={
                            **payload,
                            "pagination_complete": result.profile.pagination_complete,
                            "history_scope": result.profile.history_scope,
                            "pagination_reason": result.profile.pagination_reason,
                            "terminal_probe_count": result.profile.terminal_probe_count,
                            "advertised_event_count": result.profile.advertised_event_count,
                            "observed_unique_event_count": result.profile.observed_unique_event_count,
                        },
                    ))
                projection_available_at = (
                    result.retrieved_at if is_new_observation else
                    (latest_observation.available_at if latest_observation else result.retrieved_at)
                )
                existing = session.query(ExtTennisMatch).filter_by(source="flashscore", source_match_id=item.source_match_id).first()
                if existing is not None:
                    # ExtTennisMatch is the current materialized projection;
                    # immutable, timestamped versions live in observations.
                    # Refresh this projection after a corrected parser so new
                    # consumers do not keep stale gender/format/result fields.
                    existing.played_at = item.played_at
                    existing.available_at = projection_available_at
                    existing.tour_level = item.tour_level
                    existing.tournament = item.tournament[:255]
                    existing.surface = item.surface
                    existing.best_of = item.best_of
                    existing.round_name = item.round_name[:32]
                    existing.gender = item.gender
                    existing.winner_name = item.winner_name[:255]
                    existing.loser_name = item.loser_name[:255]
                    existing.winner_player_id = item.winner_player_id
                    existing.loser_player_id = item.loser_player_id
                    existing.score = item.score[:64]
                    existing.stats = {
                        **(existing.stats or {}),
                        "source_url": item.source_url or result.url,
                        "source_retrieved_at": projection_available_at.isoformat(),
                        "source_content_hash": result.content_hash,
                        "parser_version": PARSER_VERSION,
                        "pagination_complete": result.profile.pagination_complete,
                        "history_scope": result.profile.history_scope,
                        "raw_fields": item.raw_fields,
                    }
                    continue
                session.add(ExtTennisMatch(
                    source="flashscore",
                    source_match_id=item.source_match_id,
                    played_at=item.played_at,
                    available_at=result.retrieved_at,
                    tour_level=item.tour_level,
                    tournament=item.tournament[:255],
                    surface=item.surface,
                    best_of=item.best_of,
                    round_name=item.round_name[:32],
                    gender=item.gender,
                    winner_name=item.winner_name[:255],
                    loser_name=item.loser_name[:255],
                    winner_player_id=item.winner_player_id,
                    loser_player_id=item.loser_player_id,
                    score=item.score[:64],
                    stats={
                        "source_url": result.url,
                        "source_retrieved_at": result.retrieved_at.isoformat(),
                        "source_content_hash": result.content_hash,
                        "parser_version": PARSER_VERSION,
                        "pagination_complete": result.profile.pagination_complete,
                        "history_scope": result.profile.history_scope,
                        "raw_fields": item.raw_fields,
                    },
                ))
                matches_added += 1
            facts_added = persist_profile_facts(session, result, owner_participant_id)
            logger.info(
                "Flashscore profile imported player=%s matches_added=%s facts_added=%s complete=%s",
                profile.source_player_id, matches_added, facts_added, profile.pagination_complete,
            )
            session.commit()
        return matches_added

