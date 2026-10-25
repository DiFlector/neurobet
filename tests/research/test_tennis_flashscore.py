from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio
import hashlib
import threading

import pytest

from app.tennis_flashscore import (
    _run_browser_awaitable,
    FlashscoreTennisImporter,
    _player_name_matches,
    is_auto_confirmable_player_match,
    is_exact_player_name_match,
    _profile_candidates_from_search_payload,
    _profile_search_queries,
    _profile_search_failure_reason,
    _profile_search_exception_reason,
    _search_flashscore_player_endpoint,
    _close_profile_http_client,
    _observation_hash,
    _observation_payload,
    _browser_launch_options,
    _profile_country_id,
    _profile_feed_page_url,
    _profile_initial_feed_from_html,
    is_compatible_profile_parser_version,
    _is_doubles_result,
    is_doubles_player_display,
    _advertised_singles_count_matches,
    _category,
    _parse_match_with_reason,
    _match_urls_from_profile_html,
    _page_has_pagination_progress,
    _profile_page_failure_reason,
    _profile_http_get,
    PARSER_VERSION,
    parse_player_profile,
)
from app.main import _flashscore_source_wait_seconds
from app.player_profiles import (
    _canonical_match_key, _is_duplicate_adjacent_fact, _matching_canonical_match_key,
    _post_match_profile_refresh_due, profile_refresh_timeout_seconds,
    lock_flashscore_source_entities,
)


def test_incremental_profile_refresh_has_shorter_timeout_than_initial_history():
    assert profile_refresh_timeout_seconds(None, {}) == 300
    assert profile_refresh_timeout_seconds(datetime.now(timezone.utc), {}) == 120
    assert profile_refresh_timeout_seconds(None, {
        "FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS": "420",
        "FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS": "75",
    }) == 420
    assert profile_refresh_timeout_seconds(datetime.now(timezone.utc), {
        "FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS": "420",
        "FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS": "75",
    }) == 75
    assert profile_refresh_timeout_seconds(datetime.now(timezone.utc), {
        "FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS": "1",
    }) == 30


def test_browser_awaitable_handles_sync_scraper_called_from_running_event_loop():
    async def caller():
        caller_thread = threading.get_ident()

        async def browser_work():
            await asyncio.sleep(0)
            return threading.get_ident(), "ok"

        result = _run_browser_awaitable(browser_work())
        return caller_thread, result

    caller_thread, (browser_thread, value) = asyncio.run(caller())
    assert value == "ok"
    assert browser_thread != caller_thread


@pytest.fixture(autouse=True)
def reset_profile_search_http_pool():
    _close_profile_http_client()
    yield
    _close_profile_http_client()
from app.point_history import (
    _confirmed_link_page, _discover_live_match, _external_home_is_fonbet_a, _external_match_state,
    _match_page_url, _match_url_has_player_ids,
    _normalize_live_match_discovery,
    _source_match_summary_url,
    _open_point_history_entry, _point_by_point_index_url,
    _point_by_point_set_tabs, _point_by_point_set_url, _preferred_point_history_url,
    _parse_point_history_feed, _point_history_feed_url, fetch_point_documents,
)


def test_live_match_discovery_ambiguity_is_not_treated_as_a_match_record():
    ambiguous = _normalize_live_match_discovery({
        "status": "ambiguous",
        "candidates": [
            {"source_match_id": "matcha", "url": "https://www.flashscore.com/match/tennis/a/?mid=matcha", "label": "A"},
            {"source_match_id": "matchb", "url": "https://www.flashscore.com/match/tennis/b/?mid=matchb", "label": "B"},
        ],
    })
    assert ambiguous["status"] == "ambiguous"
    assert [row["source_match_id"] for row in ambiguous["candidates"]] == ["matcha", "matchb"]
    assert "source_match_id" not in ambiguous

    matched = _normalize_live_match_discovery({
        "status": "matched", "source_match_id": "matchc",
        "url": "https://www.flashscore.com/match/tennis/c/?mid=matchc",
    })
    assert matched["status"] == "matched"
    assert matched["source_match_id"] == "matchc"
    assert _normalize_live_match_discovery({"status": "ambiguous", "candidates": [{}]})["status"] == "unmatched"


def test_source_only_pbp_url_requires_allowed_tennis_match_and_exact_source_id():
    url = "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/point-by-point/set-1/?mid=AbCd123"
    assert _source_match_summary_url("AbCd123", url) == (
        "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/summary/?mid=AbCd123"
    )
    assert _source_match_summary_url("Other123", url) is None
    assert _source_match_summary_url(
        "AbCd123", "https://example.com/match/tennis/player-a-id/player-b-id/?mid=AbCd123"
    ) is None


def test_live_match_discovery_collapses_duplicate_board_links_for_same_fixture():
    import time
    import app.point_history as point_history

    point_history._LIVE_MATCH_CACHE.update(
        links=[
            {"href": "https://www.flashscore.com/match/tennis/player-a-player-a/player-b-player-b/?mid=fixture1", "text": "A - B"},
            {"href": "https://www.flashscore.com/match/tennis/player-a-player-a/player-b-player-b/summary/?mid=fixture1", "text": "A - B"},
        ],
        expires=time.monotonic() + 30,
    )
    result = _discover_live_match("player-a", "player-b")
    assert result["status"] == "matched"
    assert result["source_match_id"] == "fixture1"


def test_live_match_discovery_requires_exact_player_ids_in_separate_slugs():
    import time
    import app.point_history as point_history

    assert _match_url_has_player_ids(
        "https://www.flashscore.com/match/tennis/adkar-vaishnavi-riKJvzYF/shkutova-anna-O8EO0106/?mid=rRgwzmTF",
        "riKJvzYF", "O8EO0106",
    )
    assert _match_url_has_player_ids(
        "https://www.flashscore.com/match/tennis/shkutova-anna-O8EO0106/adkar-vaishnavi-riKJvzYF/?mid=rRgwzmTF",
        "riKJvzYF", "O8EO0106",
    )
    assert not _match_url_has_player_ids(
        "https://www.flashscore.com/match/tennis/adkar-riKJvzYF2/shkutova-O8EO0106/?mid=rRgwzmTF",
        "riKJvzYF", "O8EO0106",
    )
    assert not _match_url_has_player_ids(
        "https://www.flashscore.com/match/tennis/adkar-riKJvzYF/?player=O8EO0106&mid=rRgwzmTF",
        "riKJvzYF", "O8EO0106",
    )
    point_history._LIVE_MATCH_CACHE.update(links=[
        {"href": "https://www.flashscore.com/match/tennis/adkar-vaishnavi-riKJvzYF/shkutova-anna-O8EO0106/?mid=rRgwzmTF", "text": "Adkar — Shkutova"},
        {"href": "https://www.flashscore.com/match/tennis/adkar-riKJvzYF2/shkutova-O8EO0106/?mid=substring-decoy", "text": "decoy"},
    ], expires=time.monotonic() + 30)
    result = _discover_live_match("riKJvzYF", "O8EO0106")
    assert result["status"] == "matched"
    assert result["source_match_id"] == "rRgwzmTF"


