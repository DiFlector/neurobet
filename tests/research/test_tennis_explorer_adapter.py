from datetime import date, datetime, timezone

import pytest

from app.tennis_explorer import PARSER_VERSION, PROFILE_PARSER_VERSION, parse_profile_page, parse_results_page


FETCHED_AT = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def _row(cls, name, player, sets, games, match_id=None):
    match = f'<td><a href="/match-detail/?id={match_id}">info</a></td>' if match_id else ""
    cells = "".join(f'<td class="score">{value}</td>' for value in games)
    result = f'<td class="result">{sets}</td>'
    return f'<tr class="{cls}"><td class="t-name"><a href="/player/{player}/">{name}</a></td>{result}{cells}{match}</tr>'


@pytest.fixture
def results_page():
    return ('''<!doctype html><html><head><title>Tennis Explorer: Tennis Results</title></head><body>
    <table class="result">
      <tr class="head flags"><td><a href="/example/2026/itf-women/">Example ITF Women</a></td></tr>
      <tr class="one bott"><td class="first time">09:00</td><td class="t-name"><a href="/player/alpha-1/">Alpha A.</a></td><td class="result">2</td><td class="score">7<sup>9</sup></td><td class="score">6</td><td><a href="/match-detail/?id=1001">info</a></td></tr>
      <tr class="one"><td class="t-name"><a href="/player/beta-2/">Beta B.</a></td><td class="result">0</td><td class="score">6<sup>7</sup></td><td class="score">4</td></tr>
      <tr class="two bott"><td class="first time">10:00</td><td class="t-name"><a href="/player/gamma-3/">Gamma C.</a></td><td class="result">1</td><td class="score">6</td><td class="score">2</td><td><a href="/match-detail/?id=1002">info</a></td></tr>
      <tr class="two"><td class="t-name"><a href="/player/delta-4/">Delta D.</a></td><td class="result">0</td><td class="score">4</td><td class="score">1</td></tr>
      <tr class="one bott"><td class="first time">11:00</td><td class="t-name"><a href="/player/team-a/">A One</a> / <a href="/player/team-b/">B One</a></td><td class="result">2</td><td class="score">6</td><td class="score">6</td><td><a href="/match-detail/?id=1003">info</a></td></tr>
      <tr class="one"><td class="t-name"><a href="/player/team-c/">C One</a> / <a href="/player/team-d/">D One</a></td><td class="result">0</td><td class="score">2</td><td class="score">3</td></tr>
    </table></body></html>''').encode()


def test_parser_keeps_completed_singles_only_and_preserves_tiebreak_score(results_page):
    rows = parse_results_page(results_page, date(2026, 10, 22), fetched_at=FETCHED_AT)
    assert len(rows) == 1
    match = rows[0]
    assert match["source_match_id"] == "1001"
    assert match["tour_level"] == "itf"
    assert match["gender"] == "w"
    assert (match["winner_name"], match["loser_name"]) == ("Alpha A.", "Beta B.")
    assert match["score"] == "7(9)-6(7) 6-4"
    assert match["played_at"] == datetime(2026, 10, 22, tzinfo=timezone.utc)
    assert match["available_at"] == FETCHED_AT
    assert match["stats"]["retrospective"] is True
    assert match["stats"]["event_date_precision"] == "provider_local_day"


