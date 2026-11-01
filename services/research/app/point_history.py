"""Flashscore point-by-point retrieval, immutable archival, and conservative Fonbet linking."""
from __future__ import annotations
import asyncio, hashlib, json, logging, re, time, threading
from datetime import datetime, timezone, timedelta
import os
from contextlib import nullcontext
from urllib.parse import parse_qs, urlencode, urlparse

from tennis_adapter.point_history import PARSER_VERSION, parse_point_history_html
from .domain_filter import DomainFilter
from .storage import ResearchStorage

log = logging.getLogger("research.point_history")
_LIVE_MATCH_CACHE_LOCK = threading.Lock()
_LIVE_MATCH_CACHE: dict[str, object] = {"expires": 0.0, "links": []}


def _point_history_feed_url(source_match_id: str) -> str:
    """Build Flashscore's compact point-by-point feed URL for tennis project 2."""
    source_match_id = str(source_match_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{5,32}", source_match_id):
        raise ValueError("Invalid Flashscore match id")
    return f"https://global.flashscore.ninja/2/x/feed/df_mh_2_{source_match_id}"


def _feed_fields(segment: str) -> dict[str, str]:
    fields = {}
    for cell in segment.split("¬"):
        code, separator, value = cell.partition("÷")
        if separator and code:
            fields[code] = value
    return fields


def _feed_set_number(title: str) -> int | None:
    match = re.search(r"(?:set|сет)\s*(\d+)|(\d+)\s*(?:set|сет)", title, re.I)
    return int(match.group(1) or match.group(2)) if match else None


def _parse_point_history_feed(payload: str) -> dict:
    """Parse Flashscore's match-history text feed into the shared PBP event shape."""
    from tennis_adapter.point_history import infer_point_winner

    events, gaps, sets_seen = [], [], set()
    current_set = None
    signature = None
    previous_game_score: dict[int, list[int]] = {}
    tiebreak_score: dict[int, list[int]] = {}
    tiebreak_results: dict[int, list[int]] = {}
    completed_tiebreaks: set[int] = set()
    in_tiebreak = False
    for segment in (part for part in payload.split("~") if part):
        fields = _feed_fields(segment)
        if "A1" in fields:
            signature = fields["A1"]
        if "HA" in fields:
            current_set = _feed_set_number(fields["HA"])
            in_tiebreak = False
            if current_set is not None:
                sets_seen.add(current_set)
        if "HB" in fields and re.search(r"tie[ -]?break", fields["HB"], re.I):
            current_set = _feed_set_number(fields["HB"]) or current_set
            in_tiebreak = True
            if current_set is not None:
                sets_seen.add(current_set)
        if current_set is None or "HC" not in fields or "HE" not in fields:
            continue
        try:
            game_score = [int(fields["HC"]), int(fields["HE"])]
        except (TypeError, ValueError):
            gaps.append({"reason": "invalid_game_score", "fields": {k: fields.get(k) for k in ("HC", "HE")}})
            continue
        before_games = previous_game_score.get(current_set, [0, 0])
        changed_games = [i for i in (0, 1) if game_score[i] == before_games[i] + 1 and game_score[1-i] == before_games[1-i]]
        completed_game_side = changed_games[0] if len(changed_games) == 1 else None
        server_code = fields.get("HG") or fields.get("HN")
        server = {"1": 0, "2": 1}.get(server_code)
        raw_points = fields.get("HL", "")
        point_states = []
        # HC/HE are game scores in ordinary rows and point scores in the
        # separate HB="Tiebreak - Set N" section. A normal game ending 6:6
        # can still contain deuce/advantage; it is not itself the tiebreak.
        tie_break = in_tiebreak or bool(
            game_score == [6, 6] and any(
                re.fullmatch(r"\s*\d+\s*:\s*\d+\s*", p.split("|", 1)[0])
                and any(int(v) not in {0, 15, 30, 40} for v in re.findall(r"\d+", p.split("|", 1)[0]))
                for p in raw_points.split(",") if p.strip()
            )
        )
        if not in_tiebreak and not raw_points and "HD" in fields and "HF" in fields and sorted(game_score) == [6, 7]:
            tiebreak_results[current_set] = game_score
            continue
        if in_tiebreak and not raw_points:
            point_states.append(tuple(game_score))
        for raw_point in raw_points.split(","):
            point = raw_point.split("|", 1)[0].strip()
            if not point:
                continue
            score = re.search(r"^\s*(\d+)\s*:\s*(\d+)\s*$", point)
            if score and tie_break:
                point_states.append((int(score.group(1)), int(score.group(2))))
                continue
            if tie_break:
                gaps.append({"set": current_set, "reason": "invalid_tiebreak_point_score"})
                continue
            from tennis_adapter.point_history import _point_pair
            parsed = _point_pair(point)
            if parsed:
                point_states.append(parsed)
            else:
                gaps.append({"set": current_set, "game_score": game_score, "reason": "unrecognized_feed_point"})

        if tie_break and point_states:
            before = tiebreak_score.get(current_set, [0, 0])
            for point_index, after_tuple in enumerate(point_states):
                after = list(after_tuple)
                changed = [i for i in (0, 1) if after[i] == before[i] + 1 and after[1-i] == before[1-i]]
                winner = changed[0] if len(changed) == 1 else None
                events.append({"set": current_set, "game_score": [6, 6], "point_index": sum(after) - 1,
                               "tie_break": True, "state_before": before, "state_after": after,
                               "point_score": after, "server": server, "winner": winner,
                               "confirmed": winner is not None, "retrospective": True,
                               "quality": "confirmed" if winner is not None else "ambiguous"})
                if winner is None:
                    gaps.append({"set": current_set, "tie_break_score": after, "reason": "invalid_or_unanchored_tiebreak_transition"})
                before = after
            tiebreak_score[current_set] = before
            if current_set not in completed_tiebreaks and max(before) >= 7 and abs(before[0] - before[1]) >= 2:
                winner = 0 if before[0] > before[1] else 1
                set_score = [7, 6] if winner == 0 else [6, 7]
                if current_set in tiebreak_results and tiebreak_results[current_set] != set_score:
                    gaps.append({"set": current_set, "reason": "tiebreak_final_score_mismatch"})
                completed_tiebreaks.add(current_set)
                events.append({"set": current_set, "game_score": set_score, "point_index": None,
                               "state_before": before, "state_after": {"set_result": set_score},
                               "point_score": None, "server": server, "winner": winner,
                               "confirmed": True, "tie_break": True, "retrospective": True,
                               "quality": "terminal_from_tiebreak_result"})
        else:
            before = ("0", "0")
            for point_index, after in enumerate(point_states):
                winner = infer_point_winner(before, after)
                events.append({"set": current_set, "game_score": game_score, "point_index": point_index,
                               "state_before": list(before), "state_after": list(after),
                               "point_score": list(after), "server": server, "winner": winner,
                               "confirmed": winner is not None, "retrospective": True,
                               "quality": "confirmed" if winner is not None else "ambiguous"})
                before = after
            winner_code = fields.get("HK")
            if point_states and winner_code in {"1", "2"}:
                possible = []
                for side in (0, 1):
                    if before[side] == "AD" or (before[side] == "40" and before[1-side] in {"0", "15", "30"}):
                        possible.append(side)
                reported_winner = {"1": 0, "2": 1}.get(winner_code)
                if (len(possible) == 1 and completed_game_side == possible[0]
                        and reported_winner == possible[0]):
                    events.append({"set": current_set, "game_score": game_score,
                                   "point_index": len(point_states), "state_before": list(before),
                                   "state_after": {"game_result": game_score}, "point_score": None,
                                   "server": server, "winner": possible[0], "confirmed": True,
                                   "retrospective": True, "quality": "terminal_from_game_result"})
                else:
                    gaps.append({"set": current_set, "game_score": game_score, "reason": "ambiguous_terminal_point"})
            elif not point_states and winner_code in {"1", "2"}:
                gaps.append({"set": current_set, "game_score": game_score, "reason": "point_sequence_missing"})
            previous_game_score[current_set] = game_score

    if not sets_seen:
        gaps.append({"reason": "point_by_point_feed_missing"})
    for set_number in tiebreak_results.keys() - completed_tiebreaks:
        gaps.append({"set": set_number, "reason": "tiebreak_point_sequence_missing"})
    quality = {"event_count": len(events), "confirmed_points": sum(
        bool(event["confirmed"]) and event.get("quality") in {"confirmed", "terminal_from_game_result"}
        for event in events), "ambiguous_points": sum(not event["confirmed"] for event in events),
        "gaps": gaps, "complete": bool(events) and not gaps, "set_numbers": sorted(sets_seen),
        "feed_signature": signature}
    return {"parser_version": PARSER_VERSION, "events": events, "sets": sorted(sets_seen), "quality": quality}


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
    # The order of player IDs in the match URL is not authoritative home/away
    # evidence. Flashscore may order URL slugs differently from the scoreboard
    # participants. Only retain side orientation from an explicit manual
    # confirmation; imported source observations resolve it from source IDs.
    orientation = evidence.get("external_home_is_fonbet_a")
    if evidence.get("resolution") != "manual_confirmation" or not isinstance(orientation, bool):
        orientation = None
    return canonical, orientation


def _source_dom_side_orientation(document_html: str, participant_a_id: str, participant_b_id: str):
    """Resolve Flashscore home/away only from its scoreboard DOM and confirmed profile IDs."""
    if not document_html or not participant_a_id or not participant_b_id:
        return None
    try:
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(document_html)
    except Exception:
        return None

    def profile_id(side: str):
        hrefs = tree.xpath(
            f'//*[contains(concat(" ", normalize-space(@class), " "), " duelParticipant__{side} ")]'
            '//a[contains(@href, "/player/")]/@href'
        )
        ids = set()
        for href in hrefs:
            match = re.search(r"/player/[^/]+/([A-Za-z0-9]+)/?$", urlparse(href).path)
            if match:
                ids.add(match.group(1))
        return next(iter(ids)) if len(ids) == 1 else None

    home_id, away_id = profile_id("home"), profile_id("away")
    if home_id == participant_a_id and away_id == participant_b_id:
        return True
    if home_id == participant_b_id and away_id == participant_a_id:
        return False
    return None


def _source_dom_doubles_side_orientation(document_html: str, side_a_ids: list[str], side_b_ids: list[str]):
    """Confirm both two-player rosters and their scoreboard sides from archived DOM."""
    expected_a, expected_b = set(map(str, side_a_ids)), set(map(str, side_b_ids))
    if len(expected_a) != 2 or len(expected_b) != 2 or expected_a & expected_b:
        return None
    if not document_html:
        return None
    try:
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(document_html)
    except Exception:
        return None

    def profile_ids(side: str) -> set[str]:
        hrefs = tree.xpath(
            f'//*[contains(concat(" ", normalize-space(@class), " "), " duelParticipant__{side} ")]'
            '//a[contains(@href, "/player/")]/@href'
        )
        ids = set()
        for href in hrefs:
            match = re.search(r"/player/[^/]+/([A-Za-z0-9]+)/?$", urlparse(href).path)
            if match:
                ids.add(match.group(1))
        return ids

    home_ids, away_ids = profile_ids("home"), profile_ids("away")
    if len(home_ids) != 2 or len(away_ids) != 2 or home_ids & away_ids:
        return None
    if home_ids == expected_a and away_ids == expected_b:
        return True
    if home_ids == expected_b and away_ids == expected_a:
        return False
    return None


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


def _match_url_has_doubles_player_ids(match_url: str, side_a_ids: list[str], side_b_ids: list[str]) -> bool:
    """Use four IDs only to find candidates; URL order never proves team side or orientation."""
    expected = set(map(str, side_a_ids)) | set(map(str, side_b_ids))
    if len(set(side_a_ids)) != 2 or len(set(side_b_ids)) != 2 or len(expected) != 4:
        return False
    segments = [segment for segment in urlparse(match_url or "").path.split("/") if segment]
    tennis_index = next((i for i, segment in enumerate(segments) if segment == "tennis"), None)
    if tennis_index is None:
        return False
    ids_in_path = set()
    for slug in segments[tennis_index + 1:]:
        for player_id in expected:
            if re.search(rf"(?:^|-){re.escape(player_id)}(?:-|$)", slug):
                ids_in_path.add(player_id)
    return ids_in_path == expected

def _discover_live_match(player_a_id: str | tuple[str, str], player_b_id: str | tuple[str, str],
                         source_access=None, excluded_match_ids=None):
    """Find candidate links by confirmed player IDs, reusing one source page for 30s."""
    if not player_a_id or not player_b_id: return None
    doubles = isinstance(player_a_id, tuple) and isinstance(player_b_id, tuple)
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
                observed_at = datetime.now(timezone.utc)
                html = page.content()
                raw_hash = hashlib.sha256(html.encode()).hexdigest()
                raw_key = ResearchStorage().archive_document("flashscore_live_board", raw_hash, {
                    "source": "flashscore", "url": url, "html": html,
                    "retrieved_at": observed_at.isoformat(), "content_hash": raw_hash,
                })
                _LIVE_MATCH_CACHE.update(links=links, expires=time.monotonic() + 30,
                    raw_hash=raw_hash, raw_object_key=raw_key, observed_at=observed_at.isoformat())
        # The same live fixture can be exposed through more than one link (for
        # example, a pinned card and its normal event row). Treat repeated URLs
        # for one stable match ID as one candidate, not as an ambiguity.
        candidates_by_id = {}
        for item in links:
            href = item.get("href", "")
            if doubles:
                ids_match = _match_url_has_doubles_player_ids(href, list(player_a_id), list(player_b_id))
            else:
                ids_match = _match_url_has_player_ids(href, str(player_a_id), str(player_b_id))
            if not ids_match or (not doubles and "/doubles/" in urlparse(href).path.casefold()):
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
        return {"status": "matched", "source_match_id":mid.group(1), "url":href.split("/summary",1)[0]+f"/summary/?{urlencode({'mid':mid.group(1)})}",
                "raw_hash": _LIVE_MATCH_CACHE.get("raw_hash"),
                "raw_object_key": _LIVE_MATCH_CACHE.get("raw_object_key"),
                "observed_at": _LIVE_MATCH_CACHE.get("observed_at")}


def _discover_live_doubles_match(side_a_ids: list[str], side_b_ids: list[str],
                                 source_access=None, excluded_match_ids=None):
    """Return a live candidate containing all four IDs; callers must verify DOM rosters."""
    if (len(set(side_a_ids)) != 2 or len(set(side_b_ids)) != 2
            or set(side_a_ids) & set(side_b_ids)):
        return None
    # Share the bounded cached board and source request path with singles lookup.
    return _discover_live_match(tuple(side_a_ids), tuple(side_b_ids), source_access, excluded_match_ids)


def _find_doubles_archive_candidate(event_id, side_a_ids: tuple[str, str],
                                    side_b_ids: tuple[str, str], *, as_of: datetime):
    """Find a completed doubles candidate seen on all four confirmed player profiles.

    Profile history is candidate evidence only. The caller must still confirm
    both two-player teams from the match scoreboard DOM before linking it.
    """
    import uuid
    from db.connection import SessionLocal
    from db.models import Event, League, TennisDoublesMatchObservation

    provider_ids = set(map(str, (*side_a_ids, *side_b_ids)))
    if len(provider_ids) != 4:
        return {"status": "unmatched", "reason": "DOUBLES_PROVIDER_IDS_NOT_UNIQUE"}
    with SessionLocal() as session:
        event = session.get(Event, uuid.UUID(str(event_id)))
        event_time = (event.scheduled_start_at or event.finished_at) if event is not None else None
        if event is None or event_time is None:
            return {"status": "unmatched", "reason": "DOUBLES_EVENT_TIME_MISSING"}
        observations = session.query(TennisDoublesMatchObservation).filter(
            TennisDoublesMatchObservation.source == "flashscore",
            TennisDoublesMatchObservation.source_profile_player_id.in_(provider_ids),
            TennisDoublesMatchObservation.played_at.between(
                event_time - timedelta(days=2),
                event_time + timedelta(days=2),
            ),
            TennisDoublesMatchObservation.available_at <= as_of,
        ).order_by(TennisDoublesMatchObservation.available_at.desc(),
                   TennisDoublesMatchObservation.id.desc()).all()
        by_match = _group_four_player_doubles_observations(observations, provider_ids)
        candidates = []
        league = session.get(League, event.league_id) if event.league_id else None
        wanted = {token for token in re.findall(r"[a-z0-9]+", str(league.name if league else "").casefold())
                  if len(token) > 3}
        for match_id, player_rows in by_match.items():
            rows = list(player_rows.values())
            payloads = [row.payload or {} for row in rows]
            tournaments = {str(payload.get("tournament") or "").strip() for payload in payloads}
            tournaments.discard("")
            if len(tournaments) > 1:
                continue
            tournament = next(iter(tournaments), "")
            if wanted:
                actual = set(re.findall(r"[a-z0-9]+", tournament.casefold()))
                if not actual or not wanted.intersection(actual):
                    continue
            candidates.append({
                "source_match_id": match_id,
                "url": f"https://www.flashscore.com/match/tennis/summary/?{urlencode({'mid': match_id})}",
                "provider_ids": sorted(provider_ids),
                "observations": [{
                    "profile_player_id": str(row.source_profile_player_id),
                    "observation_hash": row.observation_hash,
                    "source_content_hash": row.source_content_hash,
                    "available_at": row.available_at.isoformat(),
                    "played_at": row.played_at.isoformat(),
                } for row in sorted(rows, key=lambda item: item.source_profile_player_id)],
                "tournament": tournament or None,
            })
        if len(candidates) == 1:
            return {"status": "matched", **candidates[0]}
        if candidates:
            return {"status": "ambiguous", "candidates": candidates,
                    "reason": "MULTIPLE_DOUBLES_ARCHIVE_CANDIDATES"}
        return {"status": "unmatched", "reason": "DOUBLES_ARCHIVE_MATCH_NOT_INDEXED"}


def _fetch_scoreboard_evidence(summary_url: str, *, source_access=None) -> dict:
    """Fetch and durably archive a summary DOM independently of PBP availability."""
    allowed, reason = DomainFilter.is_allowed(summary_url)
    if not allowed:
        return {"status": "unavailable", "reason": f"SUMMARY_URL_REJECTED:{reason}"}
    from .browser_pool import flashscore_chromium
    from .tennis_flashscore import _install_flashscore_request_filter_sync

    with flashscore_chromium.context(
        _browser_launch_options(), locale="en-US", timezone_id="UTC"
    ) as (_browser, context):
        page = context.new_page()
        _install_flashscore_request_filter_sync(page)
        with (source_access or nullcontext)():
            response = page.goto(summary_url, wait_until="domcontentloaded", timeout=25000)
        observed_at = datetime.now(timezone.utc)
        if response and response.status in (403, 429):
            return {"status": "unavailable", "reason": f"HTTP_{response.status}_BLOCKED"}
        if response and response.status >= 400:
            return {"status": "unavailable", "reason": f"HTTP_{response.status}_MATCH_PAGE_UNAVAILABLE"}
        final_url = page.url
        allowed, reason = DomainFilter.is_allowed(final_url)
        if not allowed:
            return {"status": "unavailable", "reason": f"SUMMARY_REDIRECT_REJECTED:{reason}"}
        try:
            page.wait_for_selector(".duelParticipant__home, [data-testid='match-header']", timeout=8000)
        except Exception:
            pass
        document_html = page.content()
        from .flashscore_recovery import classify_response
        challenge = classify_response(response.status if response else 200, document_html)
        if challenge in {"browser_challenge", "interactive_captcha"}:
            return {"status": "unavailable", "reason": "CAPTCHA_CHALLENGE_ABORTED"}
    content_hash = hashlib.sha256(document_html.encode("utf-8")).hexdigest()
    object_key = ResearchStorage().archive_document("flashscore_doubles_scoreboard", content_hash, {
        "source": "flashscore", "source_match_id": parse_qs(urlparse(final_url).query).get("mid", [""])[0],
        "url": final_url, "retrieved_at": observed_at.isoformat(),
        "content_hash": content_hash, "html": document_html,
    })
    if not object_key:
        return {"status": "unavailable", "reason": "SCOREBOARD_RAW_ARCHIVE_FAILED"}
    return {"status": "available", "url": final_url, "html": document_html,
            "retrieved_at": observed_at, "raw_hash": content_hash,
            "raw_object_key": object_key}


def _group_four_player_doubles_observations(observations, provider_ids: set[str]):
    """Return matches seen on all confirmed profiles, keeping latest per profile."""
    if len(provider_ids) != 4:
        return {}
    by_match: dict[str, dict[str, object]] = {}
    for row in observations:
        match_id = str(getattr(row, "source_match_id", "") or "")
        profile_id = str(getattr(row, "source_profile_player_id", "") or "")
        if match_id and profile_id in provider_ids:
            by_match.setdefault(match_id, {}).setdefault(profile_id, row)
    return {match_id: rows for match_id, rows in by_match.items()
            if set(rows) == provider_ids}


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

def _recover_point_documents(summary_url, source_access=None):
    """Collect only source-published set tabs through the same solver session."""
    from html import unescape
    from urllib.parse import urljoin
    from .flashscore_recovery import recover

    result = recover(_preferred_point_history_url(summary_url), source_access=source_access)
    if result is None or result.outcome != "recovered":
        return [], "CAPTCHA_CHALLENGE_ABORTED"
    mid = re.search(r"[?&]mid=([A-Za-z0-9]{5,32})(?:&|$)", summary_url)
    if not mid:
        return [], "MATCH_ID_MISSING"
    links = [urljoin(result.url, unescape(href)) for href in
             re.findall(r'href=[\"\']([^\"\']+)', result.body, re.I)]
    set_urls = _point_by_point_set_tabs(links, mid.group(1))
    if not set_urls:
        return [], "POINT_HISTORY_RECOVERY_NO_SET_TABS"
    documents = []
    def finish(status):
        for document in documents:
            document["collection_status"] = status
        return documents, status
    for set_no, url in sorted(set_urls.items()):
        page = recover(url, source_access=source_access, require_session=True)
        if page is None or page.outcome != "recovered":
            return finish("CAPTCHA_CHALLENGE_ABORTED")
        parsed = parse_point_history_html(page.body)
        if not parsed.get("events"):
            return finish(f"EXPECTED_SET_{set_no}_HISTORY_NOT_AVAILABLE")
        documents.append({"set": set_no, "url": page.url, "html": page.body,
                          "parsed": None, "retrieved_at": datetime.now(timezone.utc),
                          "http_status": page.status, "set_tab_confirmed": True,
                          "collection_status": "complete", **page.diagnostics()})
    return finish("complete")


def fetch_point_documents(
    summary_url: str,
    fetcher=None,
    delay_seconds: float = 1.0,
    source_access=None,
    *,
    skip_browser_when_feed_empty: bool = False,
):
    allowed, reason = DomainFilter.is_allowed(summary_url)
    if not allowed:
        return [], f"URL_REJECTED:{reason}"
    fallback_reason = None
    if fetcher is None:
        mid = re.search(r"[?&]mid=([A-Za-z0-9]{5,32})(?:&|$)", summary_url)
        if mid:
            from .tennis_flashscore import _profile_http_get
            from .flashscore_recovery import diagnostics
            feed_url = _point_history_feed_url(mid.group(1))
            status, body, final_url, error = _profile_http_get(
                feed_url, source_access=source_access, feed=True,
            )
            if error == "CAPTCHA_CHALLENGE_ABORTED":
                return [], error
            if status in (403, 429):
                from .main import _record_flashscore_throttle
                _record_flashscore_throttle()
                return [], f"HTTP_{status}_BLOCKED"
            if status == 200 and not error:
                parsed = _parse_point_history_feed(body)
                if parsed.get("events"):
                    quality = parsed.get("quality") or {}
                    return [{
                        "set": None,
                        "url": final_url,
                        "raw_feed": body,
                        "html": "",
                        "parsed": parsed,
                        "retrieved_at": datetime.now(timezone.utc),
                        "http_status": status,
                        **diagnostics(),
                        "fetch_transport": "flaresolverr" if diagnostics().get("fetch_transport") == "flaresolverr" else "http_feed",
                        "set_tab_confirmed": bool(parsed.get("sets")),
                        "collection_status": "complete" if quality.get("complete") else "partial",
                    }], "complete" if quality.get("complete") else "partial"
                if skip_browser_when_feed_empty:
                    return [], "HTTP_200_NO_PUBLISHED_HISTORY"
                fallback_reason = "HTTP_200_FEED_WITHOUT_USABLE_HISTORY"
            else:
                fallback_reason = error or f"HTTP_{status}_FEED_UNAVAILABLE"
                if "FLASHSCORE_SOURCE_LOCK_TIMEOUT" in fallback_reason:
                    return [], "SOURCE_QUEUE_TIMEOUT"
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
        from .flashscore_recovery import classify_response
        if classify_response(response.status if response else 200, page.content()) in {
            "browser_challenge", "interactive_captcha"
        }:
            return _recover_point_documents(summary_url, source_access)
        if response and response.status in (403, 429): return [], f"HTTP_{response.status}_BLOCKED"
        if response and response.status >= 400: return [], f"HTTP_{response.status}_MATCH_PAGE_UNAVAILABLE"
        allowed, reason = DomainFilter.is_allowed(page.url)
        if not allowed: return [], f"REDIRECT_REJECTED:{reason}"
        page.wait_for_function(
            "() => document.body && (document.body.innerText.length > 50 || document.querySelector('[data-testid=\"match-header\"]'))",
            timeout=5000,
        )
        body = page.locator("body").inner_text().casefold()
        if classify_response(200, body) in {"browser_challenge", "interactive_captcha"}:
            return _recover_point_documents(summary_url, source_access)
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
            item["fetch_transport"] = "browser"
            item["fallback_reason"] = fallback_reason
        return output, "complete" if output and incomplete_reason is None else (incomplete_reason or "unavailable")


def _source_match_summary_url(source_match_id: str, source_url: str) -> str | None:
    """Validate and canonicalize a Flashscore match URL for source-only PBP work."""
    source_match_id = str(source_match_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{5,32}", source_match_id):
        return None
    allowed, _ = DomainFilter.is_allowed(source_url or "")
    if not allowed:
        return None
    parsed = urlparse(source_url)
    if "/match/tennis/" not in parsed.path:
        return None
    if parse_qs(parsed.query).get("mid") != [source_match_id]:
        return None
    path = re.sub(r"/(?:summary|point-by-point)(?:/.*)?$", "", parsed.path).rstrip("/")
    canonical = f"{parsed.scheme}://{parsed.netloc}{path}/summary/?{urlencode({'mid': source_match_id})}"
    canonical_allowed, _ = DomainFilter.is_allowed(canonical)
    return canonical if canonical_allowed else None


def collect_source_point_history(
    source_match_id: str,
    source_url: str,
    attempt_token: str | None = None,
    source_access=None,
) -> dict:
    """Archive PBP against Flashscore identity without requiring a Fonbet link."""
    from db.connection import SessionLocal
    from db.models import TennisPointDocument, TennisPointHistoryVersion, TennisPointSourceRefreshJob

    summary_url = _source_match_summary_url(source_match_id, source_url)
    if not summary_url:
        return {"status": "rejected", "reason": "SOURCE_MATCH_URL_INVALID", "source_match_id": source_match_id}

    def lease_is_current(session) -> bool:
        if not attempt_token:
            return True
        return session.query(TennisPointSourceRefreshJob.source_match_id).filter_by(
            source_match_id=source_match_id, attempt_token=attempt_token, status="running"
        ).filter(TennisPointSourceRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first() is not None

    with SessionLocal() as session:
        if not lease_is_current(session):
            return {"status": "lease_lost", "source_match_id": source_match_id}

    docs, collection_status = fetch_point_documents(summary_url, source_access=source_access)
    if not docs:
        return {"status": collection_status, "source_match_id": source_match_id, "documents_saved": 0}

    storage = ResearchStorage()
    saved = 0
    confirmed_points = 0
    gaps = []
    complete = collection_status == "complete"
    for doc in docs:
        raw_content = doc.get("raw_feed") if doc.get("raw_feed") is not None else doc.get("html", "")
        raw_hash = hashlib.sha256(raw_content.encode()).hexdigest()
        retrieved_at = doc["retrieved_at"]
        quality = {
            **((doc.get("parsed") or {}).get("quality") or {}),
            "set_number": doc.get("set"),
            "set_tab_confirmed": doc.get("set_tab_confirmed"),
            "collection_status": doc.get("collection_status"),
            "fetch_status": doc.get("fetch_status"),
            "fetch_transport": doc.get("fetch_transport", "browser"),
            "fallback_reason": doc.get("fallback_reason"),
            "challenge_type": doc.get("challenge_type"),
            "recovery_result": doc.get("recovery_result"),
            "fonbet_link_status": "unlinked_at_retrieval",
        }
        parsed = doc.get("parsed") or parse_point_history_html(doc.get("html", ""))
        quality = {**(parsed.get("quality") or {}), **quality}
        if not parsed.get("events"):
            quality.setdefault("gaps", []).append("HISTORY_EMPTY_OR_UNRECOGNIZED")
        if quality.get("complete") is not True:
            complete = False
        with SessionLocal() as session:
            if not lease_is_current(session):
                return {"status": "lease_lost", "source_match_id": source_match_id, "documents_saved": saved}
        archive_payload = {
            "source": "flashscore", "url": doc["url"], "source_match_id": source_match_id,
            "retrieved_at": retrieved_at.isoformat(), "http_status": doc.get("http_status"),
            "fetch_status": doc.get("fetch_status"), "fetch_transport": doc.get("fetch_transport", "browser"),
            "challenge_type": doc.get("challenge_type"), "recovery_result": doc.get("recovery_result"),
            "parser_version": PARSER_VERSION, "retrospective": True,
            "fonbet_link_status": "unlinked_at_retrieval",
        }
        archive_payload["raw_feed" if doc.get("raw_feed") is not None else "html"] = raw_content
        archived = storage.archive_document(source_match_id, raw_hash, archive_payload)
        if not archived:
            return {"status": "archive_failed", "source_match_id": source_match_id, "documents_saved": saved}
        parsed_at = datetime.now(timezone.utc)
        with SessionLocal() as session:
            if not lease_is_current(session):
                return {"status": "lease_lost", "source_match_id": source_match_id, "documents_saved": saved}
            latest = session.query(TennisPointDocument).filter_by(
                source="flashscore", source_match_id=source_match_id, source_url=doc["url"]
            ).order_by(TennisPointDocument.retrieved_at.desc(), TennisPointDocument.id.desc()).first()
            if latest and latest.content_hash == raw_hash and latest.parser_version == PARSER_VERSION:
                continue
            document = TennisPointDocument(
                source_match_id=source_match_id, source_url=doc["url"], content_hash=raw_hash,
                archived_path=archived, parser_version=PARSER_VERSION, retrieved_at=retrieved_at,
                parsed_at=parsed_at, available_at=parsed_at, availability_status="known",
                retrospective=True, parse_status="complete" if quality.get("complete") else "partial",
                quality_json=quality,
            )
            session.add(document)
            session.flush()
            events = [{**item, "retrospective": True} for item in parsed.get("events", [])]
            session.add(TennisPointHistoryVersion(
                document_id=document.id, source_match_id=source_match_id, fonbet_event_id=None,
                parser_version=PARSER_VERSION, retrieved_at=retrieved_at, parsed_at=parsed_at,
                available_at=parsed_at, availability_status="known", events_json=events,
                quality_json=quality, retrospective=True,
            ))
            session.commit()
            saved += 1
        confirmed_points += int(quality.get("confirmed_points", 0) or 0)
        gaps.extend(quality.get("gaps") or [])
    return {
        "status": "available" if complete else "partial", "source_match_id": source_match_id,
        "documents_saved": saved, "confirmed_points": confirmed_points, "retrospective": True,
        "fonbet_link_status": "unlinked_at_retrieval", "gaps": gaps,
    }

def _usable_alternative_point_version(*, source: str, events: list, quality: dict,
                                      available_at: datetime | None, retrospective: bool,
                                      is_live: bool, as_of: datetime,
                                      allow_partial: bool = False) -> bool:
    """Check whether an alternative has usable points without overstating completeness.

    A caller may accept an explicitly partial retrospective sequence so we do
    not scrape the same match again merely to rediscover known points. The
    returned quality stays partial and retains its gaps. Live use always needs
    a fresh, non-retrospective observation within the 90-second window.
    """
    from .player_data_coverage import source_capability_enabled

    if source not in {"api_tennis", "match_charting_project"}:
        return False
    if not source_capability_enabled(source, "point_by_point"):
        return False
    cutoff = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    available = available_at.replace(tzinfo=timezone.utc) if available_at and available_at.tzinfo is None else available_at
    if available is None:
        return False
    available = available.astimezone(timezone.utc)
    if available > cutoff or not isinstance(events, list) or not events:
        return False
    confirmed_events = [event for event in events
                        if isinstance(event, dict) and event.get("confirmed") is True]
    if not confirmed_events:
        return False
    if not allow_partial:
        if quality.get("complete") is not True or quality.get("gaps"):
            return False
        # A provider's completeness flag must not hide ambiguous normalized rows.
        if len(confirmed_events) != len(events):
            return False
    if is_live and (retrospective or cutoff - available > timedelta(seconds=90)):
        return False
    return True


def _orient_alternative_point_events(events: list, *, source_first_is_fonbet_a: bool) -> list:
    """Copy source point rows into Fonbet A/B orientation without changing quality."""
    import copy

    output = copy.deepcopy(events)
    if source_first_is_fonbet_a:
        return output
    for event in output:
        if not isinstance(event, dict):
            continue
        for key in ("state_before", "state_after", "point_score", "game_score", "game_score_before"):
            values = event.get(key)
            if isinstance(values, list) and len(values) == 2:
                event[key] = [values[1], values[0]]
            elif isinstance(values, dict):
                for nested in ("game_score", "game_result", "set_result"):
                    pair = values.get(nested)
                    if isinstance(pair, list) and len(pair) == 2:
                        values[nested] = pair[::-1]
        for key in ("winner", "server"):
            value = event.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
                event[key] = 1 - value
    return output


def _link_api_tennis_archive_for_event(session, event, *, as_of: datetime) -> dict | None:
    """Attach an archived API-Tennis PBP tape only through canonical IDs and source IDs.

    This is intentionally limited to finished singles events, a unique canonical
    match in a one-day date window, and an exact provider first/second-player ID
    orientation. Name-only or score-only matches are never linked.
    """
    if str(getattr(event, 'source_event_id', '')).isdigit():
        from .tennis_history_linking import link_api_history
        upgraded = link_api_history(session, event, as_of=as_of)
        if upgraded is not None:
            return upgraded
    from db.models import TennisPlayerProfile, TennisPointDocument, TennisPointHistoryVersion
    from db.models.external_tennis import (
        ExtTennisCanonicalMatch, ExtTennisMatch, ExtTennisMatchObservation,
    )
    from sqlalchemy import and_, or_

    if event.finished_at is None or event.is_live or not event.participant_a_id or not event.participant_b_id:
        return None
    profiles = session.query(TennisPlayerProfile).filter(
        TennisPlayerProfile.participant_id.in_((event.participant_a_id, event.participant_b_id))
    ).all()
    canonical_by_participant = {
        row.participant_id: row.canonical_player_id for row in profiles if row.canonical_player_id
    }
    canonical_a = canonical_by_participant.get(event.participant_a_id)
    canonical_b = canonical_by_participant.get(event.participant_b_id)
    if not canonical_a or not canonical_b or canonical_a == canonical_b:
        return None

    matches = session.query(ExtTennisCanonicalMatch).filter(
        or_(
            and_(ExtTennisCanonicalMatch.winner_player_id == canonical_a,
                 ExtTennisCanonicalMatch.loser_player_id == canonical_b),
            and_(ExtTennisCanonicalMatch.winner_player_id == canonical_b,
                 ExtTennisCanonicalMatch.loser_player_id == canonical_a),
        ),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ExtTennisCanonicalMatch.played_at >= event.scheduled_start_at - timedelta(days=1),
        ExtTennisCanonicalMatch.played_at < event.scheduled_start_at + timedelta(days=2),
    ).all()
    api_matches = []
    for match in matches:
        source_ids = [str(value).split(":", 1)[1] for value in
                      (match.stats or {}).get("sources", [])
                      if isinstance(value, str) and value.startswith("api_tennis:")]
        if source_ids:
            api_matches.append((match, list(dict.fromkeys(source_ids))))
    if len(api_matches) != 1:
        return None
    canonical_match, api_source_ids = api_matches[0]

    provider_match = session.query(ExtTennisMatch).filter(
        ExtTennisMatch.source == "api_tennis",
        ExtTennisMatch.source_match_id.in_(api_source_ids),
        ExtTennisMatch.available_at <= as_of,
    ).order_by(ExtTennisMatch.available_at.desc()).first()
    if provider_match is None:
        return None
    observation = session.query(ExtTennisMatchObservation).filter(
        ExtTennisMatchObservation.source == "api_tennis",
        ExtTennisMatchObservation.source_match_id == provider_match.source_match_id,
        ExtTennisMatchObservation.available_at <= as_of,
    ).order_by(ExtTennisMatchObservation.available_at.desc()).first()
    source_event = (observation.payload or {}).get("event") if observation else None
    try:
        first_id = str(source_event["first_player_key"])
        second_id = str(source_event["second_player_key"])
    except (KeyError, TypeError):
        return None
    source_winner_id = str(provider_match.winner_player_id or "")
    source_loser_id = str(provider_match.loser_player_id or "")
    if not source_winner_id or not source_loser_id or {first_id, second_id} != {source_winner_id, source_loser_id}:
        return None

    source_first_canonical = (canonical_match.winner_player_id if first_id == source_winner_id
                              else canonical_match.loser_player_id)
    if source_first_canonical not in {canonical_a, canonical_b}:
        return None
    source_first_is_a = source_first_canonical == canonical_a

    documents = session.query(TennisPointDocument, TennisPointHistoryVersion).join(
        TennisPointHistoryVersion,
        TennisPointHistoryVersion.document_id == TennisPointDocument.id,
    ).filter(
        TennisPointDocument.source == "api_tennis",
        TennisPointDocument.source_match_id == provider_match.source_match_id,
        TennisPointDocument.available_at <= as_of,
        TennisPointDocument.availability_status == "known",
        TennisPointHistoryVersion.fonbet_event_id.is_(None),
        TennisPointHistoryVersion.available_at <= as_of,
        TennisPointHistoryVersion.availability_status == "known",
    ).order_by(TennisPointHistoryVersion.available_at.desc(),
               TennisPointHistoryVersion.id.desc()).all()
    for document, source_version in documents:
        source_quality = source_version.quality_json or {}
        if not _usable_alternative_point_version(
            source="api_tennis", events=source_version.events_json,
            quality=source_quality, available_at=source_version.available_at,
            retrospective=bool(source_version.retrospective), is_live=False,
            as_of=as_of, allow_partial=True,
        ):
            continue
        linked = session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event.id,
            parser_version="api_tennis_pbp_link_v1",
        ).order_by(TennisPointHistoryVersion.available_at.desc()).first()
        linked_version_created = linked is None
        if linked is None:
            linked_at = datetime.now(timezone.utc)
            linked_quality = {
                **source_quality,
                "source_complete": bool(source_quality.get("complete")),
                "complete": False,
                "link": {
                    "method": "canonical_match_and_provider_player_ids",
                    "canonical_match_key": canonical_match.canonical_key,
                    "fonbet_event_id": str(event.id),
                    "source_first_is_fonbet_a": source_first_is_a,
                    "source_match_id": provider_match.source_match_id,
                    "linked_at": linked_at.isoformat(),
                },
            }
            linked = TennisPointHistoryVersion(
                document_id=document.id, source_match_id=provider_match.source_match_id,
                fonbet_event_id=event.id, parser_version="api_tennis_pbp_link_v1",
                retrieved_at=source_version.retrieved_at, parsed_at=linked_at,
                # Linking knowledge becomes available now; never backdate it to
                # the source observation or the historical match date.
                available_at=linked_at, availability_status="known",
                events_json=_orient_alternative_point_events(
                    source_version.events_json, source_first_is_fonbet_a=source_first_is_a,
                ), quality_json=linked_quality, retrospective=True,
            )
            session.add(linked)
            session.flush()
        return {
            "status": "partial", "source": "api_tennis",
            "source_match_id": provider_match.source_match_id,
            "documents_saved": 0, "confirmed_points": int(source_quality.get("confirmed_points", 0) or 0),
            "retrospective": True, "cached": True, "available_at": linked.available_at,
            "quality": linked.quality_json or source_quality, "complete": False,
            "linked_version_created": linked_version_created,
        }
    return None


def _link_match_charting_archive_for_event(session, event, *, as_of: datetime) -> dict | None:
    """Link MCP's selected chart only by exact canonical names/date/tournament evidence."""
    from db.models import TennisPlayerProfile, TennisPointDocument, TennisPointHistoryVersion
    from db.models.external_tennis import (
        ExtTennisCanonicalMatch, ExtTennisCanonicalPlayer, ExtTennisMatchObservation,
    )
    from sqlalchemy import and_, or_
    from tennis_adapter.external_names import transliterate

    if event.finished_at is None or event.is_live or not event.participant_a_id or not event.participant_b_id:
        return None
    profiles = session.query(TennisPlayerProfile).filter(
        TennisPlayerProfile.participant_id.in_((event.participant_a_id, event.participant_b_id))
    ).all()
    canonical_by_participant = {
        row.participant_id: row.canonical_player_id for row in profiles if row.canonical_player_id
    }
    canonical_a = canonical_by_participant.get(event.participant_a_id)
    canonical_b = canonical_by_participant.get(event.participant_b_id)
    if not canonical_a or not canonical_b or canonical_a == canonical_b:
        return None
    matches = session.query(ExtTennisCanonicalMatch).filter(
        or_(
            and_(ExtTennisCanonicalMatch.winner_player_id == canonical_a,
                 ExtTennisCanonicalMatch.loser_player_id == canonical_b),
            and_(ExtTennisCanonicalMatch.winner_player_id == canonical_b,
                 ExtTennisCanonicalMatch.loser_player_id == canonical_a),
        ),
        ExtTennisCanonicalMatch.duplicate_of_id.is_(None),
        ExtTennisCanonicalMatch.played_at >= event.scheduled_start_at - timedelta(days=1),
        ExtTennisCanonicalMatch.played_at < event.scheduled_start_at + timedelta(days=2),
    ).all()
    if len(matches) != 1:
        return None
    canonical_match = matches[0]
    if not canonical_match.tournament.strip():
        return None
    players = session.query(ExtTennisCanonicalPlayer).filter(
        ExtTennisCanonicalPlayer.id.in_((canonical_a, canonical_b))
    ).all()
    if len(players) != 2:
        return None
    names_by_id = {row.id: " ".join(transliterate(row.display_name).split()) for row in players}
    expected_names = {names_by_id.get(canonical_a), names_by_id.get(canonical_b)}
    if not all(expected_names):
        return None
    generic_tournament_tokens = {
        "open", "championship", "championships", "tournament", "masters",
        "cup", "trophy", "series", "international", "internationals",
        "atp", "wta", "itf", "challenger", "qualifying", "qualifiers",
    }
    tournament_tokens = set(re.findall(r"[a-z0-9]{4,}", transliterate(canonical_match.tournament)))
    tournament_tokens -= generic_tournament_tokens
    if not tournament_tokens:
        return None
    dates = {(canonical_match.played_at + timedelta(days=offset)).strftime("%Y%m%d")
             for offset in (-1, 0, 1)}
    observations = session.query(ExtTennisMatchObservation).filter(
        ExtTennisMatchObservation.source == "match_charting_project",
        ExtTennisMatchObservation.available_at <= as_of,
        ExtTennisMatchObservation.payload["date"].astext.in_(dates),
    ).order_by(ExtTennisMatchObservation.available_at.desc()).all()
    matched_metadata = {}
    for observation in observations:
        payload = observation.payload or {}
        player_1 = " ".join(transliterate(str(payload.get("player_1") or "")).split())
        player_2 = " ".join(transliterate(str(payload.get("player_2") or "")).split())
        if not player_1 or not player_2 or {player_1, player_2} != expected_names:
            continue
        source_tournament_tokens = set(re.findall(
            r"[a-z0-9]{4,}", transliterate(str(payload.get("tournament") or "")),
        ))
        source_tournament_tokens -= generic_tournament_tokens
        if not source_tournament_tokens or source_tournament_tokens != tournament_tokens:
            continue
        # Repeated snapshots of the same stable MCP match id are one candidate;
        # two different ids remain ambiguous and are never joined.
        matched_metadata.setdefault(str(observation.source_match_id), (observation, payload))
    if len(matched_metadata) != 1:
        return None
    source_match_id, (observation, payload) = next(iter(matched_metadata.items()))
    player_1 = " ".join(transliterate(str(payload.get("player_1") or "")).split())
    source_first_is_a = player_1 == names_by_id[canonical_a]
    documents = session.query(TennisPointDocument, TennisPointHistoryVersion).join(
        TennisPointHistoryVersion,
        TennisPointHistoryVersion.document_id == TennisPointDocument.id,
    ).filter(
        TennisPointDocument.source == "match_charting_project",
        TennisPointDocument.source_match_id == source_match_id,
        TennisPointDocument.available_at <= as_of,
        TennisPointDocument.availability_status == "known",
        TennisPointHistoryVersion.fonbet_event_id.is_(None),
        TennisPointHistoryVersion.available_at <= as_of,
        TennisPointHistoryVersion.availability_status == "known",
    ).order_by(TennisPointHistoryVersion.available_at.desc(),
               TennisPointHistoryVersion.id.desc()).all()
    for document, source_version in documents:
        source_quality = source_version.quality_json or {}
        if not _usable_alternative_point_version(
            source="match_charting_project", events=source_version.events_json,
            quality=source_quality, available_at=source_version.available_at,
            retrospective=bool(source_version.retrospective), is_live=False,
            as_of=as_of, allow_partial=True,
        ):
            continue
        parser_version = "match_charting_project_pbp_link_v1"
        linked = session.query(TennisPointHistoryVersion).filter_by(
            document_id=document.id, fonbet_event_id=event.id,
            parser_version=parser_version,
        ).order_by(TennisPointHistoryVersion.available_at.desc()).first()
        linked_version_created = linked is None
        if linked is None:
            linked_at = datetime.now(timezone.utc)
            linked_quality = {
                **source_quality, "source_complete": bool(source_quality.get("complete")),
                "complete": False,
                "link": {
                    "method": "unique_canonical_pair_date_tournament_and_exact_names",
                    "canonical_match_key": canonical_match.canonical_key,
                    "fonbet_event_id": str(event.id),
                    "source_first_is_fonbet_a": source_first_is_a,
                    "source_match_id": source_match_id,
                    "source_observation_available_at": observation.available_at.isoformat(),
                    "linked_at": linked_at.isoformat(),
                },
            }
            linked = TennisPointHistoryVersion(
                document_id=document.id, source_match_id=source_match_id,
                fonbet_event_id=event.id, parser_version=parser_version,
                retrieved_at=source_version.retrieved_at, parsed_at=linked_at,
                available_at=linked_at, availability_status="known",
                events_json=_orient_alternative_point_events(
                    source_version.events_json, source_first_is_fonbet_a=source_first_is_a,
                ), quality_json=linked_quality, retrospective=True,
            )
            session.add(linked)
            session.flush()
        return {
            "status": "partial", "source": "match_charting_project",
            "source_match_id": source_match_id, "documents_saved": 0,
            "confirmed_points": int(source_quality.get("confirmed_points", 0) or 0),
            "retrospective": True, "cached": True, "available_at": linked.available_at,
            "quality": linked.quality_json or source_quality, "complete": False,
            "linked_version_created": linked_version_created,
        }
    return None


def usable_alternative_history_for_event(session, event, *, as_of: datetime | None = None) -> dict | None:
    """Return the best linked alternative PBP snapshot without hiding partial quality."""
    from db.models import TennisPointDocument, TennisPointHistoryVersion

    cutoff = as_of or datetime.now(timezone.utc)
    is_live = bool(event.is_live and getattr(event, "finished_at", None) is None)
    versions = session.query(TennisPointHistoryVersion, TennisPointDocument).join(
        TennisPointDocument, TennisPointDocument.id == TennisPointHistoryVersion.document_id,
    ).filter(
        TennisPointHistoryVersion.fonbet_event_id == event.id,
        TennisPointHistoryVersion.available_at <= cutoff,
        TennisPointHistoryVersion.availability_status == "known",
        TennisPointDocument.available_at <= cutoff,
        TennisPointDocument.availability_status == "known",
        TennisPointDocument.parse_status.in_(("complete", "partial")),
        TennisPointDocument.source.in_(("api_tennis", "match_charting_project")),
    ).order_by(TennisPointHistoryVersion.available_at.desc(),
               TennisPointHistoryVersion.id.desc()).all()
    for version, document in versions:
        quality = version.quality_json or {}
        if (document.source == "api_tennis" and getattr(version, 'parser_version', '') == "api_tennis_pbp_link_v1"
                and not is_live and str(getattr(event, 'source_event_id', '')).isdigit()):
            from .tennis_history_linking import link_api_history
            upgraded = link_api_history(session, event, as_of=cutoff)
            if upgraded is not None:
                return upgraded
        if _usable_alternative_point_version(
            source=document.source, events=version.events_json,
            quality=quality, available_at=version.available_at,
            retrospective=bool(version.retrospective), is_live=is_live, as_of=cutoff,
            allow_partial=True,
        ):
            complete = quality.get("complete") is True and not quality.get("gaps") and len(
                [event for event in (version.events_json or [])
                 if isinstance(event, dict) and event.get("confirmed") is True]
            ) == len(version.events_json or [])
            return {
                "status": "available" if complete else "partial", "source": document.source,
                "source_match_id": document.source_match_id,
                "documents_saved": 0,
                "confirmed_points": int(quality.get("confirmed_points", 0) or 0),
                "retrospective": bool(version.retrospective), "cached": True,
                "available_at": version.available_at, "quality": quality,
                "complete": complete,
            }
    return (_link_api_tennis_archive_for_event(session, event, as_of=cutoff)
            or _link_match_charting_archive_for_event(session, event, as_of=cutoff))


def collect_event_point_history(event_id: str, attempt_token: str | None = None, source_access=None) -> dict:
    from db.connection import SessionLocal
    from db.models import Event, ExtTennisMatch, ExtTennisMatchObservation, TennisPointDocument, TennisPointHistoryVersion, TennisPointMatchLink, TennisPointRefreshJob, TennisDoublesRosterObservation
    import uuid
    now = datetime.now(timezone.utc)
    event_uuid = uuid.UUID(str(event_id))
    side_orientation_source = None
    doubles_roster_ids = None
    doubles_candidate_evidence = None
    is_doubles = False
    with SessionLocal() as session:
        if attempt_token and session.query(TennisPointRefreshJob).filter_by(
            event_id=event_uuid, attempt_token=attempt_token, status="running"
        ).filter(TennisPointRefreshJob.lease_until > datetime.now(timezone.utc)).with_for_update().first() is None:
            return {"status": "lease_lost"}
        event = session.get(Event, event_uuid)
        if event is None or event.sport_code != "tennis": return {"status": "not_found"}
        event_names = (event.participant_a_name, event.participant_b_name)
        is_doubles = "/" in (event.participant_a_name or "") or "/" in (event.participant_b_name or "")
        is_live = bool(event.is_live and getattr(event, "finished_at", None) is None)
        alternative = usable_alternative_history_for_event(session, event, as_of=now)
        if alternative:
            if alternative.get("linked_version_created"):
                session.commit()
            if alternative.get("status") == "available":
                return alternative
        from .player_data_coverage import source_capability_enabled
        if not source_capability_enabled("flashscore", "point_by_point"):
            return {"status": "source_disabled", "reason": "SOURCE_CAPABILITY_DISABLED"}
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
        if is_doubles:
            matches = []
        elif linked_source_ids:
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
        from db.models import League
        league = session.get(League, event.league_id) if event.league_id else None
        from .main import _confirmed_flashscore_player_id
        if is_doubles:
            from .tennis_doubles import confirmed_provider_roster
            roster_observation = session.query(TennisDoublesRosterObservation).filter(
                TennisDoublesRosterObservation.event_id == event_uuid,
                TennisDoublesRosterObservation.available_at <= now,
            ).order_by(TennisDoublesRosterObservation.available_at.desc(),
                       TennisDoublesRosterObservation.id.desc()).first()
            doubles_roster_ids = (confirmed_provider_roster(
                roster_observation.roster_json or {},
                lambda participant_id: _confirmed_flashscore_player_id(session, participant_id),
            ) if roster_observation else None)
            flashscore_a = flashscore_b = ""
        else:
            flashscore_a = _confirmed_flashscore_player_id(session, event.participant_a_id) or ""
            flashscore_b = _confirmed_flashscore_player_id(session, event.participant_b_id) or ""
        if flashscore_a and flashscore_a == flashscore_b:
            return {"status": "unmatched", "reason": "PLAYER_SOURCE_IDS_CONFLICT"}
        league_name = league.name if league else ""
        source_side_orientation = {}
        source_side_orientation_source = {}
        def player_match(row):
            if row.home_name.strip().endswith("/") or row.away_name.strip().endswith("/"):
                return False
            if "/" in row.home_name or "/" in row.away_name:
                return False
            # Candidate discovery requires two independently confirmed IDs.
            # Scoreboard orientation is resolved only from retained source DOM.
            if not all((flashscore_a, flashscore_b, row.home_player_id, row.away_player_id)):
                return False
            if flashscore_a == flashscore_b or row.home_player_id == row.away_player_id:
                return False
            orientation = _external_home_is_fonbet_a(
                event_names, (row.home_name, row.away_name),
                (flashscore_a, flashscore_b), (row.home_player_id, row.away_player_id),
            )
            participants_ok = orientation is not None
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
            side_orientation_source = source_side_orientation_source.get(source_match_id)
        else:
            confirmed_link_pages = []
            for link in confirmed_links:
                if is_doubles:
                    evidence = link.evidence_json or {}
                    recorded = {side: tuple(str(value) for value in evidence.get(f"flashscore_side_{side.lower()}_ids", []))
                                for side in ("A", "B")}
                    same_rosters = bool(doubles_roster_ids and all(
                        set(recorded[side]) == set(doubles_roster_ids[side]) for side in ("A", "B")))
                    if (same_rosters
                            and evidence.get("side_orientation_source") == "flashscore_scoreboard_dom_confirmed_four_profile_ids"
                            and isinstance(evidence.get("external_home_is_fonbet_a"), bool)):
                        resolved_url = evidence.get("source_url") or ""
                        if resolved_url:
                            confirmed_link_pages.append((link, resolved_url, evidence["external_home_is_fonbet_a"]))
                    continue
                resolved = _confirmed_link_page(link, flashscore_a, flashscore_b)
                if resolved:
                    confirmed_link_pages.append((link, resolved[0], resolved[1]))
            if len(confirmed_link_pages) == 1:
                link, url, external_home_is_a = confirmed_link_pages[0]
                source_match_id = str(link.source_match_id)
                source_side_orientation[source_match_id] = external_home_is_a
                evidence = link.evidence_json or {}
                if evidence.get("resolution") == "manual_confirmation" and isinstance(external_home_is_a, bool):
                    link_method = "manual_confirmed_source_link"
                    side_orientation_source = "explicit_manual_confirmation"
                else:
                    link_method = "confirmed_link_source_url_without_history_import"
                    side_orientation_source = evidence.get("side_orientation_source")
            else:
                source_match_id, url = "", None
                link_method, external_home_is_a = "live_match_anchor_external_player_ids", None
                side_orientation_source = None
    if not url and is_doubles and doubles_roster_ids:
        discovered = (_discover_live_doubles_match(
            doubles_roster_ids["A"], doubles_roster_ids["B"], source_access=source_access,
            excluded_match_ids=rejected_source_ids) if is_live else None)
        normalized = _normalize_live_match_discovery(discovered)
        if normalized["status"] == "matched":
            source_match_id, url = normalized["source_match_id"], normalized["url"]
            link_method = "four_confirmed_player_ids_then_scoreboard_dom"
            doubles_candidate_evidence = {"candidate_source": "live_board_four_confirmed_player_ids",
                "live_board_raw_hash": normalized.get("raw_hash"),
                "live_board_raw_object_key": normalized.get("raw_object_key"),
                "live_board_observed_at": normalized.get("observed_at")}
        else:
            archive_candidate = _find_doubles_archive_candidate(
                event_uuid, doubles_roster_ids["A"], doubles_roster_ids["B"], as_of=now)
            if archive_candidate["status"] == "matched":
                source_match_id, url = archive_candidate["source_match_id"], archive_candidate["url"]
                link_method = "four_profile_archive_observations_then_scoreboard_dom"
                doubles_candidate_evidence = {
                    "candidate_source": "four_confirmed_profile_match_observations",
                    "profile_match_observations": archive_candidate["observations"],
                    "tournament": archive_candidate.get("tournament"),
                }
            elif normalized["status"] == "ambiguous" or archive_candidate["status"] == "ambiguous":
                live_candidates = normalized.get("candidates", []) if normalized["status"] == "ambiguous" else []
                archive_candidates = archive_candidate.get("candidates", []) if archive_candidate["status"] == "ambiguous" else []
                return {"status": "ambiguous", "reason": "MULTIPLE_DOUBLES_MATCH_CANDIDATES",
                        "candidate_count": max(len(live_candidates), len(archive_candidates)),
                        "source_match_ids": sorted({str(row.get("source_match_id"))
                            for row in (*live_candidates, *archive_candidates) if row.get("source_match_id")})}
    elif not url and not is_doubles and flashscore_a and flashscore_b:
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
        if is_doubles and not doubles_roster_ids:
            return {"status": "unmatched", "reason": "DOUBLES_ROSTER_PROVIDER_IDS_MISSING"}
        return {"status": "unmatched", "reason": "FLASHSCORE_MATCH_LINK_NOT_FOUND" if flashscore_a and flashscore_b else "PLAYER_SOURCE_IDS_MISSING"}
    docs, status = [], "not_requested"
    if is_doubles and doubles_roster_ids:
        if external_home_is_a is None:
            scoreboard = _fetch_scoreboard_evidence(url, source_access=source_access)
            if scoreboard.get("status") == "available":
                external_home_is_a = _source_dom_doubles_side_orientation(
                    scoreboard["html"], doubles_roster_ids["A"], doubles_roster_ids["B"])
                if external_home_is_a is not None:
                    side_orientation_source = "flashscore_scoreboard_dom_confirmed_four_profile_ids"
                    source_match_id = str(parse_qs(urlparse(scoreboard["url"]).query).get("mid", [source_match_id])[0])
                    if doubles_candidate_evidence is None:
                        doubles_candidate_evidence = {"candidate_source": "unlinked_doubles_candidate"}
                    doubles_candidate_evidence.update({"scoreboard_url": scoreboard["url"],
                        "scoreboard_raw_hash": scoreboard["raw_hash"],
                        "scoreboard_raw_object_key": scoreboard["raw_object_key"],
                        "scoreboard_retrieved_at": scoreboard["retrieved_at"].isoformat()})
                    from db.connection import SessionLocal
                    from db.models import TennisPointMatchLink
                    from sqlalchemy import text
                    with SessionLocal() as session:
                        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                            {"key": "flashscore-pbp-link:" + source_match_id})
                        current_rows = session.query(TennisPointMatchLink).filter_by(
                            source_match_id=source_match_id).filter(
                            TennisPointMatchLink.available_at <= scoreboard["retrieved_at"]
                        ).order_by(TennisPointMatchLink.available_at.desc(),
                                   TennisPointMatchLink.id.desc()).all()
                        current = current_rows[0] if current_rows else None
                        if current and current.status in {"matched", "manual"} and current.fonbet_event_id != event_uuid:
                            return {"status": "unmatched", "reason": "SOURCE_MATCH_ALREADY_LINKED",
                                    "source_match_id": source_match_id}
                        if not current or current.status not in {"matched", "manual"} or current.fonbet_event_id != event_uuid:
                            evidence = {"method": link_method,
                                "fonbet_participants": list(event_names),
                                "flashscore_side_a_ids": list(doubles_roster_ids["A"]),
                                "flashscore_side_b_ids": list(doubles_roster_ids["B"]),
                                "external_home_is_fonbet_a": external_home_is_a,
                                "side_orientation_source": side_orientation_source,
                                "source_url": scoreboard["url"],
                                "raw_hash": scoreboard["raw_hash"],
                                "raw_object_key": scoreboard["raw_object_key"],
                                **doubles_candidate_evidence}
                            session.add(TennisPointMatchLink(source_match_id=source_match_id,
                                fonbet_event_id=event_uuid, status="matched", confidence=1.0,
                                evidence_json=evidence, available_at=scoreboard["retrieved_at"]))
                        session.commit()
            docs, status = fetch_point_documents(
                url, source_access=source_access,
                skip_browser_when_feed_empty=is_live,
            )
            if external_home_is_a is None:
                for doc in docs:
                    if doc.get("html"):
                        dom_orientation = _source_dom_doubles_side_orientation(
                            doc["html"], doubles_roster_ids["A"], doubles_roster_ids["B"])
                        if dom_orientation is not None:
                            external_home_is_a = dom_orientation
                            side_orientation_source = "flashscore_scoreboard_dom_confirmed_four_profile_ids"
                            break
        else:
            docs, status = fetch_point_documents(
                url, source_access=source_access,
                skip_browser_when_feed_empty=is_live,
            )
            for doc in docs:
                if doc.get("html"):
                    dom_orientation = _source_dom_doubles_side_orientation(
                        doc["html"], doubles_roster_ids["A"], doubles_roster_ids["B"])
                    if dom_orientation is not None:
                        external_home_is_a = dom_orientation
                        side_orientation_source = "flashscore_scoreboard_dom_confirmed_four_profile_ids"
                        break
        if external_home_is_a is None:
            if not docs:
                return {"status": status, "reason": "DOUBLES_SCOREBOARD_EVIDENCE_UNAVAILABLE",
                        "source_match_id": source_match_id}
            return {"status": "unmatched", "reason": "DOUBLES_SCOREBOARD_SIDE_ORIENTATION_UNCONFIRMED",
                    "source_match_id": source_match_id}
        if not docs:
            return {"status": status, "source_match_id": source_match_id,
                    "match_link_confirmed": True}
    elif external_home_is_a is None:
        docs, status = fetch_point_documents(
            url, source_access=source_access,
            skip_browser_when_feed_empty=is_live,
        )
        if not docs: return {"status": status, "source_match_id": source_match_id}
        for doc in docs:
            external_home_is_a = _source_dom_side_orientation(doc.get("html", ""), flashscore_a, flashscore_b)
            if external_home_is_a is not None:
                side_orientation_source = "flashscore_scoreboard_dom_confirmed_profile_ids"
                break
    else:
        docs, status = fetch_point_documents(
            url, source_access=source_access,
            skip_browser_when_feed_empty=is_live,
        )
    storage = ResearchStorage(); saved = 0; confirmed = 0; gaps = []
    # Flashscore does not timestamp individual points. Keep availability at
    # document receipt; only classify the document as observed when a fresh,
    # already-persisted Fonbet live snapshot proves the match was being watched.
    all_retrospective = True
    for doc in docs:
        retrospective = not _document_was_observed_live(event_uuid, doc["retrieved_at"])
        all_retrospective = all_retrospective and retrospective
        raw_content = doc.get("raw_feed") if doc.get("raw_feed") is not None else doc.get("html", "")
        raw_hash = hashlib.sha256(raw_content.encode()).hexdigest()
        archive_payload = {"source": "flashscore", "url": doc["url"],
            "source_match_id": source_match_id, "retrieved_at": doc["retrieved_at"].isoformat(),
            "http_status": doc.get("http_status"), "fetch_status": doc.get("fetch_status"),
            "fetch_transport": doc.get("fetch_transport", "browser"),
            "fallback_reason": doc.get("fallback_reason"),
            "challenge_type": doc.get("challenge_type"),
            "recovery_result": doc.get("recovery_result"),
            "parser_version": PARSER_VERSION, "retrospective": retrospective}
        archive_payload["raw_feed" if doc.get("raw_feed") is not None else "html"] = raw_content
        archived = storage.archive_document(source_match_id, raw_hash, archive_payload)
        if not archived: return {"status": "archive_failed", "source_match_id": source_match_id}
        parsed_at = datetime.now(timezone.utc)
        parsed = doc.get("parsed") or parse_point_history_html(doc.get("html", ""))
        quality = {
            **(parsed.get("quality") or {}),
            "set_number": doc.get("set"),
            "set_tab_confirmed": doc.get("set_tab_confirmed"),
            "collection_status": doc.get("collection_status"),
            "fetch_status": doc.get("fetch_status"),
            "fetch_transport": doc.get("fetch_transport", "browser"),
            "fallback_reason": doc.get("fallback_reason"),
            "challenge_type": doc.get("challenge_type"),
            "recovery_result": doc.get("recovery_result"),
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
            prior_link = session.query(TennisPointMatchLink).filter_by(
                source_match_id=source_match_id, fonbet_event_id=event_uuid,
            ).filter(TennisPointMatchLink.available_at <= datetime.now(timezone.utc)).order_by(
                TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).first()
            link_evidence = dict(prior_link.evidence_json or {}) if prior_link else {}
            link_evidence.update({"method": link_method,
                "fonbet_participants": list(event_names), "external_home_is_fonbet_a": external_home_is_a,
                "side_orientation_source": side_orientation_source, "raw_hash": raw_hash,
                "raw_object_key": archived, "source_url": doc["url"],
                "flashscore_side_a_ids": list(doubles_roster_ids["A"]) if is_doubles and doubles_roster_ids else None,
                "flashscore_side_b_ids": list(doubles_roster_ids["B"]) if is_doubles and doubles_roster_ids else None,
                "source_retrieved_at": doc["retrieved_at"].isoformat()})
            session.add(TennisPointMatchLink(source_match_id=source_match_id, fonbet_event_id=event_uuid,
                status="matched", confidence=1.0, evidence_json=link_evidence,
                available_at=datetime.now(timezone.utc)))
            session.commit(); saved += 1
        confirmed += quality["confirmed_points"]
        gaps.extend(quality["gaps"])
    complete = status == "complete" and all((doc.get("parsed") or {}).get("quality", {}).get("complete") for doc in docs)
    return {"status": "available" if complete else "partial", "reason": None if complete else status,
            "source_match_id": source_match_id, "documents_saved": saved,
            "confirmed_points": confirmed, "retrospective": all_retrospective, "gaps": gaps}