def test_exact_player_confirmation_requires_full_ordered_name_match():
    assert is_exact_player_name_match("Tsitsipas Stefanos", "Tsitsipas Stefanos")
    assert is_exact_player_name_match("Tsítsipas Stefanos", "Tsitsipas Stefanos")
    assert is_exact_player_name_match("TSITSIpas   Stefanos", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Tsitsipas S", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Stefanos Tsitsipas", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Tsitsipas Stefanos", "Tsitsipas Stefanos Jr")


def test_overlapping_profile_import_locks_are_deduplicated_and_ordered():
    class Result:
        def all(self):
            return []

    class Session:
        statement = None
        params = None

        def execute(self, statement, params):
            self.statement = str(statement)
            self.params = params
            return Result()

    session = Session()
    lock_flashscore_source_entities(
        session,
        player_ids={"P2": "Two", "P1": "One"},
        match_ids={"M1", "M2", "M1"},
    )

    assert session.params["lock_keys"] == [
        "flashscore:match:M1", "flashscore:match:M2",
        "flashscore:player:P1", "flashscore:player:P2",
    ]
    assert "pg_advisory_xact_lock" in session.statement


def test_automatic_player_confirmation_accepts_only_simple_full_surname_and_initial():
    assert is_auto_confirmable_player_match("Таггер Л", "Tagger Lilli")
    assert is_auto_confirmable_player_match("Л Таггер", "Lilli Tagger")
    assert is_auto_confirmable_player_match("Нишимото С.", "Saki Nishimoto")
    assert is_auto_confirmable_player_match("Tsitsipas Stefanos", "Tsitsipas Stefanos")

    assert not is_auto_confirmable_player_match("Нишимото К", "Saki Nishimoto")
    assert not is_auto_confirmable_player_match("Альварадо-берроспи А-о", "Alvarado Berrospi Alberto Odiseo")
    assert not is_auto_confirmable_player_match("Паскуаль-ферра Р", "Pascual Ferra Roger")
    assert not is_auto_confirmable_player_match("Силва Л-Ф", "Silva Luiz Felipe")


def test_russian_initials_and_surnames_accept_common_flashscore_transliterations():
    # Exact surname + initial remains the only abbreviated-name auto-approval
    # rule; spelling variants cover common Flashscore transliteration choices.
    for fonbet_name, flashscore_name in [
        ("Нишимото Ч", "Nishimoto Chikako"),
        ("Нишимото Ч", "Nishimoto Catherine"),  # Ч is also rendered as C
        ("Жукова А", "Jukova Anastasia"),
        ("Шкутова А", "Skutova Anna"),
        ("Циципас С", "Cicipas Stefanos"),
        ("Хачанов К", "Hachanov Karen"),
    ]:
        assert is_auto_confirmable_player_match(fonbet_name, flashscore_name), (fonbet_name, flashscore_name)

    assert _player_name_matches("Шкутова А", "Skutova Anna")
    assert _player_name_matches("Жукова А", "Jukova Anastasia")
    assert _player_name_matches("Чайковская В", "Caykovskaya Violetta")
    assert not is_auto_confirmable_player_match("Чайковская В", "Caykovsky Viktor")


def test_yi_is_preserved_during_diacritic_folding_and_c_initials_are_auto_confirmable():
    # NFKD must not collapse й into и; Flashscore varies between i/y/j and
    # uses c for ч in both shortened given names and full surnames.
    for fonbet_name, flashscore_name in [
        ("Чайковская В", "Caykovskaya Violetta"),
        ("Чайковская В", "Chaykovskaya Violetta"),
        ("Нишимото Ч", "Nishimoto Catherine"),
        ("Шкутова А", "Skutova Anna"),
        ("Жукова А", "Jukova Anastasia"),
        ("Циципас С", "Cicipas Stefanos"),
    ]:
        assert is_auto_confirmable_player_match(fonbet_name, flashscore_name), (
            fonbet_name, flashscore_name
        )

    # Similar-looking but different surnames must still stay out of auto-approval.
    assert not is_auto_confirmable_player_match("Чайковская В", "Caykovsky Violetta")


def test_point_history_side_order_is_resolved_for_name_only_archive_matches():
    assert _external_home_is_fonbet_a(
        ("Alcaraz Carlos", "Sinner Jannik"),
        ("Carlos Alcaraz", "Jannik Sinner"),
    ) is True
    assert _external_home_is_fonbet_a(
        ("Alcaraz Carlos", "Sinner Jannik"),
        ("Jannik Sinner", "Carlos Alcaraz"),
    ) is False
    assert _external_home_is_fonbet_a(
        ("Player One", "Player Two"),
        ("Unrelated", "Player Two"),
    ) is None


def test_localized_surname_and_initial_matches_full_source_name():
    assert _player_name_matches("Нишимото С.", "Saki Nishimoto")
    assert _player_name_matches("Маккензи Н", "Naomi Mackenzie")
    assert _player_name_matches("Утидзима Маико", "Maiko Utijima")
    assert not _player_name_matches("Нишимото К", "Saki Nishimoto")


def test_flashscore_search_payload_filters_to_tennis_player_and_builds_profile_url():
    payload = [
        {"id": "QNuG0Gzb", "url": "shelton-ben", "name": "Shelton Ben",
         "type": {"id": 3, "name": "Player"}, "sport": {"id": 2, "name": "Tennis"},
         "gender": {"id": 1, "name": "Men"}},
        {"id": "zBkyuyRI", "url": "benfica", "name": "Benfica",
         "type": {"id": 2, "name": "Team"}, "sport": {"id": 1, "name": "Soccer"}},
        {"id": "S4JXTi90", "url": "benneteau-julien", "name": "Benneteau Julien",
         "type": {"id": 3, "name": "Player"}, "sport": {"id": 2, "name": "Tennis"}},
    ]
    assert _profile_candidates_from_search_payload(payload, "Ben Shelton") == [{
        "name": "Shelton Ben",
        "profile_url": "https://www.flashscore.com/player/shelton-ben/QNuG0Gzb/results/",
        "source_player_id": "QNuG0Gzb",
        "gender": "male",
    }]


def test_profile_search_endpoint_returns_only_tennis_player_candidates(monkeypatch):
    import httpx

    class Response:
        status_code = 200
        content = b"[]"
        def json(self):
            return [
                {"id": "84GyAaXB", "url": "rus-arantxa", "name": "Rus Arantxa",
                 "type": {"id": 3, "name": "Player"}, "sport": {"id": 2, "name": "Tennis"}},
                {"id": "zBkyuyRI", "url": "benfica", "name": "Benfica",
                 "type": {"id": 2, "name": "Team"}, "sport": {"id": 1, "name": "Soccer"}},
            ]

    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, endpoint, params):
            calls.append((endpoint, params))
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    result = _search_flashscore_player_endpoint("Arantxa Rus", ["Arantxa Rus"])
    assert result["status"] == "matched"
    assert result["candidate"]["source_player_id"] == "84GyAaXB"
    assert calls == [("https://s.livesport.services/api/v2/search/", {"q": "Arantxa Rus"})]


