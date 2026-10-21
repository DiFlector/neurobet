from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib

import pytest

from app.tennis_flashscore import (
    FlashscoreTennisImporter,
    _player_name_matches,
    is_auto_confirmable_player_match,
    is_exact_player_name_match,
    _profile_candidates_from_search_payload,
    _profile_search_queries,
    _profile_search_failure_reason,
    _profile_search_exception_reason,
    _observation_hash,
    _observation_payload,
    _browser_launch_options,
    _profile_country_id,
    _profile_feed_page_url,
    _is_doubles_result,
    is_doubles_player_display,
    _advertised_singles_count_matches,
    _category,
    _parse_match_with_reason,
    _match_urls_from_profile_html,
    _page_has_pagination_progress,
    _profile_page_failure_reason,
    PARSER_VERSION,
    parse_player_profile,
)
from app.player_profiles import _canonical_match_key, _is_duplicate_adjacent_fact, _matching_canonical_match_key
from app.point_history import (
    _external_home_is_fonbet_a, _external_match_state, _match_page_url, _point_by_point_index_url,
    _point_by_point_set_tabs, _point_by_point_set_url,
)


def test_exact_player_confirmation_requires_full_ordered_name_match():
    assert is_exact_player_name_match("Tsitsipas Stefanos", "Tsitsipas Stefanos")
    assert is_exact_player_name_match("Tsítsipas Stefanos", "Tsitsipas Stefanos")
    assert is_exact_player_name_match("TSITSIpas   Stefanos", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Tsitsipas S", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Stefanos Tsitsipas", "Tsitsipas Stefanos")
    assert not is_exact_player_name_match("Tsitsipas Stefanos", "Tsitsipas Stefanos Jr")


def test_automatic_player_confirmation_accepts_only_simple_full_surname_and_initial():
    assert is_auto_confirmable_player_match("Таггер Л", "Tagger Lilli")
    assert is_auto_confirmable_player_match("Л Таггер", "Lilli Tagger")
    assert is_auto_confirmable_player_match("Нишимото С.", "Saki Nishimoto")
    assert is_auto_confirmable_player_match("Tsitsipas Stefanos", "Tsitsipas Stefanos")

    assert not is_auto_confirmable_player_match("Нишимото К", "Saki Nishimoto")
    assert not is_auto_confirmable_player_match("Альварадо-берроспи А-о", "Alvarado Berrospi Alberto Odiseo")
    assert not is_auto_confirmable_player_match("Паскуаль-ферра Р", "Pascual Ferra Roger")
    assert not is_auto_confirmable_player_match("Силва Л-Ф", "Silva Luiz Felipe")


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


def test_profile_count_compares_advertised_singles_not_all_feed_events():
    singles = {f"singles-{n}" for n in range(45)}
    assert _advertised_singles_count_matches(45, singles)
    assert not _advertised_singles_count_matches(59, singles)
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


def test_point_by_point_set_tabs_keep_only_the_same_source_match():
    links = [
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-1/?mid=m1",
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-2/?mid=m1",
        "https://www.flashscore.com.ua/match/tennis/a/b/summary/point-by-point/set-3/?mid=other",
        "https://example.invalid/match/tennis/a/b/summary/point-by-point/set-4/?mid=m1",
    ]
    assert _point_by_point_set_tabs(links, "m1") == {1: links[0], 2: links[1]}