def test_parser_classifies_atp_wta_and_challenger_from_tournament_context():
    page = b'''<html><title>Tennis Explorer: Tennis Results</title><table class="result">
      <tr class="head"><td><a href="/event/atp-men/">ATP Example</a></td></tr>
      <tr class="one"><td><a href="/player/a/">A A.</a></td><td class="result">2</td><td class="score">6</td><td class="score">6</td><td><a href="/match-detail/?id=1">info</a></td></tr>
      <tr class="one"><td><a href="/player/b/">B B.</a></td><td class="result">0</td><td class="score">2</td><td class="score">3</td></tr>
      <tr class="head"><td><a href="/event/wta-women/">WTA Example</a></td></tr>
      <tr class="two"><td><a href="/player/c/">C C.</a></td><td class="result">2</td><td class="score">6</td><td class="score">6</td><td><a href="/match-detail/?id=2">info</a></td></tr>
      <tr class="two"><td><a href="/player/d/">D D.</a></td><td class="result">0</td><td class="score">2</td><td class="score">3</td></tr>
      <tr class="head"><td><a href="/event/challenger-men/">Challenger</a></td></tr>
      <tr class="one"><td><a href="/player/e/">E E.</a></td><td class="result">2</td><td class="score">6</td><td class="score">6</td><td><a href="/match-detail/?id=3">info</a></td></tr>
      <tr class="one"><td><a href="/player/f/">F F.</a></td><td class="result">0</td><td class="score">2</td><td class="score">3</td></tr>
    </table></html>'''
    rows = parse_results_page(page, date(2026, 10, 22), fetched_at=FETCHED_AT)
    assert [(r["tour_level"], r["gender"]) for r in rows] == [("atp", "m"), ("wta", "w"), ("challenger", "m")]


def test_parser_rejects_challenge_or_error_html():
    with pytest.raises(ValueError, match="not_a_tennisexplorer_results_page"):
        parse_results_page(b"<html><title>Access denied</title><body>challenge</body></html>", date(2026, 10, 22))


def test_parser_declares_version_and_deduplicates_mirrored_match_rows(results_page):
    rows = parse_results_page(results_page, date(2026, 10, 22), fetched_at=FETCHED_AT)
    assert PARSER_VERSION == "tennisexplorer_results_v1"
    assert len({row["source_match_id"] for row in rows}) == len(rows)


def test_profile_parser_reads_accessible_singles_and_tiebreaks_without_claiming_career_completeness():
    profile_page = b'''<!doctype html><html><head><title>Sample Player - Tennis Explorer</title></head><body>
      <table class="plDetail"><tr><td>Country: Example Sex: woman</td></tr></table>
      <div id="matches-2026-1-data"><table class="result">
        <tr class="head flags"><td><a href="/sample/2026/itf-women/">Sample ITF</a></td></tr>
        <tr><td class="first time">23.10.</td><td class="t-name"><a href="/player/sample-player/">Sample P.</a> - <a href="/player/opponent/">Opponent O.</a></td><td class="round">F</td><td class="tl"><a href="/match-detail/?id=98765">6-4, 7-6<sup>5</sup></a></td></tr>
        <tr><td class="first time">22.10.</td><td class="t-name"><a href="/player/opponent/">Opponent O.</a> - <a href="/player/sample-player/">Sample P.</a></td><td class="round">SF</td><td class="tl"><a href="/match-detail/?id=98766">6-2, 2-6, 3-6</a></td></tr>
      </table></div>
      <div id="matches-2026-2-data"><table class="result">
        <tr class="head flags"><td><a href="/sample/2026/itf-women/">Doubles</a></td></tr>
        <tr><td class="first time">21.10.</td><td class="t-name"><a href="/player/sample-player/">Sample P.</a> / <a href="/player/partner/">Partner P.</a></td><td class="tl"><a href="/match-detail/?id=98767">6-1, 6-1</a></td></tr>
      </table></div>
    </body></html>'''

    result = parse_profile_page(
        profile_page, "https://www.tennisexplorer.com/player/sample-player/?annual=all", fetched_at=FETCHED_AT
    )

    assert result["parser_version"] == PROFILE_PARSER_VERSION
    assert result["source_player_id"] == "sample-player"
    assert result["years"] == [2026]
    assert result["rows_seen"] == 3
    assert result["rows_rejected"] == 1
    assert result["complete_career_history"] is False
    assert [(row["source_match_id"], row["winner_player_id"], row["score"], row["tour_level"], row["gender"])
            for row in result["matches"]] == [
                ("98765", "sample-player", "6-4 7-6(5)", "itf", "w"),
                ("98766", "sample-player", "6-2 2-6 3-6", "itf", "w"),
            ]