def test_profile_search_acquires_rate_limit_slot_for_each_http_request(monkeypatch):
    import httpx
    from contextlib import nullcontext

    class Response:
        status_code = 200
        content = b"[]"
        def json(self): return []

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, endpoint, params): return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    slots = []
    clients = []
    original_client = Client

    def make_client(**kwargs):
        clients.append(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", make_client)
    def acquire():
        slots.append("acquired")
        return nullcontext()

    result = _search_flashscore_player_endpoint(
        "Player Name", ["player name", "name player", "player"], source_access=acquire
    )
    assert result["status"] == "not_found"
    assert len(slots) == 3
    assert len(clients) == 1


@pytest.mark.parametrize("status_code,expected", [(403, "blocked"), (429, "blocked")])
def test_profile_search_endpoint_treats_rate_limits_as_blocked(monkeypatch, status_code, expected):
    import httpx

    class Response:
        content = b"blocked"
        def __init__(self): self.status_code = status_code
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, endpoint, params): return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    result = _search_flashscore_player_endpoint("Player Name", ["Player Name"])
    assert result["status"] == expected


def test_profile_search_endpoint_never_maps_transport_timeout_to_not_found(monkeypatch):
    import httpx

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, endpoint, params): raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "Client", Client)
    result = _search_flashscore_player_endpoint("Player Name", ["Player Name"])
    assert result["status"] == "source_unavailable"
    assert result["reason"] == "SEARCH_HTTP_ERROR"


def test_profile_search_variants_cover_order_and_initial_without_unbounded_queries():
    assert _profile_search_queries("Hamad Medjedovic") == [
        "hamad medjedovic", "medjedovic hamad"
    ]
    assert _profile_search_queries("Меджедович Х") == [
        "medzhedovich kh", "kh medzhedovich", "medjedovic"
    ]
    assert _profile_search_queries("Джонсон София")[2] == "johnson sofia"
    assert len(_profile_search_queries("Player Name Extra Tokens")) <= 3


def test_profile_search_timeout_or_missing_response_is_not_not_found():
    queries = _profile_search_queries("Hamad Medjedovic")
    assert _profile_search_failure_reason(queries, {queries[0]}, set(), set()) == "SEARCH_RESPONSE_MISSING"
    assert _profile_search_failure_reason(queries, set(), {queries[0]}, set()) == "SEARCH_HTTP_ERROR"
    assert _profile_search_failure_reason(queries, set(queries), set(), set()) is None


def test_profile_search_timeout_reason_identifies_browser_stage():
    assert _profile_search_exception_reason("page_open", "TimeoutError") == "PAGE_OPEN_TIMEOUT"
    assert _profile_search_exception_reason("search_response", "TimeoutError") == "SEARCH_RESPONSE_MISSING"
    assert _profile_search_exception_reason("search_button", "TimeoutError") == "SEARCH_UI_MISSING"
    assert _profile_search_exception_reason("search_input", "TimeoutError") == "SEARCH_INPUT_MISSING"
    assert _profile_search_exception_reason("search_results", "TimeoutError") == "SEARCH_INTERACTION_TIMEOUT"


def test_doubles_participant_display_is_excluded_from_player_import_queue():
    assert is_doubles_player_display("Player A / Player B")
    assert not is_doubles_player_display("Player A")


def test_browser_proxy_is_configurable_from_research_environment(monkeypatch):
    monkeypatch.setenv("RESEARCH_HTTP_PROXY", "http://proxy.internal:7890")
    assert _browser_launch_options()["proxy"] == {"server": "http://proxy.internal:7890"}


def test_current_profile_feed_pagination_uses_zero_based_public_endpoint():
    assert _profile_feed_page_url("AZg49Et9", "167", 0) == (
        "https://global.flashscore.ninja/2/x/feed/pr_2_167_AZg49Et9_0_1_en_1"
    )
    with pytest.raises(ValueError):
        _profile_feed_page_url("not-a-player-id", "167", 0)


def test_first_paged_response_can_repeat_the_initial_feed():
    assert _page_has_pagination_progress(0, set())
    assert _page_has_pagination_progress(1, {"new-match"})
    assert not _page_has_pagination_progress(1, set())


def test_profile_queue_timeout_is_not_reported_as_a_provider_network_failure():
    assert _profile_page_failure_reason(
        0, "TimeoutError: FLASHSCORE_SOURCE_LOCK_TIMEOUT"
    ) == "source_queue_timeout"
    assert _profile_page_failure_reason(0, "TimeoutError: connection reset") == "pagination_network_error"


def test_live_pbp_has_bounded_source_queue_wait(monkeypatch):
    monkeypatch.setenv("FLASHSCORE_SOURCE_LOCK_WAIT_SECONDS", "300")
    monkeypatch.delenv("FLASHSCORE_LIVE_SOURCE_LOCK_WAIT_SECONDS", raising=False)
    assert _flashscore_source_wait_seconds("live") == 60
    assert _flashscore_source_wait_seconds("archive") == 300
    monkeypatch.setenv("FLASHSCORE_LIVE_SOURCE_LOCK_WAIT_SECONDS", "45")
    assert _flashscore_source_wait_seconds("live") == 45
    monkeypatch.setenv("FLASHSCORE_SOURCE_LOCK_WAIT_SECONDS", "30")
    assert _flashscore_source_wait_seconds("live") == 30


