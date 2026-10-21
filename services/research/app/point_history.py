"""Flashscore point-by-point retrieval, immutable archival, and conservative Fonbet linking."""
from __future__ import annotations
import asyncio, hashlib, json, logging, re, time, threading
from datetime import datetime, timezone, timedelta
import os
from contextlib import nullcontext
from urllib.parse import urlencode, urlparse

from tennis_adapter.point_history import PARSER_VERSION, parse_point_history_html
from .domain_filter import DomainFilter
from .storage import ResearchStorage

log = logging.getLogger("research.point_history")
_LIVE_MATCH_CACHE_LOCK = threading.Lock()
_LIVE_MATCH_CACHE: dict[str, object] = {"expires": 0.0, "links": []}


def _browser_launch_options() -> dict:
    options = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    proxy = os.getenv("RESEARCH_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        options["proxy"] = {"server": proxy}
    return options

def _slug(name: str) -> str:
    value = name.casefold().replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "player"

def _match_page_url(match):
    source_url = getattr(match, "source_url", None)
    if source_url:
        allowed, _ = DomainFilter.is_allowed(source_url)
        if allowed and re.search(r"[?&]mid=" + re.escape(str(match.source_match_id)) + r"(?:&|$)", source_url):
            return source_url
    if not match.home_player_id or not match.away_player_id:
        return None
    home = _slug(match.home_name) + "-" + match.home_player_id
    away = _slug(match.away_name) + "-" + match.away_player_id
    parsed = urlparse(source_url or "")
    host = parsed.netloc if DomainFilter.is_allowed(source_url or "")[0] else "www.flashscore.com"
    return f"https://{host}/match/tennis/{home}/{away}/summary/?{urlencode({'mid': match.source_match_id})}"


def _confirmed_link_page(link, player_a_id: str, player_b_id: str):
    """Return a canonical PBP page URL from a confirmed live-link observation.

    Live discovery can confirm a match before its profile-history import has
    created an ExtTennisMatch row. In that case the durable link itself is the
    authoritative source for the URL. Require both confirmed profile IDs in
    the match path and an allowed Flashscore host before using it.
    """
    evidence = getattr(link, "evidence_json", None) or {}
    source_url = str(evidence.get("source_url") or "")
    if not source_url or not player_a_id or not player_b_id:
        return None
    allowed, _ = DomainFilter.is_allowed(source_url)
    if not allowed:
        return None
    parsed = urlparse(source_url)
    path = parsed.path
    pos_a, pos_b = path.find(player_a_id), path.find(player_b_id)
    if pos_a < 0 or pos_b < 0 or pos_a == pos_b:
        return None
    # Source URLs observed from the live list can contain a malformed query
    # suffix after a redirect. Keep the verified match path and reconstruct a
    # stable summary URL from the durable match ID instead of replaying it.
    base_path = re.sub(r"/(?:summary|point-by-point)(?:/.*)?$", "", path).rstrip("/")
    if "/match/tennis/" not in base_path:
        return None
    source_match_id = str(getattr(link, "source_match_id", ""))
    if not source_match_id:
        return None
    canonical = f"{parsed.scheme}://{parsed.netloc}{base_path}/summary/?{urlencode({'mid': source_match_id})}"
    canonical_allowed, _ = DomainFilter.is_allowed(canonical)
    if not canonical_allowed:
        return None
    return canonical, pos_a < pos_b


def _point_by_point_set_url(page_url: str, source_match_id: str, set_no: int) -> str:
    """Build the currently observed Flashscore PBP route as a fallback."""
    base = page_url.split("?", 1)[0]
    base = re.sub(r"/(?:summary/)?point-by-point(?:/set-\d+)?/?$", "", base)
    base = re.sub(r"/summary/?$", "", base).rstrip("/")
    return f"{base}/summary/point-by-point/set-{int(set_no)}/?{urlencode({'mid': source_match_id})}"


def _point_by_point_index_url(page_url: str, source_match_id: str) -> str:
    base = page_url.split("?", 1)[0]
    base = re.sub(r"/(?:summary/)?point-by-point(?:/set-\d+)?/?$", "", base)
    base = re.sub(r"/summary/?$", "", base).rstrip("/")
    return f"{base}/summary/point-by-point/?{urlencode({'mid': source_match_id})}"


def _preferred_point_history_url(summary_url: str) -> str:
    """Open the PBP index directly when a trusted stable match id is present.

    The worker already has the confirmed source match id. Loading the summary
    page first adds a full navigation that is only needed for legacy URLs that
    do not carry that id. The caller still falls back to summary discovery if
    the direct PBP route is unavailable or does not expose set tabs.
    """
    match = re.search(r"[?&]mid=([A-Za-z0-9]+)(?:&|$)", summary_url)
    if not match:
        return summary_url
    candidate = _point_by_point_index_url(summary_url, match.group(1))
    allowed, _ = DomainFilter.is_allowed(candidate)
    return candidate if allowed else summary_url


def _open_point_history_entry(page, summary_url: str, source_access=None, timeout_ms: int = 25000):
    """Try the known PBP route, then recover through the match summary page.

    Direct-index navigation saves one round trip when it works. Some live
    matches do not expose that route consistently, and Chromium may time out
    instead of returning an HTTP 404. In either case, retry the source URL
    already confirmed for this match before reporting a failed PBP attempt.
    """
    access = source_access or nullcontext
    initial_url = _preferred_point_history_url(summary_url)

    def navigate(url):
        with access():
            return page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    direct_error = None
    try:
        response = navigate(initial_url)
    except Exception as exc:
        if initial_url == summary_url:
            return None, f"MATCH_PAGE_NAVIGATION_{type(exc).__name__}"
        direct_error = type(exc).__name__
        response = None
    if (initial_url != summary_url and
            (direct_error is not None or (response is not None and response.status == 404))):
        try:
            response = navigate(summary_url)
        except Exception as exc:
            return None, f"DIRECT_ROUTE_{direct_error or 'HTTP_404'}_SUMMARY_NAVIGATION_{type(exc).__name__}"
    return response, None


def _point_by_point_set_tabs(links, source_match_id: str) -> dict[int, str]:
    """Keep only source-provided set links for this exact match."""
    tabs = {}
    for href in links:
        path = urlparse(href).path
        set_match = re.search(r"/set-(\d+)(?:/|$)", path)
        if not set_match:
            continue
        allowed, _ = DomainFilter.is_allowed(href)
        tab_mid = re.findall(r"[?&]mid=([A-Za-z0-9]+)", href)
        if allowed and tab_mid == [source_match_id]:
            tabs.setdefault(int(set_match.group(1)), href)
    return tabs


def _point_history_tab_missing_document(page, response) -> dict:
    """Archive the source summary when no PBP/set tab was published."""
    return {
        "set": None,
        "url": page.url,
        "html": page.content(),
        "parsed": None,
        "retrieved_at": datetime.now(timezone.utc),
        "http_status": response.status if response else None,
        "fetch_status": "point_history_tab_missing",
        "set_tab_confirmed": False,
        "collection_status": "PBP_TAB_NOT_PUBLISHED",
    }


def _is_live_observed_document(
    snapshot_status: str | None,
    snapshot_at: datetime | None,
    retrieved_at: datetime,
    event_finished_at: datetime | None = None,
) -> bool:
    """A PBP fetch is live-observed only beside a fresh persisted live snapshot.

    The document's availability remains its retrieval time. This flag never
    backdates the cumulative point sequence to the time its points were played.
    """
    if snapshot_status != "live" or snapshot_at is None:
        return False
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    observed_at, available_at = aware(snapshot_at), aware(retrieved_at)
    age = (available_at - observed_at).total_seconds()
    if age < 0 or age > 90:
        return False
    if event_finished_at is not None and aware(event_finished_at) <= available_at:
        return False
    return True


def _document_was_observed_live(event_id, retrieved_at: datetime) -> bool:
    """Check the latest Fonbet state known by this document's retrieval time."""
    from db.connection import SessionLocal
    from db.models import Event, EventStateSnapshot
    with SessionLocal() as session:
        snapshot = session.query(EventStateSnapshot).filter(
            EventStateSnapshot.event_id == event_id,
            EventStateSnapshot.observed_at <= retrieved_at,
        ).order_by(EventStateSnapshot.observed_at.desc()).first()
        event = session.get(Event, event_id)
        return _is_live_observed_document(
            snapshot.status if snapshot else None,
            snapshot.observed_at if snapshot else None,
            retrieved_at,
            event.finished_at if event else None,
        )


def _external_match_state(match, observation_payload):
    """Build the source-side participants from the immutable imported observation."""
    from types import SimpleNamespace
    match_stats = getattr(match, "stats", None) or {}

    return SimpleNamespace(
        source_match_id=match.source_match_id,
        home_name=str(observation_payload.get("home_name") or ""),
        away_name=str(observation_payload.get("away_name") or ""),
        home_player_id=observation_payload.get("home_player_id"),
        away_player_id=observation_payload.get("away_player_id"),
        tournament=match.tournament,
        source_url=observation_payload.get("source_url") or match_stats.get("source_url"),
    )


def _external_home_is_fonbet_a(fonbet_names, source_names, fonbet_player_ids=None, source_player_ids=None):
    """Resolve source side order only when both participant identities agree."""
    if fonbet_player_ids and source_player_ids and all(fonbet_player_ids) and all(source_player_ids):
        if source_player_ids[0] == fonbet_player_ids[0] and source_player_ids[1] == fonbet_player_ids[1]:
            return True
        if source_player_ids[0] == fonbet_player_ids[1] and source_player_ids[1] == fonbet_player_ids[0]:
            return False
        return None
    from .tennis_flashscore import _player_name_matches
    a_home = _player_name_matches(fonbet_names[0], source_names[0])
    b_away = _player_name_matches(fonbet_names[1], source_names[1])
    b_home = _player_name_matches(fonbet_names[1], source_names[0])
    a_away = _player_name_matches(fonbet_names[0], source_names[1])
    direct, reverse = a_home and b_away, b_home and a_away
    if direct == reverse:
        return None
    return bool(direct)


def _match_url_has_player_ids(match_url: str, player_a_id: str, player_b_id: str) -> bool:
    """Require confirmed player IDs as separate Flashscore match slugs.

    Flashscore appends each player profile ID to a participant slug. A broad
    substring search can accidentally match an ID in another slug or query.
    """
    if not match_url or not player_a_id or not player_b_id:
        return False
    segments = [segment for segment in urlparse(match_url).path.split("/") if segment]
    tennis_index = next((i for i, segment in enumerate(segments) if segment == "tennis"), None)
    if tennis_index is None:
        return False
    slugs = segments[tennis_index + 1:]

    def has_id(slug: str, player_id: str) -> bool:
        return slug == player_id or slug.endswith(f"-{player_id}")

    return any(
        i != j and has_id(left, player_a_id) and has_id(right, player_b_id)
        for i, left in enumerate(slugs)
        for j, right in enumerate(slugs)
    )

def _discover_live_match(player_a_id: str, player_b_id: str, source_access=None, excluded_match_ids=None):
    """Find an exact two-player live match, reusing one source page for 30s."""
    if not player_a_id or not player_b_id: return None
    excluded_match_ids = {str(value) for value in (excluded_match_ids or set())}
    with _LIVE_MATCH_CACHE_LOCK:
        links = _LIVE_MATCH_CACHE.get("links") or []
        if time.monotonic() >= float(_LIVE_MATCH_CACHE.get("expires") or 0):
            from .browser_pool import flashscore_chromium
            url = "https://www.flashscore.com/tennis/"
            allowed, _ = DomainFilter.is_allowed(url)
            if not allowed: return None
            with flashscore_chromium.context(
                _browser_launch_options(), locale="en-US", timezone_id="UTC"
            ) as (_browser, context):
                page = context.new_page()
                from .tennis_flashscore import _install_flashscore_request_filter_sync
                _install_flashscore_request_filter_sync(page)
                with (source_access or nullcontext)():
                    response = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if response and response.status in (403, 429): return None
                page.wait_for_timeout(800)
                links = page.locator('a[href*="/match/tennis/"]').evaluate_all(
                    "els => els.map(a => ({href:a.href, text:(a.innerText||a.textContent||'').trim()}))")
                _LIVE_MATCH_CACHE.update(links=links, expires=time.monotonic() + 30)
        # The same live fixture can be exposed through more than one link (for
        # example, a pinned card and its normal event row). Treat repeated URLs
        # for one stable match ID as one candidate, not as an ambiguity.
        candidates_by_id = {}
        for item in links:
            href = item.get("href", "")
            if (not _match_url_has_player_ids(href, player_a_id, player_b_id)
                    or "/doubles/" in urlparse(href).path.casefold()):
                continue
            found = re.search(r"[?&]mid=([A-Za-z0-9]+)", href)
            if found and found.group(1) not in excluded_match_ids:
                candidates_by_id.setdefault(found.group(1), item)
        candidates = list(candidates_by_id.values())
        if len(candidates) > 1:
            ids = []
            for item in candidates:
                found = re.search(r"[?&]mid=([A-Za-z0-9]+)", item["href"])
                if found:
                    ids.append({"source_match_id": found.group(1), "url": item["href"],
                                "label": item.get("text") or ""})
            return {"status": "ambiguous", "candidates": ids}
        if not candidates: return None
        href = candidates[0]["href"]
        ok, _ = DomainFilter.is_allowed(href)
        if not ok: return None
        mid = re.search(r"[?&]mid=([A-Za-z0-9]+)", href)
        if not mid: return None
        return {"status": "matched", "source_match_id":mid.group(1), "url":href.split("/summary",1)[0]+f"/summary/?{urlencode({'mid':mid.group(1)})}"}


def _normalize_live_match_discovery(discovered: dict | None) -> dict:
    """Validate the result shape before the PBP caller consumes match fields."""
    if not isinstance(discovered, dict):
        return {"status": "unmatched", "candidates": []}
    status = str(discovered.get("status") or "unmatched")
    if status == "matched":
        source_match_id = str(discovered.get("source_match_id") or "").strip()
        url = str(discovered.get("url") or "").strip()
        allowed, _ = DomainFilter.is_allowed(url) if url else (False, "missing_url")
        mid = re.search(r"[?&]mid=([A-Za-z0-9]+)", url) if allowed else None
        if source_match_id and allowed and mid and mid.group(1) == source_match_id:
            return {"status": "matched", "source_match_id": source_match_id, "url": url}
        return {"status": "unmatched", "candidates": []}
    if status == "ambiguous":
        candidates = []
        for item in discovered.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            source_match_id = str(item.get("source_match_id") or "").strip()
            url = str(item.get("url") or "").strip()
            allowed, _ = DomainFilter.is_allowed(url) if url else (False, "missing_url")
            mid = re.search(r"[?&]mid=([A-Za-z0-9]+)", url) if allowed else None
            if source_match_id and allowed and mid and mid.group(1) == source_match_id:
                candidates.append({
                    "source_match_id": source_match_id,
                    "url": url,
                    "label": str(item.get("label") or "").strip(),
                })
        return {"status": "ambiguous" if candidates else "unmatched", "candidates": candidates}
    return {"status": status, "candidates": []}


def _persist_live_match_confirmations(event_uuid, attempt_token, event_names, candidates) -> int:
    """Park a PBP attempt and create stable admin proposals for live candidates."""
    from db.connection import SessionLocal
    from db.models import Event, TennisPointMatchLink, TennisPointRefreshJob
    from db.tennis_confirmations import upsert_manual_confirmation

    now = datetime.now(timezone.utc)
    persisted = 0
    with SessionLocal() as session:
        if attempt_token and session.query(TennisPointRefreshJob).filter_by(
            event_id=event_uuid, attempt_token=attempt_token, status="running"
        ).filter(TennisPointRefreshJob.lease_until > now).with_for_update().first() is None:
            return -1
        event = session.get(Event, event_uuid)
        if event is None:
            return -1
        for candidate in candidates:
            source_match_id = candidate["source_match_id"]
            latest = session.query(TennisPointMatchLink).filter_by(
                source_match_id=source_match_id, fonbet_event_id=event_uuid
            ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).first()
            if latest and latest.status in {"matched", "manual", "rejected"}:
                continue
            evidence = {
                "fonbet_event_id": str(event_uuid),
                "fonbet_participants": list(event_names),
                "flashscore_match_label": candidate.get("label") or None,
                "source_url": candidate["url"],
                "match_lookup": "live_board_exact_player_ids_multiple_candidates",
            }
            if latest is None or latest.status != "ambiguous":
                session.add(TennisPointMatchLink(
                    source_match_id=source_match_id,
                    fonbet_event_id=event_uuid,
                    status="ambiguous",
                    confidence=0.5,
                    evidence_json=evidence,
                    available_at=now,
                ))
            request = upsert_manual_confirmation(
                session,
                kind="event_match",
                subject_id=str(event_uuid),
                candidate_id=source_match_id,
                evidence=evidence,
            )
            if request is not None:
                persisted += 1
        session.commit()
    return persisted

def fetch_point_documents(summary_url: str, fetcher=None, delay_seconds: float = 1.0, source_access=None):
    allowed, reason = DomainFilter.is_allowed(summary_url)
    if not allowed:
        return [], f"URL_REJECTED:{reason}"
    access = source_access or nullcontext
    output = []
    from .browser_pool import flashscore_chromium
    with flashscore_chromium.context(
        _browser_launch_options(), locale="en-US", timezone_id="UTC"
    ) as (_browser, context):
        page = context.new_page()
        from .tennis_flashscore import _install_flashscore_request_filter_sync
        _install_flashscore_request_filter_sync(page)
        initial_url = _preferred_point_history_url(summary_url)
        response, navigation_error = _open_point_history_entry(page, summary_url, source_access=source_access)
        if navigation_error:
            flashscore_chromium.invalidate_current()
            return [], navigation_error
        if response and response.status in (403, 429): return [], f"HTTP_{response.status}_BLOCKED"
        if response and response.status >= 400: return [], f"HTTP_{response.status}_MATCH_PAGE_UNAVAILABLE"
        allowed, reason = DomainFilter.is_allowed(page.url)
        if not allowed: return [], f"REDIRECT_REJECTED:{reason}"
        page.wait_for_function(
            "() => document.body && (document.body.innerText.length > 50 || document.querySelector('[data-testid=\"match-header\"]'))",
            timeout=5000,
        )
        body = page.locator("body").inner_text().casefold()
        if any(x in body for x in ("captcha", "verify you are human", "checking your browser")):
            return [], "BLOCKED"
        mid_match = re.search(r"[?&]mid=([A-Za-z0-9]+)", page.url)
        if mid_match: mid = mid_match.group(1)
        else:
            # Match pages encode the stable source id in a data attribute or link.
            mid = page.locator('[data-testid="match-header"] [data-mid]').get_attribute("data-mid")
        if not mid: return [], "MATCH_ID_MISSING"
        route_links = page.locator('a[href*="point-by-point"]').evaluate_all(
            "els => els.map(a => a.href)"
        )
        index_urls = []
        for href in route_links:
            path = urlparse(href).path
            if "/point-by-point/" not in path or re.search(r"/set-\d+/?$", path):
                continue
            allowed, _ = DomainFilter.is_allowed(href)
            if allowed and re.findall(r"[?&]mid=([A-Za-z0-9]+)", href) == [mid]:
                index_urls.append(href)
        point_index_url = index_urls[0] if index_urls else _point_by_point_index_url(page.url, mid)
        if page.url != point_index_url:
            try:
                with access():
                    response = page.goto(point_index_url, wait_until="domcontentloaded", timeout=25000)
            except Exception as exc:
                flashscore_chromium.invalidate_current()
                return [], f"POINT_BY_POINT_NAVIGATION_{type(exc).__name__}"
            if response and response.status in (403, 429): return [], f"HTTP_{response.status}_BLOCKED"
            if response and response.status >= 400: return [], f"POINT_BY_POINT_HTTP_{response.status}"
            allowed, reason = DomainFilter.is_allowed(page.url)
            if not allowed: return [], f"REDIRECT_REJECTED:{reason}"
        try:
            page.wait_for_function(
                "() => [...document.querySelectorAll('a[href*=\\\"point-by-point\\\"]')].some(a => /\\/set-\\d+\\//.test(a.href))",
                timeout=5000,
            )
        except Exception:
            pass
        set_tabs = page.locator('a[href*="point-by-point"]').evaluate_all(
            "els => els.map(a => a.href)"
        )
        set_urls = _point_by_point_set_tabs(set_tabs, mid)
        if not set_urls and initial_url != summary_url and page.url != summary_url:
            # The direct route can be accepted while returning an incomplete
            # shell. Recover source-provided tabs from the summary only on this
            # fallback path; normal matches skip an extra full navigation.
            try:
                with access():
                    response = page.goto(summary_url, wait_until="domcontentloaded", timeout=25000)
                if response and response.status in (403, 429):
                    return [], f"HTTP_{response.status}_BLOCKED"
                if response and response.status < 400:
                    allowed, reason = DomainFilter.is_allowed(page.url)
                    if not allowed:
                        return [], f"REDIRECT_REJECTED:{reason}"
                    summary_links = page.locator('a[href*="point-by-point"]').evaluate_all(
                        "els => els.map(a => a.href)"
                    )
                    summary_tabs = _point_by_point_set_tabs(summary_links, mid)
                    if summary_tabs:
                        index_url = next((href for href in summary_links
                                          if "/point-by-point/" in urlparse(href).path
                                          and not re.search(r"/set-\d+/?$", urlparse(href).path)
                                          and re.findall(r"[?&]mid=([A-Za-z0-9]+)", href) == [mid]), None)
                        if index_url:
                            with access():
                                response = page.goto(index_url, wait_until="domcontentloaded", timeout=25000)
                            if response and response.status in (403, 429):
                                return [], f"HTTP_{response.status}_BLOCKED"
                            if response and response.status < 400:
                                set_urls = summary_tabs
            except Exception as exc:
                log.debug("PBP direct-route recovery failed mid=%s error=%s", mid, type(exc).__name__)
        expected_sets = sorted(set_urls)
        tabs_confirmed = bool(expected_sets)
        if not expected_sets:
            # Do not synthesize a set-1 URL when the source page has no PBP
            # tab. This commonly happens for matches where Flashscore only
            # publishes final results. Archive the actual summary response
            # for audit, mark it distinctly, and let the durable job back off.
            output.append(_point_history_tab_missing_document(page, response))
            return output, "point_history_tab_missing"
        incomplete_reason = None
        for set_no in expected_sets:
            url = set_urls.get(set_no) or _point_by_point_set_url(page.url, mid, set_no)
            # The global source regulator already enforces the minimum
            # interval whenever a managed worker supplies source_access.
            # Sleeping here as well serialized set tabs twice and kept the
            # browser lease occupied without adding any source protection.
            if set_no > 1 and delay_seconds and source_access is None:
                time.sleep(delay_seconds)
            try:
                with access():
                    response = page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as exc:
                flashscore_chromium.invalidate_current()
                incomplete_reason = f"SET_{set_no}_NAVIGATION_{type(exc).__name__}"
                continue
            if response and response.status in (403, 429): return output, f"HTTP_{response.status}_BLOCKED"
            if response and response.status >= 400:
                html = page.content()
                output.append({"set": set_no, "url": url, "html": html, "parsed": None,
                    "retrieved_at": datetime.now(timezone.utc), "http_status": response.status,
                    "fetch_status": f"HTTP_{response.status}"})
                incomplete_reason = f"EXPECTED_SET_{set_no}_HTTP_{response.status}"
                continue
            try: page.wait_for_selector(".matchHistoryRowWrapper", timeout=4000)
            except Exception:
                error_html = page.content()
                output.append({"set": set_no, "url": url, "html": error_html,
                    "parsed": None,
                    "retrieved_at": datetime.now(timezone.utc),
                    "http_status": response.status if response else None,
                    "fetch_status": "history_selector_missing"})
                incomplete_reason = f"EXPECTED_SET_{set_no}_HISTORY_NOT_AVAILABLE"
                continue
            html = page.content()
            output.append({"set": set_no, "url": url, "html": html,
                           "parsed": None,
                           "retrieved_at": datetime.now(timezone.utc)})
            # Keep browser retrieval independent from parsing so the caller
            # can archive the exact document before interpreting it.
            if not html:
                incomplete_reason = f"EXPECTED_SET_{set_no}_HISTORY_EMPTY"
        for item in output:
            item["set_tab_confirmed"] = tabs_confirmed
            item["collection_status"] = "complete" if incomplete_reason is None else incomplete_reason
        return output, "complete" if output and incomplete_reason is None else (incomplete_reason or "unavailable")

def collect_event_point_history(event_id: str, attempt_token: str | None = None, source_access=None) -> dict:
    from db.connection import SessionLocal
    from db.models import Event, ExtTennisMatch, ExtTennisMatchObservation, TennisPointDocument, TennisPointHistoryVersion, TennisPointMatchLink, TennisPointRefreshJob
    import uuid
    now = datetime.now(timezone.utc)
    event_uuid = uuid.UUID(str(event_id))
    with SessionLocal() as session:
        if attempt_token and session.query(TennisPointRefreshJob).filter_by(
            event_id=event_uuid, attempt_token=attempt_token, status="running"
        ).filter(TennisPointRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first() is None:
            return {"status": "lease_lost"}
        event = session.get(Event, event_uuid)
        if event is None or event.sport_code != "tennis": return {"status": "not_found"}
        event_names = (event.participant_a_name, event.participant_b_name)
        is_live = bool(event.is_live)
        event_link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.fonbet_event_id == event_uuid,
            TennisPointMatchLink.available_at <= now,
        ).all()
        candidate_source_ids = {link.source_match_id for link in event_link_rows}
        link_rows = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.source_match_id.in_(candidate_source_ids) if candidate_source_ids else False,
            TennisPointMatchLink.fonbet_event_id.is_not(None),
            TennisPointMatchLink.available_at <= now,
        ).order_by(TennisPointMatchLink.available_at, TennisPointMatchLink.id).all()
        latest_links = {}
        for link in link_rows:
            latest_links[link.source_match_id] = link
        confirmed_links = [link for link in latest_links.values()
                           if link.fonbet_event_id == event_uuid and link.status in ("matched", "manual")]
        linked_source_ids = list(dict.fromkeys(link.source_match_id for link in confirmed_links))
        rejected_source_ids = {link.source_match_id for link in latest_links.values()
                               if link.fonbet_event_id == event_uuid and link.status == "rejected"}
        match_query = session.query(ExtTennisMatch).filter(ExtTennisMatch.source == "flashscore")
        if linked_source_ids:
            matches = match_query.filter(ExtTennisMatch.source_match_id.in_(linked_source_ids)).all()
        else:
            matches = match_query.filter(ExtTennisMatch.played_at.between(
                event.scheduled_start_at - timedelta(days=2), event.scheduled_start_at + timedelta(days=2)
            )).all()
        observations_by_match = {}
        if matches:
            observations = session.query(ExtTennisMatchObservation).filter(
                ExtTennisMatchObservation.source == "flashscore",
                ExtTennisMatchObservation.source_match_id.in_([row.source_match_id for row in matches]),
                ExtTennisMatchObservation.available_at <= now,
            ).order_by(ExtTennisMatchObservation.available_at, ExtTennisMatchObservation.id).all()
            for observation in observations:
                observations_by_match[observation.source_match_id] = observation.payload or {}
        match_states = {
            row.source_match_id: _external_match_state(row, observations_by_match.get(row.source_match_id, {}))
            for row in matches
        }
        from db.models import TennisPlayerProfile, League
        league = session.get(League, event.league_id) if event.league_id else None
        profile_a = session.get(TennisPlayerProfile, event.participant_a_id) if event.participant_a_id else None
        profile_b = session.get(TennisPlayerProfile, event.participant_b_id) if event.participant_b_id else None
        flashscore_a = str((profile_a.source_identities or {}).get("flashscore") or "") if profile_a else ""
        flashscore_b = str((profile_b.source_identities or {}).get("flashscore") or "") if profile_b else ""
        league_name = league.name if league else ""
        source_side_orientation = {}
        def player_match(row):
            if row.home_name.strip().endswith("/") or row.away_name.strip().endswith("/"):
                return False
            if "/" in row.home_name or "/" in row.away_name:
                return False
            # Name-only matching is accepted only when both participants agree;
            # side orientation is retained for label alignment.
            orientation = _external_home_is_fonbet_a(
                event_names, (row.home_name, row.away_name),
                (flashscore_a, flashscore_b) if flashscore_a and flashscore_b else None,
                (row.home_player_id, row.away_player_id) if row.home_player_id and row.away_player_id else None,
            )
            participants_ok = orientation is not None
            if participants_ok:
                source_side_orientation[row.source_match_id] = orientation
            if not participants_ok: return False
            if league_name:
                if not row.tournament:
                    return False
                wanted = {token for token in re.findall(r"[a-z0-9]+", league_name.casefold()) if len(token) > 3}
                actual = set(re.findall(r"[a-z0-9]+", row.tournament.casefold()))
                if not wanted or not wanted.intersection(actual): return False
            return True
        candidates = [match_states[m.source_match_id] for m in matches
                      if m.source_match_id in match_states and m.source_match_id not in rejected_source_ids
                      and player_match(match_states[m.source_match_id])]
        if len(candidates) > 1:
            from db.tennis_confirmations import upsert_manual_confirmation
            for item in candidates:
                evidence = {"candidate_event_id": str(event_uuid), "fonbet_participants": list(event_names),
                    "flashscore_players": [item.home_name, item.away_name], "tournament": item.tournament,
                    "source_url": item.source_url}
                session.add(TennisPointMatchLink(source_match_id=item.source_match_id, fonbet_event_id=event_uuid,
                    status="ambiguous", confidence=0.5, evidence_json=evidence, available_at=now))
                upsert_manual_confirmation(session, kind="event_match", subject_id=str(event_uuid),
                    candidate_id=item.source_match_id, evidence=evidence)
            session.commit()
            return {"status": "ambiguous", "candidate_count": len(candidates)}
        match = candidates[0] if candidates else None
        if match:
            source_match_id, url = match.source_match_id, _match_page_url(match)
            link_method = "prior_manual_or_confirmed_link" if source_match_id in linked_source_ids else "external_player_ids_tournament_and_start_window"
            external_home_is_a = source_side_orientation.get(source_match_id)
        else:
            confirmed_link_pages = []
            for link in confirmed_links:
                resolved = _confirmed_link_page(link, flashscore_a, flashscore_b)
                if resolved:
                    confirmed_link_pages.append((link, resolved[0], resolved[1]))
            if len(confirmed_link_pages) == 1:
                link, url, external_home_is_a = confirmed_link_pages[0]
                source_match_id = str(link.source_match_id)
                source_side_orientation[source_match_id] = external_home_is_a
                link_method = "confirmed_link_source_url_without_history_import"
            else:
                source_match_id, url = "", None
                link_method, external_home_is_a = "live_match_anchor_external_player_ids", None
    if not url and flashscore_a and flashscore_b:
        discovered = _discover_live_match(flashscore_a, flashscore_b, source_access=source_access,
                                          excluded_match_ids=rejected_source_ids)
        normalized = _normalize_live_match_discovery(discovered)
        if normalized["status"] == "matched":
            source_match_id, url = normalized["source_match_id"], normalized["url"]
        elif normalized["status"] == "ambiguous":
            saved = _persist_live_match_confirmations(
                event_uuid, attempt_token, event_names, normalized["candidates"]
            )
            if saved < 0:
                return {"status": "lease_lost"}
            if saved:
                return {"status": "ambiguous", "candidate_count": len(normalized["candidates"])}
    if not url:
        return {"status": "unmatched", "reason": "FLASHSCORE_MATCH_LINK_NOT_FOUND" if flashscore_a and flashscore_b else "PLAYER_SOURCE_IDS_MISSING"}
    docs, status = fetch_point_documents(url, source_access=source_access)
    if not docs: return {"status": status, "source_match_id": source_match_id}
    storage = ResearchStorage(); saved = 0; confirmed = 0; gaps = []
    # Flashscore does not timestamp individual points. Keep availability at
    # document receipt; only classify the document as observed when a fresh,
    # already-persisted Fonbet live snapshot proves the match was being watched.
    all_retrospective = True
    for doc in docs:
        retrospective = not _document_was_observed_live(event_uuid, doc["retrieved_at"])
        all_retrospective = all_retrospective and retrospective
        raw_hash = hashlib.sha256(doc["html"].encode()).hexdigest()
        archived = storage.archive_document(source_match_id, raw_hash, {"source": "flashscore", "url": doc["url"],
            "source_match_id": source_match_id, "retrieved_at": doc["retrieved_at"].isoformat(),
            "http_status": doc.get("http_status"), "fetch_status": doc.get("fetch_status"),
            "parser_version": PARSER_VERSION, "retrospective": retrospective, "html": doc["html"]})
        if not archived: return {"status": "archive_failed", "source_match_id": source_match_id}
        parsed_at = datetime.now(timezone.utc)
        parsed = parse_point_history_html(doc["html"])
        quality = {
            **(parsed.get("quality") or {}),
            "set_number": doc.get("set"),
            "set_tab_confirmed": doc.get("set_tab_confirmed"),
            "collection_status": doc.get("collection_status"),
            "fetch_status": doc.get("fetch_status"),
        }
        if not parsed.get("events"):
            quality.setdefault("gaps", []).append("HISTORY_EMPTY_OR_UNRECOGNIZED")
        doc["parsed"] = {**parsed, "quality": quality}
        with SessionLocal() as session:
            if attempt_token and session.query(TennisPointRefreshJob).filter_by(
                event_id=event_uuid, attempt_token=attempt_token, status="running"
            ).filter(TennisPointRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first() is None:
                return {"status": "lease_lost", "source_match_id": source_match_id}
            latest = session.query(TennisPointDocument).filter_by(source="flashscore", source_match_id=source_match_id,
                source_url=doc["url"]).order_by(TennisPointDocument.retrieved_at.desc(), TennisPointDocument.id.desc()).first()
            if latest and latest.content_hash == raw_hash and latest.parser_version == PARSER_VERSION:
                continue
            events = [{**item, "retrospective": retrospective} for item in parsed["events"]]
            document = TennisPointDocument(source_match_id=source_match_id, source_url=doc["url"], content_hash=raw_hash,
                archived_path=archived, parser_version=PARSER_VERSION, retrieved_at=doc["retrieved_at"],
                parsed_at=parsed_at, available_at=datetime.now(timezone.utc), availability_status="known",
                retrospective=retrospective, parse_status="complete" if quality["complete"] else "partial",
                quality_json=quality)
            session.add(document); session.flush()
            session.add(TennisPointHistoryVersion(document_id=document.id, source_match_id=source_match_id,
                fonbet_event_id=event_uuid, parser_version=PARSER_VERSION, retrieved_at=doc["retrieved_at"],
                parsed_at=parsed_at, available_at=datetime.now(timezone.utc), availability_status="known",
                events_json=events, quality_json=quality, retrospective=retrospective))
            session.add(TennisPointMatchLink(source_match_id=source_match_id, fonbet_event_id=event_uuid,
                status="matched", confidence=1.0, evidence_json={"method": link_method,
                    "fonbet_participants": list(event_names), "external_home_is_fonbet_a": external_home_is_a},
                available_at=datetime.now(timezone.utc)))
            session.commit(); saved += 1
        confirmed += quality["confirmed_points"]
        gaps.extend(quality["gaps"])
    complete = status == "complete" and all((doc.get("parsed") or {}).get("quality", {}).get("complete") for doc in docs)
    return {"status": "available" if complete else "partial", "reason": None if complete else status,
            "source_match_id": source_match_id, "documents_saved": saved,
            "confirmed_points": confirmed, "retrospective": all_retrospective, "gaps": gaps}