def test_profile_parser_reads_all_year_pages_and_reports_pagination_gaps():
    page = b'''<!doctype html><html><head><title>Sample Player - Tennis Explorer</title></head><body>
      <table class="plDetail"><tr><td>Sex: woman</td></tr></table>
      <ul><li id="matches-2026-1">1</li><li id="matches-2026-2">2</li></ul>
      <div id="matches-2026-1-data"><table><tr class="head"><td><a href="/itf-women/">ITF Women</a></td></tr>
        <tr><td class="first time">23.10.</td><td class="t-name"><a href="/player/sample-player/">Sample P.</a> - <a href="/player/a/">A A.</a></td><td class="round">F</td><td class="tl"><a href="/match-detail/?id=10001">6-4, 6-3</a></td></tr>
      </table></div>
      <div id="matches-2026-2-data"><table><tr class="head"><td><a href="/itf-women/">ITF Women</a></td></tr>
        <tr><td class="first time">22.10.</td><td class="t-name"><a href="/player/b/">B B.</a> - <a href="/player/sample-player/">Sample P.</a></td><td class="round">SF</td><td class="tl"><a href="/match-detail/?id=10002">6-2, 4-6, 3-6</a></td></tr>
      </table></div>
    </body></html>'''

    result = parse_profile_page(
        page, "https://www.tennisexplorer.com/player/sample-player/?annual=all", fetched_at=FETCHED_AT
    )

    assert [match["source_match_id"] for match in result["matches"]] == ["10001", "10002"]
    assert result["pages_by_year"] == {"2026": [1, 2]}
    assert result["rows_by_year"] == {"2026": 2}
    assert result["pagination_complete"] is True
    assert result["complete_career_history"] is False

    incomplete = page.replace(
        b'<div id="matches-2026-2-data"><table>',
        b'<div id="matches-2026-3-data"><table>',
    )
    incomplete_result = parse_profile_page(
        incomplete, "https://www.tennisexplorer.com/player/sample-player/?annual=all", fetched_at=FETCHED_AT
    )
    assert incomplete_result["pagination_complete"] is False
    assert incomplete_result["pagination_selector_without_data"] == {"2026": [2]}


def test_tennis_explorer_parser_upgrade_can_reuse_only_hash_verified_raw():
    import hashlib
    from app.external_tennis_sources import _tennis_explorer_archived_profile_body

    body = b"saved provider response"
    class Storage:
        def __init__(self, value): self.value = value
        def read_external_raw_bytes(self, _key): return self.value

    prior = {
        "key": "snapshots/external-tennis/tennis_explorer/profile-x/hash.json.gz",
        "content_sha256": hashlib.sha256(body).hexdigest(),
    }
    assert _tennis_explorer_archived_profile_body(prior, Storage(body)) == body
    assert _tennis_explorer_archived_profile_body(prior, Storage(b"corrupt")) is None
    assert _tennis_explorer_archived_profile_body(
        {**prior, "key": "snapshots/research/not-an-external-raw.json"}, Storage(body)
    ) is None


def test_profile_parser_recognizes_linkless_futures_from_heading_and_player_gender():
    profile_page = b'''<!doctype html><html><head><title>Sample Player - Tennis Explorer</title></head><body>
      <table class="plDetail"><tr><td>Country: Example Sex: man</td></tr></table>
      <div id="matches-2026-1-data"><table class="result">
        <tr class="head flags"><td>Futures Monastir M15</td></tr>
        <tr><td class="first time">23.10.</td><td class="t-name"><a href="/player/sample-player/">Sample P.</a> - <a href="/player/opponent/">Opponent O.</a></td><td class="round">F</td><td class="tl"><a href="/match-detail/?id=99881">6-4, 6-3</a></td></tr>
      </table></div>
    </body></html>'''

    result = parse_profile_page(
        profile_page, "https://www.tennisexplorer.com/player/sample-player/?annual=all", fetched_at=FETCHED_AT
    )

    assert result["rows_seen"] == 1 and result["rows_rejected"] == 0
    assert result["matches"][0]["tour_level"] == "itf"
    assert result["matches"][0]["gender"] == "m"
    assert result["matches"][0]["tournament"] == "Futures Monastir M15"