def test_profile_http_get_returns_source_queue_timeout_without_sending_http(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def source_queue_timeout():
        raise TimeoutError("FLASHSCORE_SOURCE_LOCK_TIMEOUT")
        yield

    monkeypatch.setattr(
        "app.tennis_flashscore._get_profile_page_http_client",
        lambda: pytest.fail("no HTTP request is sent when the source slot was not acquired"),
    )
    status, body, url, error = _profile_http_get(
        "https://www.flashscore.com/player/example/player1/results/",
        source_access=source_queue_timeout,
    )
    assert (status, body) == (0, "")
    assert url.endswith("/results/")
    assert error == "TimeoutError:FLASHSCORE_SOURCE_LOCK_TIMEOUT"


def test_profile_count_compares_advertised_singles_not_all_feed_events():
    singles = {f"singles-{n}" for n in range(45)}
    assert _advertised_singles_count_matches(45, singles)
    assert not _advertised_singles_count_matches(59, singles)
    assert not _advertised_singles_count_matches(None, singles)
    assert _advertised_singles_count_matches(0, set())
    assert _is_doubles_result(
        {"CX": "Player A.", "AF": "Player B."},
        {"ZA": "MIXED DOUBLES: US Open", "ZAF": "Mixed Doubles"},
    )
    assert _is_doubles_result(
        {"CX": "Player A./Player C.", "AF": "Player B./Player D."},
        {"ZA": "US Open", "ZAF": "Mixed Doubles"},
    )
    assert not _is_doubles_result(
        {"CX": "Player A.", "AF": "Player B."},
        {"ZA": "WTA - Singles: US Open", "ZAF": "WTA - Singles"},
    )


def test_server_rendered_profile_feed_and_zero_count_are_read_without_browser():
    html = '''<script>
      cjs.initialFeeds["summary-results_s"] = {
        data: `SA÷2¬~AA÷match001¬PX÷player01¬CA÷39¬PY÷other001¬CB÷51`,
        allEventsCount: 1,
      };
      cjs.initialFeeds["summary-results_m"] = { data: ``, allEventsCount: 0, };
    </script>'''
    feed, count = _profile_initial_feed_from_html(html)
    assert feed == "SA÷2¬~AA÷match001¬PX÷player01¬CA÷39¬PY÷other001¬CB÷51"
    assert count == 1

    zero_html = '''<script>cjs.initialFeeds["summary-results_s"] = {
      data: ``, allEventsCount: 0,
    };</script>'''
    assert _profile_initial_feed_from_html(zero_html) == ("", 0)


@pytest.mark.parametrize(("advertised_count", "expected_complete", "expected_gap"), [(1, True, 0), (2, False, 1)])
def test_http_profile_archive_does_not_start_browser(monkeypatch, advertised_count, expected_complete, expected_gap):
    feed = (
        "SA÷2¬~ZA÷ATP MEN'S SINGLES: Rome, clay¬ZAF÷ATP Men Singles¬ZK÷Rome"
        "¬ZL÷/tennis/atp-singles/rome/¬~AA÷match001¬AD÷1780000000"
        "¬CX÷Player One¬PX÷player01¬CA÷39¬AF÷Player Two¬PY÷other001¬CB÷51"
        "¬AG÷2¬AH÷0¬BA÷6¬BB÷4¬BC÷7¬BD÷6"
    )
    profile_html = (
        '<script>cjs.initialFeeds["summary-results_s"] = {'
        f'data: `{feed}`, allEventsCount: {advertised_count},}};</script>'
    )
    requested = []

    def fake_get(url, _source_access=None, *, feed=False):
        requested.append(url)
        if not feed:
            return 200, profile_html, url, None
        if "_0_1_en_1" in url:
            return 200, feed_payload, url, None
        return 200, "", url, None

    feed_payload = feed
    monkeypatch.setattr("app.tennis_flashscore._profile_http_get", fake_get)
    importer = FlashscoreTennisImporter(storage=SimpleNamespace())
    monkeypatch.setattr(
        importer, "_fetch_rendered_profile_with_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Chromium should not start")),
    )

    html, status, payloads, _name, complete, documents = importer._fetch_rendered_profile(
        "https://www.flashscore.com/player/player-one/player01/results/"
    )
    assert status == "OK"
    assert html == profile_html
    assert complete is expected_complete
    assert len(payloads) == 2
    assert documents[-1]["pagination_reason"] == (
        "source_terminal_empty_response_confirmed" if expected_complete else
        "source_terminal_empty_response_count_mismatch_confirmed"
    )
    assert documents[-1]["fetch_transport"] == "http"
    assert documents[-1]["count_gap"] == expected_gap
    assert len(requested) == 4  # profile, page zero, and two terminal probes


def test_profile_http_block_does_not_trigger_chromium_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.tennis_flashscore._profile_http_get",
        lambda *args, **kwargs: (429, "", args[0], None),
    )
    importer = FlashscoreTennisImporter(storage=SimpleNamespace())
    monkeypatch.setattr(
        importer, "_fetch_rendered_profile_with_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Do not retry a source block in Chromium")),
    )
    result = importer._fetch_rendered_profile(
        "https://www.flashscore.com/player/player-one/player01/results/"
    )
    assert result[1] == "HTTP_429_ANTI_BOT_BLOCKED"
    assert result[-1][0]["fetch_transport"] == "http"
    assert result[-1][0]["fallback_reason"] is None


def test_http_captcha_diagnostic_records_transport_without_browser_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.tennis_flashscore._profile_http_get",
        lambda *args, **kwargs: (200, "<html>verify you are human</html>", args[0], None),
    )
    importer = FlashscoreTennisImporter(storage=SimpleNamespace())
    monkeypatch.setattr(
        importer, "_fetch_rendered_profile_with_browser",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Do not retry a CAPTCHA in Chromium")),
    )
    result = importer._fetch_rendered_profile(
        "https://www.flashscore.com/player/player-one/player01/results/"
    )
    assert result[1] == "CAPTCHA_CHALLENGE_ABORTED"
    assert result[-1][0]["fetch_transport"] == "http"
    assert result[-1][0]["fallback_reason"] == "HTTP_200_ANTIBOT_CHALLENGE"


def test_profile_http_get_handles_httpx_response_without_permanent_redirect_property(monkeypatch):
    class Response:
        status_code = 200
        headers = {}
        content = b"ok"
        text = "ok"
        url = "https://www.flashscore.com/player/player-one/player01/results/"
        is_redirect = False

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.tennis_flashscore._get_profile_page_http_client", Client)
    monkeypatch.setattr("app.tennis_flashscore.time.sleep", lambda *_args: None)

    from app.tennis_flashscore import _profile_http_get

    assert _profile_http_get(Response.url) == (200, "ok", Response.url, None)


def test_profile_http_shell_uses_one_browser_fallback_and_records_reason(monkeypatch):
    monkeypatch.setattr(
        "app.tennis_flashscore._profile_http_get",
        lambda *args, **kwargs: (200, "<html><body>shell</body></html>", args[0], None),
    )
    importer = FlashscoreTennisImporter(storage=SimpleNamespace())
    callback_documents = []

    def fallback(*args, page_callback=None, **kwargs):
        page_callback({"page_index": 1, "html": "feed", "response_status": 200})
        return "rendered", "OK", ["feed"], "Player One", False, [{"page_index": 1}]

    monkeypatch.setattr(importer, "_fetch_rendered_profile_with_browser", fallback)
    result = importer._fetch_rendered_profile(
        "https://www.flashscore.com/player/player-one/player01/results/",
        page_callback=callback_documents.append,
    )
    assert result[0] == "rendered"
    assert callback_documents[0]["fetch_transport"] == "chromium_fallback"
    assert callback_documents[0]["fallback_reason"] == "HTTP_200_INITIAL_FEED_MISSING"
    assert result[-1][0]["fetch_transport"] == "chromium_fallback"


def test_profile_parser_accepts_prior_version_archive_documents_for_reparse():
    class Storage:
        def read_document(self, _path):
            return {
                "parser_version": "flashscore-tennis-player-v6",
                "page_index": 1,
                "url": "https://www.flashscore.ninja/x/pr_2_1_jNyZsXZe_0_1_en_1",
                "html": "payload",
                "content_hash": "sha256:" + __import__("hashlib").sha256(b"payload").hexdigest(),
                "response_status": 200,
                "match_ids": ["abc"],
            }

    importer = FlashscoreTennisImporter(storage=Storage())
    page = {
        "page_index": 1,
        "archived_path": "snapshots/research/2026-10-20/jNyZsXZe/page.json",
        "parser_version": "flashscore-tennis-player-v6",
        "content_hash": "sha256:" + __import__("hashlib").sha256(b"payload").hexdigest(),
    }
    assert importer._load_resume_documents("jNyZsXZe", [page])
    assert is_compatible_profile_parser_version("flashscore-tennis-player-v8")
    assert not is_compatible_profile_parser_version("unrelated-parser-v8")


