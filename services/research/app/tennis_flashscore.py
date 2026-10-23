"""Flashscore tennis player-history scraper and conservative feed parser.

Player history pages are read from the public profile and its ordinary sports
feed.  The current website's visible "show more" handler still calls a retired
feed shape, so pagination uses the same public feed with its current page
parameters and archives every returned response before parsing it.
"""

from __future__ import annotations

import hashlib
import asyncio
import atexit
import html as html_lib
import json
import logging
import os
import re
import time
import threading
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

_profile_http_client = None
_profile_http_client_lock = threading.Lock()
_profile_page_http_client = None
_profile_page_http_client_lock = threading.Lock()


def _run_browser_awaitable(awaitable):
    """Run a Playwright coroutine safely from synchronous or async callers.

    ``asyncio.run`` cannot nest inside an event loop. Most durable workers call
    the synchronous importer from a thread, but compatibility/API call paths
    may invoke it while an asyncio loop is active. In that case, give the
    coroutine a dedicated thread and event loop instead of failing the job.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    import queue

    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def run_in_thread():
        try:
            result_queue.put((True, asyncio.run(awaitable)))
        except BaseException as exc:
            result_queue.put((False, exc))

    thread = threading.Thread(target=run_in_thread, name="flashscore-browser-loop", daemon=True)
    thread.start()
    thread.join()
    succeeded, result = result_queue.get()
    if succeeded:
        return result
    raise result


def _get_profile_http_client():
    """Reuse a small thread-safe keep-alive pool for stateless player search GETs."""
    global _profile_http_client
    if _profile_http_client is None:
        with _profile_http_client_lock:
            if _profile_http_client is None:
                import httpx

                _profile_http_client = httpx.Client(
                    timeout=10.0,
                    follow_redirects=False,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.flashscore.com/tennis/",
                    },
                    limits=httpx.Limits(
                        max_connections=2,
                        max_keepalive_connections=2,
                        keepalive_expiry=30.0,
                    ),
                )
    return _profile_http_client


def _get_profile_page_http_client():
    """Reuse a bounded HTML/feed HTTP pool without starting Chromium."""
    global _profile_page_http_client
    if _profile_page_http_client is None:
        with _profile_page_http_client_lock:
            if _profile_page_http_client is None:
                import httpx

                _profile_page_http_client = httpx.Client(
                    timeout=httpx.Timeout(25.0, connect=10.0),
                    follow_redirects=False,
                    headers={
                        "User-Agent": SandboxedFetcher.USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
                    },
                    limits=httpx.Limits(
                        max_connections=2,
                        max_keepalive_connections=2,
                        keepalive_expiry=30.0,
                    ),
                )
    return _profile_page_http_client


def _profile_http_get(url: str, source_access=None, *, feed: bool = False) -> tuple[int, str, str, Optional[str]]:
    """Fetch one allowlisted Flashscore page/feed with a source slot per GET."""
    from contextlib import nullcontext
    import httpx

    allowlist = {"flashscore.ninja"} if feed else None
    current_url = url
    for redirect_no in range(6):
        allowed, reason = DomainFilter.is_allowed(current_url, allowlist=allowlist)
        if not allowed:
            return 0, "", current_url, f"REDIRECT_REJECTED:{reason}"
        headers = ({
            "Accept": "text/plain,*/*;q=0.9",
            "Referer": FLASHSCORE_TENNIS_HOME_URL,
            "x-fsign": os.getenv("FLASHSCORE_X_FSIGN", "SW9D1eZo"),
        } if feed else {"Referer": FLASHSCORE_TENNIS_HOME_URL})
        try:
            if source_access is None:
                time.sleep(max(2.0, float(os.getenv("FLASHSCORE_TENNIS_REQUEST_DELAY_SECONDS", "2.0"))))
            with (source_access or nullcontext)():
                response = _get_profile_page_http_client().get(current_url, headers=headers)
        except httpx.TimeoutException as exc:
            return 0, "", current_url, f"TimeoutError:{str(exc)[:180]}"
        except httpx.HTTPError as exc:
            return 0, "", current_url, f"{type(exc).__name__}:{str(exc)[:180]}"

        # httpx Response exposes is_redirect across supported versions; the
        # separate is_permanent_redirect property belongs to other clients.
        if response.is_redirect:
            location = response.headers.get("location")
            if not location or redirect_no >= 5:
                return response.status_code, "", current_url, "REDIRECT_LIMIT_EXCEEDED"
            current_url = urljoin(current_url, location)
            continue
        if len(response.content) > 5 * 1024 * 1024:
            return response.status_code, "", current_url, "PROFILE_PAGE_TOO_LARGE"
        return response.status_code, response.text, str(response.url), None
    return 0, "", current_url, "REDIRECT_LIMIT_EXCEEDED"


def _close_profile_http_client() -> None:
    global _profile_http_client, _profile_page_http_client
    with _profile_http_client_lock:
        client, _profile_http_client = _profile_http_client, None
    with _profile_page_http_client_lock:
        page_client, _profile_page_http_client = _profile_page_http_client, None
    if client is not None:
        try:
            client.close()
        except Exception:
            logger.debug("Could not close Flashscore search HTTP pool", exc_info=True)
    if page_client is not None:
        try:
            page_client.close()
        except Exception:
            logger.debug("Could not close Flashscore profile HTTP pool", exc_info=True)


atexit.register(_close_profile_http_client)

PARSER_VERSION = "flashscore-tennis-player-v9"
FLASHSCORE_TENNIS_HOME_URL = "https://www.flashscore.com/tennis/"
_CONFIRMED_TERMINAL_REASONS = {
    "source_terminal_empty_response_confirmed",
    "source_terminal_empty_response_count_mismatch_confirmed",
}
_PROFILE_ID_RE = re.compile(r"/player/[^/]+/([A-Za-z0-9]+)/")
_PROFILE_PARSER_VERSION_RE = re.compile(r"^flashscore-tennis-player-v\d+$")


def is_compatible_profile_parser_version(value: str | None) -> bool:
    """Archived page payloads remain reusable across parser revisions."""
    return bool(_PROFILE_PARSER_VERSION_RE.fullmatch(str(value or "")))
_FEED_RE = re.compile(
    r'cjs\.initialFeeds\[\s*["\'](?:summary-)?results_s["\']\s*\]\s*=\s*'
    r"\{\s*data:\s*`(.*?)`\s*(?:,\s*(?:change|\w+)\s*:|\})",
    re.DOTALL,
)
_FEED_ASSIGNMENT_RE = re.compile(
    r'cjs\.initialFeeds\[\s*["\'](?:summary-)?results_s["\']\s*\]\s*=\s*'
    r"\{\s*data:\s*`(.*?)`\s*,\s*allEventsCount\s*:\s*(\d+)\s*,?\s*\}",
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

# Flashscore player names are not transliterated consistently across tours and
# locales. Keep a small set of common spellings for comparison (e.g. Чайковская
# may appear as Chaykovskaya, Caykovskaya, or Tchaikovskaya). These variants are
# used for identity matching only; source names and IDs remain unchanged.
_TRANSLITERATION_ALTERNATIVES = (
    ("shch", ("shch", "sch", "sh")),
    ("zh", ("zh", "j")),
    ("kh", ("kh", "h")),
    ("ch", ("ch", "c", "tch")),
    ("sh", ("sh", "s")),
    ("ts", ("ts", "c")),
    ("yu", ("yu", "iu")),
    ("ya", ("ya", "ia")),
    ("ye", ("ye", "e")),
    ("yo", ("yo", "e")),
    ("j", ("j", "y", "i")),
)


def _search_tokens(value: str) -> list[str]:
    from tennis_adapter.normalizer import normalize_russian_player_name

    normalized = normalize_russian_player_name(value).casefold().replace("ё", "е")
    return re.findall(r"[a-z0-9]+", normalized.translate(_CYRILLIC_TO_LATIN))


def _transliteration_variants(token: str) -> set[str]:
    """Return bounded, common Latin spellings for one normalized name token."""
    variants = {token}
    for source, alternatives in _TRANSLITERATION_ALTERNATIVES:
        pending = list(variants)
        visited: set[str] = set()
        while pending:
            value = pending.pop()
            if value in visited:
                continue
            visited.add(value)
            start = 0
            while True:
                index = value.find(source, start)
                if index < 0:
                    break
                for replacement in alternatives:
                    # Avoid recursive expansions such as ch -> tch -> ttch.
                    if replacement != source and source not in replacement:
                        candidate = value[:index] + replacement + value[index + len(source):]
                        if candidate not in variants:
                            variants.add(candidate)
                            pending.append(candidate)
                start = index + 1
                if len(variants) >= 64:
                    break
            if len(variants) >= 64:
                break
        if len(variants) >= 64:
            break
    return variants


def _name_word_groups(value: str) -> list[tuple[str, set[str]]]:
    """Return (source word, spelling variants), preserving one-letter initials."""
    from tennis_adapter.normalizer import clean_player_name

    # NFKD decomposes Cyrillic й into и + COMBINING BREVE. Removing combining
    # marks without protecting it silently turns Чайковская into Чаиковская,
    # which then fails against common Flashscore spellings Caykovskaya and
    # Chaykovskaya. Keep й as a placeholder through accent folding.
    protected_yi = "\ue000"
    source = clean_player_name(value).casefold().replace("ё", "е").replace("й", protected_yi)
    normalized = unicodedata.normalize("NFKD", source)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace(protected_yi, "й")
    words = re.findall(r"[a-zа-я0-9]+", normalized)
    result = []
    for word in words:
        token = word.translate(_CYRILLIC_TO_LATIN)
        if token:
            variants = _transliteration_variants(token)
            # Flashscore uses i, y, and occasionally j for Russian й. Expand
            # only positions that came from й, rather than every Latin i in a
            # surname, so unrelated names do not become auto-confirmable.
            yi_positions = [
                (len(word[:index].translate(_CYRILLIC_TO_LATIN)),
                 len(char.translate(_CYRILLIC_TO_LATIN)))
                for index, char in enumerate(word) if char == "й"
            ]
            if yi_positions:
                expanded = set(variants)
                for index, width in yi_positions:
                    for spelling in ("i", "y", "j"):
                        alternate = token[:index] + spelling + token[index + width:]
                        expanded.update(_transliteration_variants(alternate))
                variants = expanded
            result.append((word, variants))
    return result


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


def _search_flashscore_player_endpoint(
    player_name: str,
    search_queries: list[str],
    excluded_source_ids: set[str] | None = None,
    source_access=None,
) -> dict[str, Any]:
    """Use the same read-only JSON endpoint emitted by Flashscore's search UI.

    The endpoint is discovered from the normal tennis page's browser traffic.
    A direct GET avoids launching Chromium for a tiny JSON lookup while preserving
    the site's own player/sport filtering. The optional source-slot factory is
    acquired separately for every HTTP request so the shared two-second spacing
    applies to each actual operation, not just to the outer player job. Any
    transport, HTTP or parse failure fails closed; it is never a confirmed empty
    search.
    """
    from contextlib import nullcontext
    import httpx
    excluded_source_ids = {str(value) for value in (excluded_source_ids or set())}
    endpoint = "https://s.livesport.services/api/v2/search/"
    allowed, reason = DomainFilter.is_allowed(endpoint, allowlist={"livesport.services"})
    if not allowed:
        return {"status": "source_unavailable", "candidates": [], "reason": reason}

    candidates: dict[str, dict[str, str]] = {}
    answered: set[str] = set()
    failed: set[str] = set()
    parse_failed: set[str] = set()
    candidate_counts = {"player_rows": 0, "valid_candidates": 0, "name_rejected": 0}
    try:
        client = _get_profile_http_client()
        for search_query in search_queries:
            query_key = " ".join(search_query.split()).casefold()
            try:
                slot = source_access() if source_access is not None else nullcontext()
                with slot:
                    response = client.get(endpoint, params={"q": search_query})
            except httpx.TimeoutException:
                failed.add(query_key)
                continue
            except httpx.HTTPError:
                failed.add(query_key)
                continue

            if response.status_code in (403, 429):
                return {"status": "blocked", "candidates": [],
                        "reason": f"HTTP_{response.status_code}_OR_429_BLOCKED"}
            if response.status_code != 200:
                failed.add(query_key)
                continue
            if len(response.content) > 5 * 1024 * 1024:
                parse_failed.add(query_key)
                continue
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                parse_failed.add(query_key)
                continue
            if not isinstance(payload, list):
                parse_failed.add(query_key)
                continue

            answered.add(query_key)
            player_rows = [
                row for row in payload if isinstance(row, dict)
                and str((row.get("type") or {}).get("id")) == "3"
                and str((row.get("sport") or {}).get("id")) == "2"
            ]
            candidate_counts["player_rows"] += len(player_rows)
            for row in player_rows:
                source_id, slug, candidate_name = str(row.get("id") or ""), str(row.get("url") or ""), str(row.get("name") or "").strip()
                if (re.fullmatch(r"[A-Za-z0-9]{8}", source_id) and
                        re.fullmatch(r"[A-Za-z0-9-]{1,96}", slug) and candidate_name):
                    candidate_counts["valid_candidates"] += 1
                    if not _player_name_matches(player_name, candidate_name):
                        candidate_counts["name_rejected"] += 1
            for candidate in _profile_candidates_from_search_payload(payload, player_name):
                source_id = candidate["source_player_id"]
                if source_id not in excluded_source_ids:
                    candidates[source_id] = candidate
            if len(candidates) == 1:
                found = next(iter(candidates.values()))
                return {"status": "matched", "profile_url": found["profile_url"],
                        "candidate": found, "candidates": [found],
                        "diagnostics": dict(candidate_counts)}

    except Exception as exc:
        logger.info("Flashscore profile endpoint unavailable for %s: %s",
                    player_name, type(exc).__name__)
        return {"status": "source_unavailable", "candidates": [],
                "reason": _profile_search_exception_reason("search_response", type(exc).__name__)}

    failure_reason = _profile_search_failure_reason(search_queries, answered, failed, parse_failed)
    if failure_reason:
        return {"status": "source_unavailable", "candidates": [], "reason": failure_reason}
    found = list(candidates.values())
    if len(found) == 1:
        return {"status": "matched", "profile_url": found[0]["profile_url"],
                "candidate": found[0], "candidates": found,
                "diagnostics": dict(candidate_counts)}
    no_match_reason = (
        "SEARCH_CANDIDATES_REJECTED_BY_NAME"
        if not found and candidate_counts["name_rejected"] else
        "MULTIPLE_PROFILE_CANDIDATES" if found else "SEARCH_COMPLETED_NO_MATCH"
    )
    return {"status": "ambiguous" if found else "not_found", "candidates": found[:10],
            "reason": no_match_reason, "diagnostics": dict(candidate_counts)}


def _player_name_matches(query_name: str, candidate_name: str) -> bool:
    """Match localized surname/initial displays to full names in search results."""
    query_groups, candidate_groups = _name_word_groups(query_name), _name_word_groups(candidate_name)
    query = [group[1] for group in query_groups]
    candidate = [group[1] for group in candidate_groups]
    if not query or not candidate:
        return False
    if len(query) == len(candidate) and all(a & b for a, b in zip(query, candidate)):
        return True
    initials = [group[1] for group in query_groups if len(group[0]) == 1]
    surnames = [group[1] for group in query_groups if len(group[0]) > 1]
    candidate_long = [group[1] for group in candidate_groups if len(group[0]) > 1]
    if not surnames or not candidate_long:
        return False
    matched = [
        token for token in surnames
        if any(token & other for other in candidate_long)
        or any(SequenceMatcher(None, left, right).ratio() >= 0.78
               for left in token for other in candidate_long for right in other)
    ]
    if initials:
        if not matched:
            return False
        remaining = [token for token in candidate_long if not any(token & surname for surname in matched)]
        return all(any(any(name.startswith(initial) for name in token)
                       for token in remaining for initial in initials_group)
                   for initials_group in initials)
    return len(matched) == len(surnames)


def is_exact_player_name_match(query_name: str, candidate_name: str) -> bool:
    """Return true only when both full names normalize to the same ordered tokens."""
    query = _name_word_groups(query_name)
    candidate = _name_word_groups(candidate_name)
    return bool(query and candidate and len(query) == len(candidate)
                and all(left[1] & right[1] for left, right in zip(query, candidate)))


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

    query = _name_word_groups(query_name)
    candidate = _name_word_groups(candidate_name)
    if len(query) != 2 or len(candidate) != 2:
        return False
    query_initials = [group for group in query if len(group[0]) == 1]
    query_surnames = [group for group in query if len(group[0]) > 1]
    if len(query_initials) != 1 or len(query_surnames) != 1:
        return False
    surname_variants = query_surnames[0][1]
    initial_variants = query_initials[0][1]
    candidate_surnames = [group for group in candidate if group[1] & surname_variants]
    candidate_given_names = [group for group in candidate if group not in candidate_surnames]
    return (
        len(candidate_surnames) == 1
        and len(candidate_given_names) == 1
        and len(candidate_given_names[0][0]) > 1
        and any(any(given.startswith(initial) for initial in initial_variants)
                for given in candidate_given_names[0][1])
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
    fetch_transport: Optional[str] = None
    fallback_reason: Optional[str] = None


def _integer(value: Optional[str]) -> Optional[int]:
    if value is None or not re.fullmatch(r"\d{1,12}", value.strip()):
        return None
    return int(value)


def _feed_payloads(html: str) -> list[str]:
    """Return results feeds found in the document (Flashscore may split by tab)."""
    return [match.group(1).replace("\\`", "`") for match in _FEED_RE.finditer(html)]


def _profile_initial_feed_from_html(html: str) -> tuple[Optional[str], Optional[int]]:
    """Read the server-rendered singles feed and its advertised count."""
    match = _FEED_ASSIGNMENT_RE.search(html or "")
    if match is None:
        return None, None
    return match.group(1).replace("\\`", "`"), int(match.group(2))


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
    return advertised_count is not None and int(advertised_count) == len(singles_ids)


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
                "count_gap": page_document.get("count_gap"),
                "fetch_transport": page_document.get("fetch_transport"),
                "fallback_reason": page_document.get("fallback_reason"),
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
                                "count_gap": page_document.get("count_gap"),
                                "fetch_transport": page_document.get("fetch_transport"),
                                "fallback_reason": page_document.get("fallback_reason"),
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
            diagnostics = page_documents[-1] if page_documents else {}
            return ScrapeResult(
                status=source_status, url=url, retrieved_at=retrieved_at,
                content_hash=None, reason=status,
                fetch_transport=diagnostics.get("fetch_transport"),
                fallback_reason=diagnostics.get("fallback_reason"),
            )
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
                "count_gap": page_documents[-1].get("count_gap") if page_documents else None,
                "fetch_transport": page_documents[-1].get("fetch_transport") if page_documents else None,
                "fallback_reason": page_documents[-1].get("fallback_reason") if page_documents else None,
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
                    "count_gap": page_document.get("count_gap"),
                    "fetch_transport": page_document.get("fetch_transport"),
                    "fallback_reason": page_document.get("fallback_reason"),
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
            fetch_transport=(page_documents[-1].get("fetch_transport") if page_documents else
                             "http" if status.startswith("PROFILE_HTTP") else None),
            fallback_reason=(page_documents[-1].get("fallback_reason") if page_documents else None),
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

    def resolve_player_profile(self, player_name: str, excluded_source_ids: set[str] | None = None,
                              source_access=None) -> dict[str, Any]:
        """Resolve a tennis player through Flashscore's observed read-only search endpoint."""
        search_queries = _profile_search_queries(player_name)
        if not search_queries:
            return {"status": "not_found", "candidates": []}
        # This is the JSON GET issued by Flashscore's normal tennis search UI.
        # Each GET obtains its own shared source slot; using the endpoint directly
        # avoids a Chromium startup and the intermittently inert search dialog.
        return _search_flashscore_player_endpoint(
            player_name, search_queries, excluded_source_ids=excluded_source_ids,
            source_access=source_access,
        )

    def _fetch_rendered_profile(self, url: str, stop_before_date: datetime | None = None, page_callback=None,
                                resume_documents=None, source_access=None) -> tuple[Optional[str], str, list[str], Optional[str], bool, list[dict[str, Any]]]:
        """Fetch profile results over HTTP, using Chromium only for a JS shell."""
        _validate_profile_url(url)
        status, profile_html, final_url, error = _profile_http_get(url, source_access)
        def http_failure(reason: str, *, fallback_reason: str | None = None):
            return None, reason, [], None, False, [{
                "diagnostic_only": True,
                "response_status": status,
                "fetch_transport": "http",
                "fallback_reason": fallback_reason,
                "pagination_reason": reason,
            }]

        if status in (403, 429):
            return http_failure(f"HTTP_{status}_ANTI_BOT_BLOCKED")
        if status != 200:
            return http_failure(f"PROFILE_HTTP_FETCH_FAILED:{error or f'HTTP_{status}'}")
        allowed, redirect_reason = DomainFilter.is_allowed(final_url)
        if not allowed:
            return http_failure(f"REDIRECT_REJECTED:{redirect_reason}")

        initial_feed, expected_count = _profile_initial_feed_from_html(profile_html)
        player_match = _PROFILE_ID_RE.search(urlparse(url).path)
        player_id = player_match.group(1) if player_match else ""
        feed_usable = (
            initial_feed is not None and
            _profile_country_id(initial_feed, player_id) is not None
        )
        if not feed_usable and expected_count != 0:
            if any(token in profile_html.casefold() for token in (
                "captcha", "verify you are human", "checking your browser",
                "just a moment...", "attention required | cloudflare",
            )):
                return http_failure("CAPTCHA_CHALLENGE_ABORTED", fallback_reason="HTTP_200_ANTIBOT_CHALLENGE")
            # A successful HTML response with no usable server feed can be a
            # JavaScript shell. Retry that case once through the existing
            # rendered path; anti-bot and network errors never reach here.
            fallback_reason = "HTTP_200_INITIAL_FEED_MISSING" if initial_feed is None else "HTTP_200_INITIAL_FEED_UNUSABLE"
            def fallback_page_callback(document):
                document["fetch_transport"] = "chromium_fallback"
                document["fallback_reason"] = fallback_reason
                if page_callback:
                    page_callback(document)

            result = self._fetch_rendered_profile_with_browser(
                url, stop_before_date=stop_before_date,
                page_callback=fallback_page_callback if page_callback else None,
                resume_documents=resume_documents, source_access=source_access,
            )
            _html, _status, _feeds, _name, _complete, documents = result
            for document in documents:
                document.setdefault("fetch_transport", "chromium_fallback")
                document.setdefault("fallback_reason", fallback_reason)
            if _html is None and not documents:
                documents.append({
                    "diagnostic_only": True,
                    "fetch_transport": "chromium_fallback",
                    "fallback_reason": fallback_reason,
                    "pagination_reason": _status,
                })
                result = (*result[:5], documents)
            return result

        return self._fetch_profile_http_archive(
            url, profile_html, initial_feed, expected_count,
            stop_before_date=stop_before_date, page_callback=page_callback,
            resume_documents=resume_documents, source_access=source_access,
            final_url=final_url,
        )

    def _fetch_profile_http_archive(self, url: str, profile_html: str, initial_feed: str,
                                    expected_count: int | None, stop_before_date=None,
                                    page_callback=None, resume_documents=None,
                                    source_access=None, final_url=None):
        """Walk archived player feeds with HTTP requests and no browser lock."""
        player_match = _PROFILE_ID_RE.search(urlparse(url).path)
        player_id = player_match.group(1) if player_match else ""
        initial_rows, _ = _feed_records(initial_feed)
        known_match_ids = {fields["AA"] for fields, _ in initial_rows if fields.get("AA")}
        known_singles_match_ids = {
            fields["AA"] for fields, context in initial_rows
            if fields.get("AA") and not _is_doubles_result(fields, context)
        }
        country_id = _profile_country_id(initial_feed, player_id)
        if country_id is None:
            return None, "PLAYER_COUNTRY_CODE_UNRESOLVED", [], None, False, []

        feeds = [initial_feed] if initial_feed else []
        documents: list[dict[str, Any]] = []
        saved_pages = list(resume_documents or [])
        cutoff_epoch = int(stop_before_date.timestamp()) if stop_before_date else None
        pagination_complete = False
        pagination_reason = "page_limit_reached"
        overlap_reached = False
        paged_data_received = False
        initial_dates = [int(value) for value in re.findall(r"(?:^|[¬~])AD÷(\d+)", initial_feed)]
        if cutoff_epoch and any(value < cutoff_epoch for value in initial_dates):
            overlap_reached = True
            pagination_reason = "30_day_overlap_reached"

        def archive_page(page_index, feed_url, body, response_status=200, page_reason=None,
                         terminal_probe_count=0, terminal_probe_status=None,
                         terminal_probe_error=None, response_error=None, reset_checkpoint=False):
            rows = _feed_records(body)[0] if body and body != "0" else []
            ids = sorted({fields["AA"] for fields, _ in rows if fields.get("AA")})
            known_match_ids.update(ids)
            known_singles_match_ids.update(
                fields["AA"] for fields, context in rows
                if fields.get("AA") and not _is_doubles_result(fields, context)
            )
            if body and body != "0":
                feeds.append(body)
            count_gap = (expected_count - len(known_singles_match_ids)
                         if expected_count is not None else None)
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
                "count_gap": count_gap,
                "response_status": response_status,
                "response_error": response_error,
                "pagination_reason": page_reason,
                "terminal_probe_count": terminal_probe_count,
                "terminal_probe_status": terminal_probe_status,
                "terminal_probe_error": terminal_probe_error,
                "reset_checkpoint": reset_checkpoint,
                "fetch_transport": "http",
                "fallback_reason": None,
            }
            if page_callback:
                page_callback(document)
            documents.append(document)

        max_pages = min(120, max(40, ((int(expected_count or 0) + 34) // 35) + 20))

        def request_feed_page(feed_url):
            last_status, last_body, last_error = 0, "", None
            for request_attempt in range(3):
                last_status, last_body, _final, last_error = _profile_http_get(
                    feed_url, source_access, feed=True
                )
                if last_status == 200 or last_status in (403, 429):
                    return last_status, last_body.strip(), last_error
                if last_error and ("FLASHSCORE_SOURCE_LOCK_TIMEOUT" in last_error or
                                   "FLASHSCORE_SOURCE_REQUEST_EXPIRED" in last_error):
                    return 0, "", last_error
                if request_attempt < 2:
                    time.sleep((request_attempt + 1) * 2)
            return last_status, last_body.strip(), last_error

        if not overlap_reached:
            for feed_page in range(max_pages):
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
                            fields["AA"] for fields, context in saved_rows
                            if fields.get("AA") and not _is_doubles_result(fields, context)
                        )
                        feeds.append(body)
                        paged_data_received = True
                    document = {
                        "page_index": feed_page + 1,
                        "source_url": saved.get("url"),
                        "retrieved_at": saved.get("retrieved_at"),
                        "html": body,
                        "result_feeds": saved.get("result_feeds") or [],
                        "match_ids": sorted(ids),
                        "response_status": saved.get("response_status", 200),
                        "pagination_reason": saved.get("pagination_reason"),
                        "terminal_probe_count": saved.get("terminal_probe_count", 0),
                        "terminal_probe_status": saved.get("terminal_probe_status"),
                        "terminal_probe_error": saved.get("terminal_probe_error"),
                        "advertised_event_count": saved.get("advertised_event_count"),
                        "observed_unique_event_count": len(known_match_ids),
                        "observed_unique_singles_event_count": len(known_singles_match_ids),
                        "fetch_transport": "http",
                        "fallback_reason": None,
                        "archived_path": saved.get("archived_path"),
                    }
                    documents.append(document)
                    if body in ("", "0"):
                        terminal_confirmed = int(saved.get("terminal_probe_count") or 0) >= 2
                        count_matches = _advertised_singles_count_matches(expected_count, known_singles_match_ids)
                        pagination_complete = terminal_confirmed and count_matches
                        pagination_reason = (
                            "source_terminal_empty_response_confirmed" if pagination_complete else
                            "source_terminal_empty_response_count_mismatch_confirmed" if terminal_confirmed else
                            "source_terminal_empty_unconfirmed"
                        )
                        break
                    continue

                allowed, _ = DomainFilter.is_allowed(feed_url, allowlist={"flashscore.ninja"})
                if not allowed:
                    pagination_reason = "pagination_response_domain_rejected"
                    break
                response_status, body, response_error = request_feed_page(feed_url)
                if response_status in (403, 429):
                    archive_page(feed_page + 1, feed_url, body, response_status,
                                 f"HTTP_{response_status}_ANTI_BOT_BLOCKED", response_error=response_error)
                    pagination_reason = f"HTTP_{response_status}_ANTI_BOT_BLOCKED"
                    break
                if response_status != 200:
                    pagination_reason = _profile_page_failure_reason(response_status, response_error)
                    archive_page(feed_page + 1, feed_url, response_error or body, 0,
                                 pagination_reason, response_error=response_error)
                    break
                if body in ("", "0"):
                    probe_status, probe_body, probe_error = request_feed_page(feed_url)
                    terminal_probe_count = int(probe_status == 200 and probe_body in ("", "0")) + 1
                    if probe_status == 200 and probe_body not in ("", "0"):
                        body = probe_body
                        paged_data_received = True
                    else:
                        count_matches = _advertised_singles_count_matches(
                            expected_count, known_singles_match_ids
                        )
                        reason = (
                            "empty_response_before_first_page_data"
                            if feed_page == 0 and not paged_data_received and
                            (expected_count is not None or bool(known_match_ids)) else
                            "source_terminal_empty_response_confirmed"
                            if terminal_probe_count == 2 and count_matches else
                            "source_terminal_empty_response_count_mismatch_confirmed"
                            if terminal_probe_count == 2 else
                            "source_terminal_empty_unconfirmed"
                        )
                        archive_page(feed_page + 1, feed_url, body,
                                     response_status if response_status == 200 else 0, reason,
                                     terminal_probe_count=terminal_probe_count,
                                     terminal_probe_status=probe_status,
                                     terminal_probe_error=probe_error,
                                     response_error=response_error)
                        if reason == "source_terminal_empty_response_confirmed":
                            pagination_complete = True
                        pagination_reason = reason
                        break

                rows = _feed_records(body)[0]
                response_ids = {fields.get("AA") for fields, _ in rows if fields.get("AA")}
                if not response_ids:
                    archive_page(feed_page + 1, feed_url, body, response_status,
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
                archive_page(feed_page + 1, feed_url, body, response_status,
                             "page_loaded", reset_checkpoint=reset_checkpoint)
                if not _page_has_pagination_progress(feed_page, new_ids):
                    pagination_reason = "pagination_response_no_new_match_ids"
                    break
                pagination_reason = "page_loaded"
                if cutoff_epoch:
                    response_dates = [int(value) for value in re.findall(r"(?:^|[¬~])AD÷(\d+)", body)]
                    if any(value < cutoff_epoch for value in response_dates):
                        overlap_reached = True
                        pagination_reason = "30_day_overlap_reached"
                        break
            else:
                pagination_reason = "page_limit_reached"
        else:
            pagination_complete = True

        if any(token in profile_html.casefold() for token in (
            "captcha", "verify you are human", "checking your browser",
            "just a moment...", "attention required | cloudflare",
        )):
            return None, "CAPTCHA_CHALLENGE_ABORTED", [], None, False, []
        if documents:
            documents[-1]["pagination_reason"] = pagination_reason
            documents[-1]["advertised_event_count"] = expected_count
            documents[-1]["observed_unique_event_count"] = len(known_match_ids)
            documents[-1]["observed_unique_singles_event_count"] = len(known_singles_match_ids)
            documents[-1]["count_gap"] = (expected_count - len(known_singles_match_ids)
                                           if expected_count is not None else None)
        name_match = re.search(r'"participantName"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', profile_html)
        name = None
        if name_match:
            try:
                name = json.loads('"' + name_match.group(1) + '"')
            except json.JSONDecodeError:
                name = name_match.group(1)
        return (profile_html, "OK", feeds or _feed_payloads(profile_html), name,
                pagination_complete or overlap_reached, documents)

    def _fetch_rendered_profile_with_browser(self, url: str, stop_before_date: datetime | None = None, page_callback=None,
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
                                "() => { const f = window.cjs?.initialFeeds?.['results_s'] || "
                                "window.cjs?.initialFeeds?.['summary-results_s']; "
                                "return Boolean(f && typeof f.data === 'string'); }", timeout=10000
                            )
                        except Exception:
                            return "", [], "RESULTS_SECTION_NOT_LOADED", False, []
                        initial = await page.evaluate("""() => {
                          const feed = window.cjs?.initialFeeds?.['results_s'] ||
                            window.cjs?.initialFeeds?.['summary-results_s'];
                          const count = Number(feed?.allEventsCount);
                          return feed ? {data: typeof feed.data === 'string' ? feed.data : '',
                            allEventsCount: Number.isFinite(count) ? count : null,
                            name: window.teamPageEnvironment?.participantName || null} : null;
                        }""")
                        if not initial or initial.get("data") is None:
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
                                "count_gap": (expected_count - len(known_singles_match_ids)
                                              if expected_count is not None else None),
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
                                                    "referer": FLASHSCORE_TENNIS_HOME_URL,
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
                            page_documents[-1]["count_gap"] = (
                                expected_count - len(known_singles_match_ids)
                                if expected_count is not None else None
                            )
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

            async def load_with_browser_slot() -> tuple[str, list[str], Optional[str], bool, list[dict[str, Any]]]:
                # Profile pagination uses async Playwright rather than the sync
                # browser pool, but must share its cross-container process cap.
                from .browser_pool import flashscore_chromium

                async with flashscore_chromium.browser_slot():
                    return await load()

            page_html, feeds, name, pagination_complete, page_documents = _run_browser_awaitable(
                asyncio.wait_for(load_with_browser_slot(), timeout=300)
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
                "fetch_transport": result.fetch_transport,
                "fallback_reason": result.fallback_reason,
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
        from .player_profiles import lock_flashscore_source_entities, persist_profile_facts

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
            source_match_ids = {
                str(item.source_match_id) for item in profile.matches if item.source_match_id
            } | {
                str(item.get("source_match_id")) for item in profile.rejected_source_matches
                if item.get("source_match_id")
            }
            lock_flashscore_source_entities(session, player_ids, source_match_ids)
            # Read the whole page's current projections once. Locks are held for
            # the transaction, so two profiles sharing a match cannot race
            # between this batch read and their insert/update.
            existing_players = {
                row.source_player_id: row
                for row in session.query(ExtTennisPlayer).filter(
                    ExtTennisPlayer.source == "flashscore",
                    ExtTennisPlayer.source_player_id.in_(list(player_ids)),
                ).all()
            } if player_ids else {}
            for external_id, name in player_ids.items():
                existing = existing_players.get(external_id)
                if existing is None:
                    existing = ExtTennisPlayer(
                        source="flashscore", source_player_id=external_id,
                        full_name=(name or f"Flashscore player {external_id}")[:255],
                    )
                    session.add(existing)
                    existing_players[external_id] = existing
            session.flush()
            existing_matches = {
                row.source_match_id: row
                for row in session.query(ExtTennisMatch).filter(
                    ExtTennisMatch.source == "flashscore",
                    ExtTennisMatch.source_match_id.in_(source_match_ids),
                ).all()
            } if source_match_ids else {}
            latest_observations = {}
            if source_match_ids:
                observations = session.query(ExtTennisMatchObservation).filter(
                    ExtTennisMatchObservation.source == "flashscore",
                    ExtTennisMatchObservation.source_match_id.in_(source_match_ids),
                ).order_by(
                    ExtTennisMatchObservation.source_match_id,
                    ExtTennisMatchObservation.available_at.desc(),
                ).distinct(ExtTennisMatchObservation.source_match_id).all()
                for observation in observations:
                    latest_observations.setdefault(observation.source_match_id, observation)
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
                previous = latest_observations.get(rejected["source_match_id"])
                if previous is None or previous.observation_hash != rejection_hash:
                    previous = ExtTennisMatchObservation(
                        source="flashscore",
                        source_match_id=rejected["source_match_id"],
                        observation_hash=rejection_hash,
                        available_at=result.retrieved_at,
                        source_url=result.url,
                        source_content_hash=result.content_hash,
                        parser_version=PARSER_VERSION,
                        payload=rejection_payload,
                    )
                    session.add(previous)
                    latest_observations[rejected["source_match_id"]] = previous
            observation_inserted: dict[str, bool] = {}
            for item in profile.matches:
                payload = _observation_payload(item)
                observation_hash = _observation_hash(payload)
                latest_observation = latest_observations.get(item.source_match_id)
                is_new_observation = latest_observation is None or latest_observation.observation_hash != observation_hash
                observation_inserted[item.source_match_id] = is_new_observation
                if is_new_observation:
                    latest_observation = ExtTennisMatchObservation(
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
                    )
                    session.add(latest_observation)
                    latest_observations[item.source_match_id] = latest_observation
                projection_available_at = (
                    result.retrieved_at if is_new_observation else
                    (latest_observation.available_at if latest_observation else result.retrieved_at)
                )
                existing = existing_matches.get(item.source_match_id)
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
                existing = ExtTennisMatch(
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
                )
                session.add(existing)
                existing_matches[item.source_match_id] = existing
                matches_added += 1
            facts_added = persist_profile_facts(session, result, owner_participant_id)
            logger.info(
                "Flashscore profile imported player=%s matches_added=%s facts_added=%s complete=%s",
                profile.source_player_id, matches_added, facts_added, profile.pagination_complete,
            )
            session.commit()
        return matches_added