def test_linkless_itf_category_codes_identify_tour_and_gender():
    from app.tennis_explorer import _profile_tour

    assert _profile_tour("", "ITF M25 Antalya") == ("itf", "m")
    assert _profile_tour("", "ITF W15 Monastir") == ("itf", "w")


class _Tx:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_):
        return False


class _CaptureConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((str(query), params))


class _CaptureEngine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Tx(self.connection)


def test_import_is_idempotent_across_repeated_fetches():
    import json
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    conn = _CaptureConnection()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(conn))
    row = parse_results_page(
        b'''<html><title>Tennis Explorer: Tennis Results</title><table class="result">
        <tr class="head"><td><a href="/example/2026/atp-men/">ATP Example</a></td></tr>
        <tr class="one"><td><a href="/player/a/">A A.</a></td><td class="result">2</td><td class="score">6</td><td class="score">6</td><td><a href="/match-detail/?id=42">info</a></td></tr>
        <tr class="one"><td><a href="/player/b/">B B.</a></td><td class="result">0</td><td class="score">2</td><td class="score">3</td></tr>
        </table></html>''',
        date(2026, 10, 22), fetched_at=FETCHED_AT,
    )[0]
    worker._store_tennis_explorer_results([row], "a" * 64, fetched_at=FETCHED_AT)
    later = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    worker._store_tennis_explorer_results([row], "a" * 64, fetched_at=later)
    observation_calls = [call for call in conn.calls if "INSERT INTO ext_tennis_match_observations" in call[0]]
    assert len(observation_calls) == 2
    assert observation_calls[0][1][0]["digest"] == observation_calls[1][1][0]["digest"]
    assert json.loads(observation_calls[0][1][0]["payload"])["fetched_at"] != json.loads(observation_calls[1][1][0]["payload"])["fetched_at"]


def test_profile_history_import_persists_its_own_parser_version():
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    conn = _CaptureConnection()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(conn))
    row = {
        "source_match_id": "profile-1", "source_url": "https://www.tennisexplorer.com/match-detail/?id=profile-1",
        "played_at": datetime(2026, 10, 20, tzinfo=timezone.utc), "available_at": FETCHED_AT,
        "tour_level": "itf", "tournament": "Example", "surface": "unknown", "best_of": 3,
        "round_name": "F", "gender": "w", "winner_name": "Sample P.", "loser_name": "Other O.",
        "winner_player_id": "sample-player", "loser_player_id": "other-player", "score": "6-4 6-3",
        "stats": {"source": "tennis_explorer", "history_scope": "accessible_profile_snapshot"},
    }
    worker._store_tennis_explorer_results(
        [row], "a" * 64, fetched_at=FETCHED_AT, parser_version=PROFILE_PARSER_VERSION
    )

    insert = next(params for statement, params in conn.calls if "INSERT INTO ext_tennis_match_observations" in statement)
    assert insert[0]["parser_version"] == PROFILE_PARSER_VERSION


def test_profile_parser_upgrade_bypasses_weekly_cache_and_http_validators():
    from app.external_tennis_sources import (
        _tennis_explorer_profile_needs_fetch,
        _tennis_explorer_profile_request_headers,
    )

    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    old_parser = {
        "status": 200, "checked_at": now.isoformat(),
        "parser_version": "tennisexplorer_profile_v1", "etag": '"same-page"',
    }
    current_parser = {**old_parser, "parser_version": PROFILE_PARSER_VERSION}

    assert _tennis_explorer_profile_needs_fetch(old_parser, now, PROFILE_PARSER_VERSION)
    assert _tennis_explorer_profile_request_headers(old_parser, PROFILE_PARSER_VERSION) == {}
    assert not _tennis_explorer_profile_needs_fetch(current_parser, now, PROFILE_PARSER_VERSION)
    assert _tennis_explorer_profile_request_headers(current_parser, PROFILE_PARSER_VERSION) == {
        "If-None-Match": '"same-page"'
    }