def test_atp_wta_categories_determine_gender_and_major_match_format():
    assert _category(
        "ATP - Singles: Australian Open (Australia), hard",
        "ATP - Singles",
        "/tennis/atp-singles/australian-open/",
    ) == ("male", "atp_wta", 5)
    assert _category(
        "WTA - Singles: Roland Garros (France), clay",
        "WTA - Singles",
        "/tennis/wta-singles/roland-garros/",
    ) == ("female", "atp_wta", 3)
    two_sets = {
        "AA": "slam-two-sets", "AD": "1780000000", "CX": "Player One", "AF": "Player Two",
        "PX": "one", "PY": "two", "BA": "6", "BB": "4", "BC": "6", "BD": "4",
    }
    context = {"ZA": "ATP - Singles: Australian Open (Australia), hard", "ZAF": "ATP - Singles"}
    parsed, reason = _parse_match_with_reason(two_sets, context)
    assert parsed is None and reason == "score_does_not_prove_match_winner"
    three_sets = {**two_sets, "BE": "6", "BF": "4"}
    parsed, reason = _parse_match_with_reason(three_sets, context)
    assert reason is None and parsed is not None and parsed.best_of == 5


def test_profile_country_id_is_inferred_from_player_side_not_opponent():
    feed = (
        "~AA÷match-home¬PX÷AZg49Et9¬CA÷167¬PY÷Other123¬CB÷154¬"
        "~AA÷match-away¬PX÷Other456¬CA÷154¬PY÷AZg49Et9¬CB÷167¬"
    )
    assert _profile_country_id(feed, "AZg49Et9") == "167"
    assert _profile_country_id(feed, "Missing1") is None


def test_profile_resume_requires_a_verified_contiguous_archived_prefix():
    player_id = "AZg49Et9"
    documents = {}
    checkpoints = []
    for index in (1, 2):
        body = f"~AA÷match-{index}¬AD÷1780000000¬PX÷{player_id}¬CA÷167¬"
        digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
        path = f"snapshots/research/2026-10-20/{player_id}/{index}.json"
        documents[path] = {
            "parser_version": PARSER_VERSION, "page_index": index,
            "url": _profile_feed_page_url(player_id, "167", index - 1),
            "html": body, "content_hash": digest, "response_status": 200,
            "match_ids": [f"match-{index}"],
        }
        checkpoints.append({"page_index": index, "archived_path": path,
                            "parser_version": PARSER_VERSION, "content_hash": digest})

    class Storage:
        def read_document(self, path):
            return documents.get(path)

    importer = FlashscoreTennisImporter(storage=Storage())
    assert len(importer._load_resume_documents(player_id, checkpoints)) == 2
    assert importer._load_resume_documents(player_id, checkpoints[1:]) == []
    assert importer._load_resume_documents(player_id, [checkpoints[0], checkpoints[0]]) == []
    assert importer._load_resume_documents(player_id, [{**checkpoints[0], "parser_version": "v3"}]) == []
    documents[checkpoints[1]["archived_path"]]["html"] = "corrected after archive"
    assert importer._load_resume_documents(player_id, checkpoints) == []


def test_profile_resume_does_not_trust_a_single_empty_terminal_response():
    player_id = "AZg49Et9"
    body = "0"
    digest = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
    path = f"snapshots/research/2026-10-20/{player_id}/terminal.json"
    document = {
        "parser_version": PARSER_VERSION,
        "page_index": 1,
        "url": _profile_feed_page_url(player_id, "167", 0),
        "html": body,
        "content_hash": digest,
        "response_status": 200,
        "match_ids": [],
        "pagination_reason": "source_terminal_empty_unconfirmed",
    }
    checkpoint = {"page_index": 1, "archived_path": path,
                  "parser_version": PARSER_VERSION, "content_hash": digest}

    class Storage:
        def read_document(self, requested_path):
            return document if requested_path == path else None

    importer = FlashscoreTennisImporter(storage=Storage())
    assert importer._load_resume_documents(player_id, [checkpoint]) == []
    document["pagination_reason"] = "source_terminal_empty_response_confirmed"
    assert len(importer._load_resume_documents(player_id, [checkpoint])) == 1


def _document(feed: str) -> str:
    return f'''<html><script>window.teamPageEnvironment = {{"participantName":"Player One"}};</script>
    <script>cjs.initialFeeds["results_s"] = {{ data: `{feed}`, change: null }};</script></html>'''


def test_parse_profile_results_and_tournament_context():
    feed = (
        "SA÷2¬~ZA÷ATP MEN'S SINGLES: Rome, clay¬ZAF÷ATP Men Singles"
        "¬ZK÷Rome (Italy)¬ZL÷/tennis/atp-singles/rome/"
        "¬~AA÷match-1¬AD÷1780000000¬AB÷3¬CX÷Player One¬PX÷player-one"
        "¬AF÷Player Two¬PY÷player-two¬AG÷2¬AH÷0"
        "¬BA÷6¬BB÷4¬BC÷7¬BD÷6¬BE÷0¬BF÷0¬AW÷1"
    )
    profile = parse_player_profile(
        _document(feed),
        "https://www.flashscore.com.ua/player/player-one/playeroneid/results/",
    )

    assert profile.source_player_id == "playeroneid"
    assert profile.full_name == "Player One"
    assert profile.feed_count == 1
    assert len(profile.matches) == 1
    match = profile.matches[0]
    assert match.source_match_id == "match-1"
    assert match.played_at.tzinfo == timezone.utc
    assert match.home_player_id == "player-one"
    assert match.away_player_id == "player-two"
    assert match.winner_name == "Player One"
    assert match.score == "6-4 7-6"
    assert match.gender == "male"
    assert match.tour_level == "atp_wta"
    assert match.surface == "clay"
    assert match.tournament == "Rome (Italy)"


def test_incomplete_and_walkover_rows_are_not_outcomes():
    feed = (
        "SA÷2¬~ZA÷ATP MEN'S SINGLES: Rome, hard¬ZAF÷ATP Men Singles"
        "¬~AA÷incomplete¬AD÷1780000000¬AB÷3¬CX÷Player One¬PX÷one"
        "¬AF÷Player Two¬PY÷two¬BA÷6¬BB÷4"
        "¬~AA÷walkover¬AD÷1780000100¬AB÷3¬CX÷Player One¬PX÷one"
        "¬AF÷Player Two¬PY÷two¬WL÷Walkover¬BA÷6¬BB÷0¬BC÷6¬BD÷0"
    )
    profile = parse_player_profile(
        _document(feed),
        "https://www.flashscore.com/player/player-one/playeroneid/results/",
    )
    assert profile.feed_count == 2
    assert profile.matches == ()
    assert profile.skipped_rows == 2


def test_doubles_profile_rows_are_archived_but_excluded_from_singles_results():
    feed = (
        "SA÷2¬~ZA÷MIXED DOUBLES: US Open (USA), hard¬ZAF÷Mixed Doubles"
        "¬ZK÷US Open (USA)¬ZL÷/tennis/mixed-doubles/us-open/"
        "¬~AA÷mixed-1¬AD÷1780000000¬AB÷3¬CX÷Player One/Player Three¬PX÷one/three"
        "¬AF÷Player Two/Player Four¬PY÷two/four¬BA÷6¬BB÷2¬BC÷6¬BD÷3"
    )
    profile = parse_player_profile(
        _document(feed),
        "https://www.flashscore.com/player/player-one/playeroneid/results/",
    )
    assert profile.feed_count == 1
    assert profile.matches == ()
    assert profile.skipped_row_reasons == {"doubles_excluded_from_singles": 1}


def test_rejects_non_profile_url_and_missing_feed():
    with pytest.raises(ValueError, match="player profile"):
        parse_player_profile("<html></html>", "https://www.flashscore.com/tennis/")
    with pytest.raises(ValueError, match="results_s feed"):
        parse_player_profile(
            "<html></html>",
            "https://www.flashscore.com/player/player-one/playeroneid/results/",
        )


def test_scraper_archives_source_and_reports_unconfirmed_pagination():
    feed = (
        "SA÷2¬~ZA÷ATP MEN'S SINGLES: Rome, clay¬ZAF÷ATP Men Singles"
        "¬ZK÷Rome¬~AA÷match-1¬AD÷1780000000¬AB÷3¬CX÷Player One¬PX÷one"
        "¬AF÷Player Two¬PY÷two¬AG÷2¬AH÷0¬BA÷6¬BB÷4¬BC÷6¬BD÷3"
    )
    html = _document(feed)

    class Fetcher:
        def fetch(self, url):
            raise AssertionError("profile import must use the single Chromium request path")

    class Storage:
        documents = []

        def archive_document(self, player_id, content_hash, document):
            self.documents.append((player_id, content_hash, document))
            return "snapshots/research/test.json"

    storage = Storage()
    importer = FlashscoreTennisImporter(fetcher=Fetcher(), storage=storage)
    importer._fetch_rendered_profile = lambda url, stop_before_date=None, **kwargs: (
        html, "OK", [feed], "Player One", False,
        [{"page_index": 1, "html": html, "result_feeds": [feed]}],
    )
    started_at = datetime.now(timezone.utc)
    result = importer.scrape_profile(
        "https://www.flashscore.com/player/player-one/playeroneid/results/"
    )

    assert result.status == "partial"
    assert result.reason == "PAGINATION_NOT_CONFIRMED"
    assert result.retrieved_at >= started_at
    assert result.profile is not None
    assert result.profile.pagination_complete is False
    assert result.archived_path == "snapshots/research/test.json"
    assert storage.documents[0][0] == "playeroneid"
    assert storage.documents[0][2]["parser_version"]
    assert storage.documents[1][2]["page_index"] == 1
    assert result.archived_page_paths == ("snapshots/research/test.json",)


def test_observation_hash_is_stable_but_changes_when_result_is_corrected():
    feed = (
        "SA÷2¬~ZA÷ATP MEN'S SINGLES: Rome, clay¬ZAF÷ATP Men Singles"
        "¬ZK÷Rome¬~AA÷match-1¬AD÷1780000000¬AB÷3¬CX÷Player One¬PX÷one"
        "¬AF÷Player Two¬PY÷two¬AG÷2¬AH÷0¬BA÷6¬BB÷4¬BC÷6¬BD÷3"
    )
    url = "https://www.flashscore.com/player/player-one/playeroneid/results/"
    original = parse_player_profile(_document(feed), url).matches[0]
    corrected_feed = feed.replace("BC÷6", "BC÷7").replace("BD÷3", "BD÷5")
    corrected = parse_player_profile(_document(corrected_feed), url).matches[0]

    original_payload = _observation_payload(original)
    assert _observation_hash(original_payload) == _observation_hash(dict(original_payload))
    assert _observation_hash(original_payload) != _observation_hash(_observation_payload(corrected))


def test_canonical_result_identity_is_stable_when_score_is_corrected():
    original = SimpleNamespace(
        source_match_id="flashscore-match-1", played_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        home_name="Player One", away_name="Player Two", score="6-4 6-3",
        tournament="Rome", round_name="Quarterfinal", gender="male",
    )
    corrected = SimpleNamespace(**{**original.__dict__, "score": "6-4 7-5"})
    assert _canonical_match_key(original) == _canonical_match_key(corrected)


def test_adjacent_day_qualification_does_not_merge_with_main_draw_match():
    archive_match = SimpleNamespace(
        played_at=datetime(2019, 5, 20, tzinfo=timezone.utc), gender="male",
        tournament="Lyon Open", canonical_key="main-draw-key",
    )
    qualifier = SimpleNamespace(
        played_at=datetime(2019, 5, 19, 8, 10, tzinfo=timezone.utc), gender="male",
        tournament="Lyon 2 (France)",
    )
    main_draw = SimpleNamespace(**{**qualifier.__dict__, "played_at": datetime(2019, 5, 20, 11, 15, tzinfo=timezone.utc)})
    assert _matching_canonical_match_key(qualifier, [archive_match]) is None
    assert _matching_canonical_match_key(main_draw, [archive_match]) == "main-draw-key"


def test_result_corrections_preserve_a_b_a_sequence_but_suppress_adjacent_repeat():
    first = SimpleNamespace(result_hash="A")
    corrected = SimpleNamespace(result_hash="B")
    assert _is_duplicate_adjacent_fact(first, "A")
    assert not _is_duplicate_adjacent_fact(first, "B")
    assert not _is_duplicate_adjacent_fact(corrected, "A")
    assert _is_duplicate_adjacent_fact(SimpleNamespace(result_hash="A"), "A")


def test_point_history_uses_source_side_order_from_imported_observation():
    match = SimpleNamespace(source_match_id="match-123", tournament="Wimbledon")
    state = _external_match_state(match, {
        "home_name": "Player A", "home_player_id": "player-a",
        "away_name": "Player B", "away_player_id": "player-b",
    })
    assert state.home_name == "Player A"
    assert state.away_name == "Player B"
    assert state.tournament == "Wimbledon"
    assert _match_page_url(state) == (
        "https://www.flashscore.com/match/tennis/player-a-player-a/player-b-player-b/summary/?mid=match-123"
    )


def test_point_history_uses_the_imported_flashscore_locale_host():
    match = SimpleNamespace(source_match_id="match-123", tournament="Rome")
    state = _external_match_state(match, {
        "home_name": "Player A", "home_player_id": "player-a",
        "away_name": "Player B", "away_player_id": "player-b",
        "source_url": "https://www.flashscore.com.ua/player/player-a/sourceid/results/",
    })
    assert _match_page_url(state) == (
        "https://www.flashscore.com.ua/match/tennis/player-a-player-a/player-b-player-b/summary/?mid=match-123"
    )


def test_point_history_falls_back_to_the_imported_match_source_host():
    match = SimpleNamespace(
        source_match_id="match-123", tournament="Rome",
        stats={"source_url": "https://www.flashscore.com.ua/player/player-a/sourceid/results/"},
    )
    state = _external_match_state(match, {
        "home_name": "Player A", "home_player_id": "player-a",
        "away_name": "Player B", "away_player_id": "player-b",
    })
    assert "https://www.flashscore.com.ua/match/" in _match_page_url(state)


def test_profile_html_preserves_exact_match_url_for_pbp():
    document = '''<a href="/match/tennis/antonius-michael-nB7oqApL/pedrico-kravtsov-albert-SC9if6GJ/?mid=fVArlUH0&amp;foo=bar">Match</a>
      <a href="https://example.invalid/match/tennis/a/b/?mid=bad">not Flashscore</a>
      <a href="/match/football/a/b/?mid=wrong">not tennis</a>'''
    urls = _match_urls_from_profile_html(
        document, "https://www.flashscore.com.ua/player/antonius-michael/nB7oqApL/results/"
    )
    assert urls == {
        "fVArlUH0": "https://www.flashscore.com.ua/match/tennis/antonius-michael-nB7oqApL/pedrico-kravtsov-albert-SC9if6GJ/?mid=fVArlUH0&foo=bar"
    }


def test_imported_exact_source_url_is_used_for_pbp_page():
    source_url = (
        "https://www.flashscore.com.ua/match/tennis/antonius-michael-nB7oqApL/"
        "pedrico-kravtsov-albert-SC9if6GJ/?mid=fVArlUH0"
    )
    match = SimpleNamespace(source_match_id="fVArlUH0", tournament="ITF M15")
    state = _external_match_state(match, {
        "home_name": "Antonius M.", "home_player_id": "nB7oqApL",
        "away_name": "Pedrico-Kravtsov A.", "away_player_id": "SC9if6GJ",
        "source_url": source_url,
    })
    assert _match_page_url(state) == source_url


def test_confirmed_live_link_can_drive_pbp_before_profile_match_import():
    link = SimpleNamespace(
        source_match_id="vV6BKNOq",
        evidence_json={
            "source_url": "https://www.flashscore.com/match/tennis/masabayashi-tomohiro-23AG6J1b/"
            "suksumrarn-thantub-tW8olIAn/?mid=vV6BKNOq/summary/?mid=vV6BKNOq",
        },
    )

    resolved = _confirmed_link_page(link, "tW8olIAn", "23AG6J1b")

    assert resolved == (
        "https://www.flashscore.com/match/tennis/masabayashi-tomohiro-23AG6J1b/"
        "suksumrarn-thantub-tW8olIAn/summary/?mid=vV6BKNOq",
        None,
    )


def test_confirmed_link_does_not_treat_player_slug_order_as_side_evidence():
    link = SimpleNamespace(
        source_match_id="nFpUNjLN",
        evidence_json={
            "source_url": "https://www.flashscore.com/match/tennis/chaudhari-vaidehi-zT0yq0KG/"
            "nishimoto-sera-Sz5NW6cC/summary/point-by-point/set-1/?mid=nFpUNjLN",
        },
    )

    resolved = _confirmed_link_page(link, "Sz5NW6cC", "zT0yq0KG")

    assert resolved == (
        "https://www.flashscore.com/match/tennis/chaudhari-vaidehi-zT0yq0KG/"
        "nishimoto-sera-Sz5NW6cC/summary/?mid=nFpUNjLN",
        None,
    )


def test_confirmed_link_preserves_explicit_manual_side_orientation():
    link = SimpleNamespace(
        source_match_id="match-1",
        evidence_json={
            "resolution": "manual_confirmation",
            "external_home_is_fonbet_a": True,
            "source_url": "https://www.flashscore.com/match/tennis/player-b-b/player-a-a/?mid=match-1",
        },
    )

    assert _confirmed_link_page(link, "player-a", "player-b")[1] is True


def test_source_dom_side_orientation_uses_home_away_profile_ids_not_url_slug_order():
    from app.point_history import _source_dom_side_orientation

    source_html = """
    <div class="duelParticipant__home"><a href="/player/nishimoto-sera/Sz5NW6cC/">Nishimoto S.</a></div>
    <div class="duelParticipant__away"><a href="/player/chaudhari-vaidehi/zT0yq0KG/">Chaudhari V.</a></div>
    """

    assert _source_dom_side_orientation(source_html, "Sz5NW6cC", "zT0yq0KG") is True
    assert _source_dom_side_orientation(source_html, "zT0yq0KG", "Sz5NW6cC") is False
    assert _source_dom_side_orientation(source_html, "unconfirmed-a", "unconfirmed-b") is None


def test_confirmed_live_link_rejects_untrusted_or_wrong_player_urls():
    foreign = SimpleNamespace(source_match_id="match-123", evidence_json={
        "source_url": "https://example.invalid/match/tennis/player-a/player-b/?mid=match-123",
    })
    wrong_players = SimpleNamespace(source_match_id="match-123", evidence_json={
        "source_url": "https://www.flashscore.com/match/tennis/other-a/other-b/?mid=match-123",
    })

    assert _confirmed_link_page(foreign, "player-a", "player-b") is None
    assert _confirmed_link_page(wrong_players, "player-a", "player-b") is None


def test_direct_pbp_navigation_timeout_falls_back_to_confirmed_summary_url():
    class Response:
        status = 200

    class Page:
        def __init__(self):
            self.urls = []

        def goto(self, url, **kwargs):
            self.urls.append(url)
            if len(self.urls) == 1:
                raise TimeoutError("direct PBP route timed out")
            return Response()

    page = Page()
    summary = "https://www.flashscore.com/match/tennis/player-a/player-b/summary/?mid=match123"

    response, error = _open_point_history_entry(page, summary)

    assert response.status == 200
    assert error is None
    assert page.urls == [
        "https://www.flashscore.com/match/tennis/player-a/player-b/summary/point-by-point/?mid=match123",
        summary,
    ]


def test_finished_match_does_not_refresh_a_fresh_player_profile_per_event():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(
        source_identities={"flashscore": "player-id"},
        last_success_at=datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc),
    )
    previous_job = SimpleNamespace(
        status="completed", updated_at=datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc),
    )

    assert not _post_match_profile_refresh_due(profile, previous_job, finished_at, now)


def test_player_profile_refresh_becomes_due_after_seven_days():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(
        source_identities={"flashscore": "player-id"},
        last_success_at=now - timedelta(days=7, seconds=1),
    )
    completed_job = SimpleNamespace(status="completed", updated_at=now - timedelta(days=7))

    assert _post_match_profile_refresh_due(profile, completed_job, None, now)


def test_post_finish_profile_refresh_is_not_requeued_for_same_result():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    finished_at = datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(
        source_identities={"flashscore": "player-id"},
        last_success_at=datetime(2026, 10, 21, 11, 30, tzinfo=timezone.utc),
    )
    completed_job = SimpleNamespace(
        status="completed", updated_at=datetime(2026, 10, 21, 11, 35, tzinfo=timezone.utc),
    )

    assert not _post_match_profile_refresh_due(profile, completed_job, finished_at, now)


def test_point_by_point_url_removes_redirected_query_before_appending_tab():
    assert _point_by_point_set_url(
        "https://www.flashscore.com/match/tennis/player-a/player-b/?mid=match-123", "match-123", 1
    ) == "https://www.flashscore.com/match/tennis/player-a/player-b/summary/point-by-point/set-1/?mid=match-123"
    assert _point_by_point_set_url(
        "https://www.flashscore.com/match/tennis/player-a/player-b/summary/?mid=match-123", "match-123", 2
    ) == "https://www.flashscore.com/match/tennis/player-a/player-b/summary/point-by-point/set-2/?mid=match-123"
    assert _point_by_point_index_url(
        "https://www.flashscore.com.ua/match/tennis/player-a/player-b/?mid=match-123", "match-123"
    ) == "https://www.flashscore.com.ua/match/tennis/player-a/player-b/summary/point-by-point/?mid=match-123"


def test_point_history_feed_url_uses_compact_tennis_http_feed():
    assert _point_history_feed_url("A5F0BCMP") == (
        "https://global.flashscore.ninja/2/x/feed/df_mh_2_A5F0BCMP"
    )
    with pytest.raises(ValueError):
        _point_history_feed_url("../../untrusted")


def test_point_history_feed_parser_retains_points_games_and_server():
    feed = (
        "HA÷Set 1¬HB÷Point by point - Set 1"
        "~HC÷0¬HE÷1¬HG÷2¬HK÷2¬HL÷0:15, 15:15, 15:30, 15:40, 30:40"
        "~HC÷1¬HE÷1¬HG÷1¬HK÷1¬HL÷15:0, 15:15, 30:15, 40:15"
    )

    parsed = _parse_point_history_feed(feed)

    assert parsed["sets"] == [1]
    assert parsed["quality"]["complete"] is True
    assert parsed["quality"]["confirmed_points"] == 11
    assert parsed["events"][0]["winner"] == 1
    assert parsed["events"][0]["server"] == 1
    assert parsed["events"][-1]["quality"] == "terminal_from_game_result"
    assert parsed["events"][-1]["winner"] == 0


def test_point_history_feed_parser_retains_tiebreak_sequence_and_set_result():
    feed = (
        "HA÷Set 1¬HB÷Point by point - Set 1"
        "~HC÷6¬HE÷6¬HG÷2¬HK÷1¬HL÷1:0, 2:0, 2:1, 3:1, 4:1, 5:1, 6:1, 7:1"
    )

    parsed = _parse_point_history_feed(feed)

    assert parsed["quality"]["complete"] is True
    assert parsed["events"][-1]["quality"] == "terminal_from_tiebreak_result"
    assert parsed["events"][-1]["state_after"] == {"set_result": [7, 6]}


def test_normal_deuce_game_reaching_six_all_is_not_a_tiebreak():
    feed = ("HA÷Set 1~HC÷6¬HE÷6¬HG÷1¬HK÷1"
            "¬HL÷15:0, 15:15, 30:15, 30:30, 40:30, 40:40, A:40")
    parsed = _parse_point_history_feed(feed)
    assert not any(e.get("tie_break") for e in parsed["events"])
    assert parsed["events"][-1]["state_after"] == ["AD", "40"]


def test_separate_tiebreak_rows_have_contiguous_indices_and_one_terminal():
    feed = "HA÷Set 1~HC÷7¬HE÷6¬HD÷7¬HF÷0¬HK÷1~HB÷Tiebreak - Set 1"
    feed += "".join(f"~HC÷{point}¬HE÷0¬HG÷1¬HK÷1" for point in range(1, 8))
    parsed = _parse_point_history_feed(feed)
    points = [e for e in parsed["events"] if e.get("point_score") is not None]
    assert [e["point_index"] for e in points] == list(range(7))
    assert [e["winner"] for e in points] == [0] * 7
    terminals = [e for e in parsed["events"] if e.get("quality") == "terminal_from_tiebreak_result"]
    assert len(terminals) == 1
    assert terminals[0]["state_after"] == {"set_result": [7, 6]}


def test_real_three_set_http_feed_keeps_deuce_and_both_tiebreaks():
    from pathlib import Path
    feed = (Path(__file__).parent / "fixtures/flashscore_point_feed_A5F0BCMP.txt").read_text()
    parsed = _parse_point_history_feed(feed)
    assert parsed["sets"] == [1, 2, 3]
    assert parsed["quality"]["gaps"] == []
    assert parsed["quality"]["confirmed_points"] == 249
    tiebreak_points = [e for e in parsed["events"] if e.get("tie_break") and e.get("point_score") is not None]
    assert len(tiebreak_points) == 21  # 7:3 and 7:4
    assert parsed["events"][-1]["state_after"] == {"set_result": [7, 6]}


def test_point_history_fetch_uses_http_feed_and_returns_cumulative_payload(monkeypatch):
    from app import tennis_flashscore
    feed = "A1÷signature-1¬HA÷Set 1¬HB÷Point by point - Set 1~HC÷0¬HE÷1¬HG÷2¬HK÷2¬HL÷0:15, 15:15, 15:30, 15:40, 30:40"
    calls = []

    def http_get(url, source_access=None, *, feed=False):
        calls.append((url, feed))
        return 200, feed_payload, url, None

    feed_payload = feed
    monkeypatch.setattr(tennis_flashscore, "_profile_http_get", http_get)

    docs, status = fetch_point_documents(
        "https://www.flashscore.com/match/tennis/a/b/summary/?mid=A5F0BCMP"
    )

    assert status == "complete"
    assert calls == [(_point_history_feed_url("A5F0BCMP"), True)]
    assert docs[0]["fetch_transport"] == "http_feed"
    assert docs[0]["parsed"]["quality"]["feed_signature"] == "signature-1"
    assert docs[0]["parsed"]["events"]


def test_live_point_history_waits_for_first_http_points_without_opening_browser(monkeypatch):
    from app import tennis_flashscore
    from app.browser_pool import flashscore_chromium
    calls = []

    def http_get(url, source_access=None, *, feed=False):
        calls.append((url, feed))
        return 200, "A1÷signature-1¬HA÷Set 1", url, None

    monkeypatch.setattr(tennis_flashscore, "_profile_http_get", http_get)
    monkeypatch.setattr(
        flashscore_chromium,
        "context",
        lambda *_args, **_kwargs: pytest.fail("live empty feed must not open a browser"),
    )

    docs, status = fetch_point_documents(
        "https://www.flashscore.com/match/tennis/a/b/summary/?mid=A5F0BCMP",
        skip_browser_when_feed_empty=True,
    )

    assert docs == []
    assert status == "HTTP_200_NO_PUBLISHED_HISTORY"
    assert calls == [(_point_history_feed_url("A5F0BCMP"), True)]


def test_point_history_http_throttle_does_not_fall_back_to_browser(monkeypatch):
    from app import main, tennis_flashscore
    throttles = []
    monkeypatch.setattr(tennis_flashscore, "_profile_http_get", lambda *_args, **_kwargs: (429, "", "", None))
    monkeypatch.setattr(main, "_record_flashscore_throttle", lambda: throttles.append(True))

    docs, status = fetch_point_documents(
        "https://www.flashscore.com/match/tennis/a/b/summary/?mid=A5F0BCMP"
    )

    assert docs == []
    assert status == "HTTP_429_BLOCKED"
    assert throttles == [True]


def test_point_history_opens_direct_index_when_confirmed_source_match_id_exists():
    summary = "https://www.flashscore.com/match/tennis/player-a/player-b/summary/?mid=match123"
    assert _preferred_point_history_url(summary) == (
        "https://www.flashscore.com/match/tennis/player-a/player-b/summary/point-by-point/?mid=match123"
    )


def test_point_history_keeps_legacy_summary_url_without_match_id():
    summary = "https://www.flashscore.com/match/tennis/player-a/player-b/summary/"
    assert _preferred_point_history_url(summary) == summary


def test_point_by_point_set_tabs_keep_only_the_same_source_match():
    links = [
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-1/?mid=m1",
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-2/?mid=m1",
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-3/?mid=other",
        "https://example.invalid/match/tennis/a/b/summary/point-by-point/set-4/?mid=m1",
    ]
    assert _point_by_point_set_tabs(links, "m1") == {1: links[0], 2: links[1]}
