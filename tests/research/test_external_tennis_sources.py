from datetime import datetime, timezone

from app.external_tennis_sources import (
    ARCHIVE_STOP_AT,
    _api_tennis_daily_budget,
    _tennis_explorer_profile_daily_budget,
    _normalize_tour,
    _normalize_api_tennis_pbp,
    _api_tennis_player_stat_ratios,
    _api_tennis_winner_first_score,
    _api_tennis_projection_item,
    _prioritize_tennis_explorer_profiles,
    _tennis_abstract_imported_observations,
    _livescore_catalog_summary,
    _live_tennis_player_candidate,
    _live_tennis_player_profile,
    _point_completeness,
    _tml_historical_partitions,
    _store_result,
    _fixture_events,
)


def test_tennis_explorer_profile_budget_prioritizes_current_fonbet_players_without_linking():
    candidates = [
        {"player_id": "recent-1", "display_names": ["Less Active Player"],
         "latest_played_at": datetime(2026, 10, 24, tzinfo=timezone.utc)},
        {"player_id": "fonbet-player", "display_names": ["Elina Svitolina"],
         "latest_played_at": datetime(2026, 10, 20, tzinfo=timezone.utc)},
        {"player_id": "not-exact", "display_names": ["Svitolina E."],
         "latest_played_at": datetime(2026, 10, 23, tzinfo=timezone.utc)},
    ]

    ordered = _prioritize_tennis_explorer_profiles(
        candidates, {"Элина Свитолина"}
    )

    # A surname+initial match is enough to spend a profile request first, but
    # this scheduling helper never creates or confirms an identity link.
    assert [row["player_id"] for row in ordered] == ["fonbet-player", "not-exact", "recent-1"]


def test_tennis_explorer_profile_budget_defaults_to_twenty_and_invalid_values_fail_closed(monkeypatch):
    monkeypatch.delenv("TENNIS_EXPLORER_PROFILE_DAILY_BUDGET", raising=False)
    assert _tennis_explorer_profile_daily_budget() == 20
    monkeypatch.setenv("TENNIS_EXPLORER_PROFILE_DAILY_BUDGET", "0")
    assert _tennis_explorer_profile_daily_budget() == 0
    monkeypatch.setenv("TENNIS_EXPLORER_PROFILE_DAILY_BUDGET", "not-a-number")
    assert _tennis_explorer_profile_daily_budget() == 0


def test_tennis_abstract_status_counts_durable_profiles_not_empty_last_cycle():
    assert _tennis_abstract_imported_observations([
        {"status": 200, "normalized_matches": 10},
        {"status": 0, "normalized_matches": 99},
        '{"status": 200, "normalized_matches": 4}',
        {"status": 200, "normalized_matches": "invalid"},
    ]) == 14


def test_tml_historical_partitions_cover_available_tours_newest_first():
    partitions = _tml_historical_partitions(2026)

    assert partitions[:3] == [(2025, "atp"), (2025, "wta"), (2025, "challenger")]
    assert (1967, "atp") in partitions
    assert (1990, "wta") in partitions
    assert (1978, "challenger") in partitions
    assert not any(year >= 2026 for year, _tour in partitions)
    assert not any(year < start for year, tour in partitions
                   for start in [dict(atp=1967, wta=1990, challenger=1978)[tour]])


def test_tml_historical_archive_checkpoints_missing_and_available_seasons(monkeypatch):
    import asyncio
    import time
    from app.external_tennis_sources import ExternalTennisSourcesWorker, TML_PARSER_VERSION

    worker = ExternalTennisSourcesWorker(storage=object(), engine=object())
    snapshots = {"historical_archive": {
        "through_year": 2024, "next_partition": 0,
        "available_by_tour": {}, "unavailable_by_tour": {},
        "last_error": "IntegrityError", "last_status": 0,
    }}
    worker._read_source_snapshot = lambda _source, key: dict(snapshots.get(key, {}))
    worker._save_source_snapshot = lambda _source, key, value: snapshots.__setitem__(key, dict(value))
    worker._last_tml_history_request = time.monotonic() - 3

    async def fetch(tour, partition, _url):
        if partition == "historical_2024_atp":
            snapshots[partition] = {"status": 404}
            return 404
        snapshots[partition] = {"status": 200, "parser_version": TML_PARSER_VERSION,
                                 "content_sha256": "sha256", "key": "raw/tml.csv"}
        return 200

    worker._fetch_tml_csv = fetch
    assert asyncio.run(worker._fetch_tml_historical_season_once(current_year=2025)) is True
    state = snapshots["historical_archive"]
    assert state["next_partition"] == 1
    assert state["unavailable_by_tour"] == {"atp": 1}
    assert "last_error" not in state and "last_status" not in state
    assert snapshots["historical_2024_atp"]["historical_unavailable"] is True

    worker._last_tml_history_request = time.monotonic() - 3
    assert asyncio.run(worker._fetch_tml_historical_season_once(current_year=2025)) is True
    state = snapshots["historical_archive"]
    assert state["next_partition"] == 2
    assert state["available_by_tour"] == {"wta": 1}


def test_tml_historical_archive_respects_retry_after_without_moving_cursor(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    worker = ExternalTennisSourcesWorker(storage=object(), engine=object())
    snapshots = {}
    worker._read_source_snapshot = lambda _source, key: dict(snapshots.get(key, {}))
    worker._save_source_snapshot = lambda _source, key, value: snapshots.__setitem__(key, dict(value))
    calls = []

    async def rate_limited(_tour, partition, _url):
        calls.append(partition)
        snapshots[partition] = {"status": 429, "retry_after": "120", "error": "HTTP_429"}
        return 429

    worker._fetch_tml_csv = rate_limited
    assert asyncio.run(worker._fetch_tml_historical_season_once(current_year=2025)) is True
    state = snapshots["historical_archive"]
    assert state["next_partition"] == 0
    assert datetime.fromisoformat(state["retry_after_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    assert asyncio.run(worker._fetch_tml_historical_season_once(current_year=2025)) is False
    assert calls == ["historical_2024_atp"]


def test_archive_cutoff_is_utc_start_of_november_7():
    assert ARCHIVE_STOP_AT == datetime(2026, 11, 7, tzinfo=timezone.utc)


def test_livescore_catalog_summary_counts_discovery_only_and_rejects_bad_envelopes():
    import pytest
    summary = _livescore_catalog_summary({"Stages": [
        {"Snm": "ITF W15", "Events": [{"Eid": "a"}, {"Eid": "b"}]},
        {"Snm": "ATP", "Events": [{"Eid": "a"}]},
    ]})
    assert summary == {
        "match_count": 3, "identified_matches": 2,
        "tournaments": ["ATP", "ITF W15"], "catalog_valid": True,
    }
    with pytest.raises(ValueError, match="invalid_livescore_catalog"):
        _livescore_catalog_summary({"events": []})


def test_livescore_catalog_worker_archives_validated_discovery_snapshot(monkeypatch):
    import asyncio
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    class ReadConnection:
        def execute(self, *_args, **_kwargs):
            return _FakeResult(None)

    class ReadEngine:
        def connect(self):
            return _Transaction(ReadConnection())

    worker = ExternalTennisSourcesWorker(storage=object(), engine=ReadEngine())
    saved = []
    worker._reserve_daily_call = lambda *_args, **_kwargs: True

    async def fetch(*_args, **_kwargs):
        return {"status": 200, "body": b'{"Stages":[{"Snm":"ITF","Events":[{"Eid":"1"}]}]}',
                "content_sha256": "abc", "key": "raw/livescore.json"}

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _key, result: saved.append(result)
    assert asyncio.run(worker._livescore_catalog_once()) is True
    assert len(saved) == 1
    assert saved[0]["match_count"] == 1
    assert saved[0]["identified_matches"] == 1
    assert saved[0]["role"] == "match_discovery_only"
    assert saved[0]["score_authority"] == "fonbet"


def test_live_tennis_player_search_requires_one_exact_non_team_identity():
    candidate, status = _live_tennis_player_candidate({"data": [
        {"id": 11, "name": "Jannik Sinner", "is_doubles_team": False},
        {"id": 12, "name": "Jannik Sinner Jr.", "is_doubles_team": False},
    ]}, "Jannik Sinner")
    assert candidate["id"] == 11
    assert status == "MATCHED"

    candidate, status = _live_tennis_player_candidate({"data": [
        {"id": 11, "name": "Jannik Sinner"}, {"id": 12, "name": "Jannik Sinner"},
    ]}, "Jannik Sinner")
    assert candidate is None
    assert status == "PLAYER_SEARCH_AMBIGUOUS"

    candidate, status = _live_tennis_player_candidate({"data": [
        {"id": 13, "name": "Sinner / Sinner", "is_doubles_team": True},
    ]}, "Jannik Sinner")
    assert candidate is None
    assert status == "PLAYER_NOT_FOUND"


def test_live_tennis_player_profile_checks_id_and_full_name():
    payload = {"id": 11, "name": "Jannik Sinner", "tour": "atp", "ranking": 2,
               "country": "Italy", "profile_version": 1}
    profile = _live_tennis_player_profile(payload, expected_id="11", expected_name="Jannik Sinner")
    assert profile["ranking"] == 2
    assert profile["tour"] == "atp"
    assert _live_tennis_player_profile(payload, expected_id="12", expected_name="Jannik Sinner") is None
    assert _live_tennis_player_profile(payload, expected_id="11", expected_name="J. Sinner") is None


def test_live_tennis_profile_worker_uses_two_free_calls_and_archives_profile(monkeypatch):
    import asyncio
    import json
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    target = {"participant_id": "participant-1", "canonical_name": "Jannik Sinner"}

    class Result:
        def mappings(self):
            return self
        def all(self):
            return [target]

    class Connection:
        def execute(self, statement, *_args, **_kwargs):
            assert "cp.display_name" in str(statement)
            assert "s.state_json->>'requested_name'=" in str(statement)
            assert "PARTITION BY lookup_name" in str(statement)
            return Result()

    class Transaction:
        def __enter__(self):
            return Connection()
        def __exit__(self, *_args):
            return False

    class Engine:
        def connect(self):
            return Transaction()

    worker = ExternalTennisSourcesWorker(storage=object(), engine=Engine())
    reservations = []
    saved = []
    snapshots = []
    worker._reserve_daily_call = lambda source, budget, *, minute_budget=None: (
        reservations.append((source, budget, minute_budget)) or True
    )

    async def fetch(_source, partition, url, *, headers=None, max_bytes=None):
        assert headers["Authorization"] == "Bearer test-key"
        if "/players?" in url:
            body = json.dumps({"data": [{"id": 11, "name": "Jannik Sinner"}]}).encode()
            return {"status": 200, "key": "raw/search.json", "content_sha256": "search-hash",
                    "fetched_at": "2026-10-24T04:00:00+00:00", "body": body}
        body = json.dumps({"id": 11, "name": "Jannik Sinner", "tour": "atp", "ranking": 2}).encode()
        return {"status": 200, "key": "raw/profile.json", "content_sha256": "profile-hash",
                "fetched_at": "2026-10-24T04:00:01+00:00", "body": body}

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda source, key, state: snapshots.append((source, key, state))
    monkeypatch.setenv("LIVE_TENNIS_API_KEY", "test-key")
    result = asyncio.run(worker._fetch_live_tennis_profiles())

    assert result == {"attempted": 1, "saved": 1, "skipped": 0}
    assert len(reservations) == 2
    assert all(call == ("live_tennis_api", 80, 24) for call in reservations)
    assert snapshots[0][1] == "profile:participant-1"
    assert snapshots[0][2]["profile"]["player_id"] == "11"
    assert snapshots[0][2]["search_raw_object_key"] == "raw/search.json"
    assert snapshots[0][2]["profile_raw_object_key"] == "raw/profile.json"
    assert snapshots[0][2]["match_data_authority"] == "fonbet"


def test_live_tennis_profile_skips_abbreviated_non_latin_name_without_spending_quota(monkeypatch):
    import asyncio
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    target = {"participant_id": "participant-ru", "canonical_name": "Басилашвили Н"}

    class Result:
        def mappings(self): return self
        def all(self): return [target]

    class Connection:
        def execute(self, statement, *_args, **_kwargs):
            assert "cp.display_name" in str(statement)
            return Result()

    class Transaction:
        def __enter__(self): return Connection()
        def __exit__(self, *_args): return False

    class Engine:
        def connect(self): return Transaction()

    async def no_op():
        return None

    worker = ExternalTennisSourcesWorker(storage=object(), engine=Engine())
    worker._reserve_daily_call = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("quota must not be spent for an abbreviated localized name")
    )
    worker._archive_external_get = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("profile search must not use a localized abbreviation")
    )
    worker._save_source_snapshot = lambda *_args, **_kwargs: None
    worker._refresh_public_sources = no_op
    monkeypatch.setenv("LIVE_TENNIS_API_KEY", "test-key")

    result = asyncio.run(worker._fetch_live_tennis_profiles(limit=1))

    assert result == {"attempted": 0, "saved": 0, "skipped": 1}


def test_live_tennis_profile_search_429_keeps_http_failure_and_retry_after(monkeypatch):
    import asyncio
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    target = {"participant_id": "participant-429", "canonical_name": "Jannik Sinner"}

    class Result:
        def mappings(self): return self
        def all(self): return [target]

    class Connection:
        def execute(self, *_args, **_kwargs): return Result()

    class Transaction:
        def __enter__(self): return Connection()
        def __exit__(self, *_args): return False

    class Engine:
        def connect(self): return Transaction()

    worker = ExternalTennisSourcesWorker(storage=object(), engine=Engine())
    worker._reserve_daily_call = lambda *_args, **_kwargs: True
    snapshots = []

    async def fetch(*_args, **_kwargs):
        return {"status": 429, "retry_after": "900"}

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda source, key, state: snapshots.append((source, key, state))
    monkeypatch.setenv("LIVE_TENNIS_API_KEY", "test-key")

    result = asyncio.run(worker._fetch_live_tennis_profiles(limit=1))

    assert result == {"attempted": 1, "saved": 0, "skipped": 0}
    state = snapshots[0][2]
    assert state["status"] == "search_failed"
    assert state["match_status"] == "SEARCH_HTTP_429"
    assert state["error"] == "HTTP_429"
    assert state["retry_after"] == "900"


def test_observed_empty_provider_envelope_is_not_confused_with_failure():
    import pytest
    assert _fixture_events({"success": 1}) == []
    assert _fixture_events({"success": 1, "result": []}) == []
    for invalid in ({"success": 0}, {"error": "unavailable"}, {"success": 1, "result": None}, []):
        with pytest.raises(ValueError):
            _fixture_events(invalid)


def test_api_tennis_budget_cannot_exceed_planned_daily_ceiling(monkeypatch):
    monkeypatch.setenv("API_TENNIS_DAILY_BUDGET", "99999")
    assert _api_tennis_daily_budget() == 6000
    monkeypatch.setenv("API_TENNIS_DAILY_BUDGET", "invalid")
    assert _api_tennis_daily_budget() == 6000


def test_tennis_abstract_persistence_binds_source_identity_as_json_text():
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    class CaptureConnection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))
            return type("Result", (), {"rowcount": 1})()

    class Transaction:
        def __init__(self, connection):
            self.connection = connection
        def __enter__(self):
            return self.connection
        def __exit__(self, *_args):
            return False

    class Engine:
        def __init__(self):
            self.connection = CaptureConnection()
        def begin(self):
            return Transaction(self.connection)

    engine = Engine()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=engine)
    worker._store_tennis_abstract_observations(
        [{"source_match_id": "row-1", "played_at": "2026-10-20", "player_won": True}],
        profile_id="canonical-player-id", source_player_id="PlayerSlug",
        source_url="https://www.tennisabstract.com/jsfrags/PlayerSlug.js",
        profile_result={"key": "profile-raw", "content_sha256": "profile-hash"},
        fragment_result={"key": "fragment-raw", "content_sha256": "fragment-hash"},
        fetched_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
    )

    assert len(engine.connection.calls) == 2
    insert_sql, insert_params = engine.connection.calls[0]
    identity_sql, identity_params = engine.connection.calls[1]
    assert "CAST(:payload AS jsonb)" in insert_sql
    assert insert_params["payload"]
    assert "to_jsonb(CAST(:slug AS text))" in identity_sql
    assert identity_params["slug"] == "PlayerSlug"


def test_tennis_abstract_profile_without_fragment_is_archived_and_not_refetched(monkeypatch):
    import asyncio
    from app import external_tennis_sources as module
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    candidate = {"participant_id": "participant-1", "id": "canonical-1",
                 "display_name": "Jannik Sinner", "gender": "m", "ta_id": None}

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def mappings(self):
            return self
        def all(self):
            return self.rows

    candidate_queries = []

    class Connection:
        def execute(self, statement, *_args, **_kwargs):
            sql = str(statement)
            if "FROM tennis_player_profiles p" in sql:
                candidate_queries.append(sql)
                return Result([candidate])
            return Result([])

    class Engine:
        def connect(self):
            return _Transaction(Connection())

    worker = ExternalTennisSourcesWorker(storage=object(), engine=Engine())
    worker._reserve_daily_call = lambda *_args, **_kwargs: True
    calls, snapshots = [], []

    async def fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": 200,
                "body": b'<html><title>Tennis Abstract: Jannik Sinner Match Results, Splits, and Analysis</title></html>',
                "fetched_at": "2026-10-24T10:00:00+00:00", "content_sha256": "page-hash",
                "key": "raw/profile.html.gz"}

    async def no_sleep(_delay):
        return None

    async def refresh():
        return None

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _key, value: snapshots.append(value)
    worker._refresh_public_sources = refresh
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

    asyncio.run(worker._fetch_tennis_abstract_profiles())

    assert len(candidate_queries) == 1
    assert "j.status='blocked' AND j.last_error='CAPTCHA_CHALLENGE_ABORTED'" in candidate_queries[0]
    assert "e.scheduled_start_at BETWEEN now()-interval '6 hours'" in candidate_queries[0]
    assert len(calls) == 1  # no fragment means there is no second HTTP request
    profile_state = next(value for value in snapshots if value.get("reason") == "tennis_abstract_fragment_missing")
    assert profile_state["status"] == 200
    assert profile_state["error"] == "profile_capability_unavailable"
    assert profile_state["profile_raw_object_key"] == "raw/profile.html.gz"
    assert profile_state["usable_for_metrics"] is False
    assert profile_state["retry_after_at"].startswith("2026-")


def test_tennis_abstract_stops_candidate_scan_when_daily_quota_is_exhausted(monkeypatch):
    import asyncio
    from app import external_tennis_sources as module
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    candidates = [
        {"participant_id": f"participant-{index}", "id": f"canonical-{index}",
         "display_name": f"Player Name{index}", "gender": "m", "ta_id": None}
        for index in range(3)
    ]

    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def all(self): return self.rows

    class Connection:
        def execute(self, statement, *_args, **_kwargs):
            return Result(candidates if "FROM tennis_player_profiles p" in str(statement) else [])

    class Engine:
        def connect(self): return _Transaction(Connection())

    async def no_op():
        return None

    worker = ExternalTennisSourcesWorker(storage=object(), engine=Engine())
    reserve_calls, snapshots = [], []
    worker._reserve_daily_call = lambda *args, **kwargs: reserve_calls.append(args) or False
    worker._save_source_snapshot = lambda _source, _key, value: snapshots.append(value)
    worker._archive_external_get = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network call"))
    worker._refresh_public_sources = no_op
    monkeypatch.setattr(module.asyncio, "sleep", lambda _delay: no_op())

    asyncio.run(worker._fetch_tennis_abstract_profiles())

    assert len(reserve_calls) == 1
    assert snapshots[-1]["attempted_requests"] == 0


def test_point_data_is_not_claimed_complete_from_presence_alone():
    result = _point_completeness({"pointbypoint": [{"points": [{"number_point": "1"}]}]})
    assert result["has_point_rows"] is True
    assert result["completeness"] == "partial_or_unknown"
    assert result["source_declares_complete"] is None


def test_api_tennis_pbp_normalizes_points_and_never_infers_complete_from_array():
    normalized, quality = _normalize_api_tennis_pbp({
        "pointbypoint": [{
            "set_number": "Set 1", "number_game": "1", "score": "1 - 0",
            "player_served": "First Player",
            "points": [
                {"score": "0 - 15"}, {"score": "0 - 30"}, {"score": "15 - 30"},
                {"score": "30 - 30"}, {"score": "40 - 30"},
            ],
        }]
    })
    assert len(normalized) == 5
    assert normalized[0]["server"] == 0
    assert normalized[0]["winner"] == 1
    assert normalized[-1]["state_after"] == ["40", "30"]
    assert quality["confirmed_points"] == 5
    assert quality["complete"] is False
    assert {gap["reason"] for gap in quality["gaps"]} == {"terminal_point_not_explicit"}


def test_api_tennis_tiebreak_states_are_numeric_and_serving_side_is_retained():
    normalized, quality = _normalize_api_tennis_pbp({
        "pointbypoint": [{
            "set_number": "Set 1", "number_game": "13", "score": "7 - 6",
            "player_served": "Second Player", "points": [{"score": "1 - 0"}, {"score": "1 - 1"}],
        }]
    })
    assert [row["winner"] for row in normalized] == [0, 1]
    assert all(row["tie_break"] for row in normalized)
    assert all(row["server"] == 1 for row in normalized)
    assert quality["complete"] is False


def test_api_tennis_serve_metrics_require_match_level_won_and_total_counts():
    ratios = _api_tennis_player_stat_ratios({"statistics": [
        {"stat_name": "Service Points Won", "stat_period": "match", "player_key": 1, "stat_won": 36, "stat_total": 56},
        {"stat_name": "Return Points Won", "stat_period": "match", "player_key": 1, "stat_won": 21, "stat_total": 44},
        {"stat_name": "Service Points Won", "stat_period": "set1", "player_key": 1, "stat_won": 9, "stat_total": 15},
        {"stat_name": "Service Points Won", "stat_period": "match", "player_key": 2, "stat_won": 1, "stat_total": 0},
    ]})
    assert ratios == {"1": {"serve_won": 36 / 56, "return_won": 21 / 44}}


def test_api_tennis_result_uses_canonical_gender_tokens_and_player_side_stats():
    conn = _CaptureConnection()
    at = datetime(2026, 10, 23, tzinfo=timezone.utc)
    event = {
        "event_key": "gender-stats-1", "event_date": "2026-10-22", "event_time": "17:40",
        "event_first_player": "Player A", "first_player_key": 1,
        "event_second_player": "Player B", "second_player_key": 2,
        "event_winner": "Second Player", "event_status": "Finished",
        "event_type_type": "ITF Women Singles", "event_final_result": "0 - 2",
        "statistics": [{"stat_name": "Service Points Won", "stat_period": "match", "player_key": 1,
                        "stat_won": 30, "stat_total": 50}],
    }
    _store_result(conn, event, at)
    query, values = conn.calls[0]
    assert values["gender"] == "w"
    assert values["winner"] == "Player B" and values["loser"] == "Player A"
    assert values["serve_won_loser"] == 0.6
    assert "coalesce(excluded.serve_won_winner" in query


def test_api_tennis_set_scores_are_normalized_to_winner_first_and_validated():
    first_won = {
        "event_winner": "First Player", "event_final_result": "2 - 0",
        "scores": [
            {"score_set": "2", "score_first": "7", "score_second": "6"},
            {"score_set": "1", "score_first": "6", "score_second": "3"},
        ],
    }
    second_won = {
        **first_won, "event_winner": "Second Player", "event_final_result": "0 - 2",
        "scores": [
            {"score_set": "1", "score_first": "2", "score_second": "6"},
            {"score_set": "2", "score_first": "4", "score_second": "6"},
        ],
    }

    assert _api_tennis_winner_first_score(first_won) == "6-3 7-6"
    assert _api_tennis_winner_first_score(second_won) == "6-2 6-4"
    assert _api_tennis_winner_first_score({**first_won, "event_final_result": "1 - 1"}) == ""
    assert _api_tennis_winner_first_score({**first_won, "scores": first_won["scores"][:1]}) == ""


def test_api_tennis_projection_item_preserves_source_time_and_winner_sides():
    at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    item = _api_tennis_projection_item({
        "event_key": 44, "event_date": "2026-10-22", "event_time": "14:30",
        "event_status": "Finished", "event_winner": "Second Player",
        "event_type_type": "ITF Women Singles", "event_final_result": "0 - 2",
        "event_first_player": "Player A", "first_player_key": 11,
        "event_second_player": "Player B", "second_player_key": 22,
        "tournament_name": "W35 Example", "tournament_round": "Final",
        "scores": [
            {"score_set": "1", "score_first": "2", "score_second": "6"},
            {"score_set": "2", "score_first": "4", "score_second": "6"},
        ],
    }, at)

    assert item == {
        "source_match_id": "44", "played_at": datetime(2026, 10, 22, 14, 30, tzinfo=timezone.utc),
        "available_at": at, "tour_level": "itf", "tournament": "W35 Example",
        "round_name": "Final", "gender": "w", "winner_name": "Player B",
        "loser_name": "Player A", "winner_player_id": "22", "loser_player_id": "11",
        "score": "6-2 6-4",
    }


def test_archive_persists_and_projects_api_tennis_result_after_raw_commit(monkeypatch):
    from datetime import date
    from app.external_tennis_sources import SourceArchive
    import sqlalchemy.orm

    class Connection:
        def __init__(self): self.calls = []
        def execute(self, statement, params=None): self.calls.append((str(statement), params)); return None
    class Transaction:
        def __init__(self, conn): self.conn = conn
        def __enter__(self): return self.conn
        def __exit__(self, *_args): return False
    class Engine:
        def __init__(self): self.connection = Connection()
        def begin(self): return Transaction(self.connection)
    class ProjectionSession:
        instances = []
        def __init__(self, _engine): self.committed = False; self.instances.append(self)
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def commit(self): self.committed = True

    engine = Engine()
    archive = SourceArchive(engine)
    projection_calls = []
    monkeypatch.setattr(sqlalchemy.orm, "Session", ProjectionSession)
    monkeypatch.setattr(
        "app.canonical_tennis_projection.project_api_tennis_results",
        lambda _session, items: projection_calls.append(items) or {
            "projected": 1, "merged": 0, "ambiguous": 0, "unlinked": 0, "unchanged": 0,
        },
    )
    observed_at = datetime(2026, 10, 24, tzinfo=timezone.utc)
    event = {
        "event_key": 123, "event_date": "2026-10-22", "event_time": "17:40",
        "event_first_player": "A Winner", "first_player_key": 1,
        "event_second_player": "B Loser", "second_player_key": 2,
        "event_winner": "First Player", "event_status": "Finished",
        "event_type_type": "ATP Men Singles", "event_final_result": "2 - 0",
        "tournament_name": "Vienna Open",
        "scores": [
            {"score_set": "1", "score_first": "6", "score_second": "3"},
            {"score_set": "2", "score_first": "7", "score_second": "6"},
        ],
    }

    archive.persist_events({
        "id": "job-id", "interval_start": date(2026, 10, 22), "observed_at": observed_at,
    }, [event], "raw-object", "sha256")

    assert len(projection_calls) == 1
    assert projection_calls[0][0]["score"] == "6-3 7-6"
    assert ProjectionSession.instances[0].committed is True
    assert any("canonical_projection" in sql for sql, _ in engine.connection.calls)


def test_tour_classification_keeps_itf_separate():
    assert _normalize_tour({"event_type_type": "ITF Women Singles"}) == "itf"
    assert _normalize_tour({"event_type_type": "WTA Women Singles"}) == "wta"
    assert _normalize_tour({"event_type_type": "ATP Men Doubles"}) == "atp"
    assert _normalize_tour({"event_type_type": "unknown series"}) == "unknown"


def test_source_metrics_persist_hourly_counters_and_roll_up_latency_and_fallbacks():
    import json
    from app.source_metrics import record_source_metric, summarize_source_metrics

    class Result:
        def __init__(self, value=None): self.value = value
        def scalar(self): return self.value

    class Connection:
        def __init__(self, engine): self.engine = engine
        def execute(self, statement, params):
            sql = str(statement)
            source = params["source"]
            if "INSERT INTO external_tennis_source_state" in sql:
                self.engine.state.setdefault(source, {})
            elif "SELECT state_json" in sql:
                return Result(self.engine.state.get(source, {}))
            elif "UPDATE external_tennis_source_state" in sql:
                self.engine.state[source] = json.loads(params["payload"])
            return Result()

    class Engine:
        def __init__(self): self.state = {}
        def begin(self):
            engine = self
            class Context:
                def __enter__(self): return Connection(engine)
                def __exit__(self, *_args): return False
            return Context()

    engine = Engine()
    now = datetime(2026, 10, 24, 12, 34, tzinfo=timezone.utc)
    record_source_metric(engine, "tennis_my_life", request=True, successful=True,
                         duration_ms=120, status_code=200, now=now)
    record_source_metric(engine, "tennis_my_life", request=True, successful=False,
                         duration_ms=480, status_code=429, error_code="HTTP_429", now=now)
    record_source_metric(engine, "tennis_my_life", published=1, prevented_flashscore=2, now=now)

    metrics = summarize_source_metrics(engine.state["tennis_my_life"], now=now)
    assert metrics["requests"] == 2
    assert metrics["successful_requests"] == 1
    assert metrics["errors"] == 1
    assert metrics["published"] == 1
    assert metrics["prevented_flashscore"] == 2
    assert metrics["avg_latency_ms"] == 300
    assert metrics["max_latency_ms"] == 480


class _CaptureConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        if "SELECT state_json" in str(statement):
            return _FakeResult(None)


def test_only_completed_events_enter_external_match_projection():
    conn = _CaptureConnection()
    at = datetime(2026, 10, 23, tzinfo=timezone.utc)
    base = {
        "event_key": 123,
        "event_date": "2026-10-22",
        "event_time": "17:40",
        "event_first_player": "Player A",
        "first_player_key": 1,
        "event_second_player": "Player B",
        "second_player_key": 2,
        "event_winner": "First Player",
        "event_status": "Finished",
        "event_type_type": "ATP Men Singles",
        "event_final_result": "2 - 0",
    }
    _store_result(conn, {**base, "event_status": "In Progress"}, at)
    assert conn.calls == []

    _store_result(conn, base, at)
    assert len(conn.calls) == 1
    _, values = conn.calls[0]
    assert values["winner"] == "Player A"
    assert values["loser"] == "Player B"
    assert values["available"] == at
    assert values["played"].isoformat() == "2026-10-22T17:40:00+00:00"
    assert values["score"] == ""


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_):
        return False


class _CaptureEngine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)

    def connect(self):
        return _Transaction(self.connection)


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _QuotaConnection:
    def __init__(self, state=None):
        self.state = state

    def execute(self, statement, params):
        query = str(statement)
        if query.startswith("SELECT state_json"):
            return _FakeResult(self.state)
        if "INSERT INTO external_tennis_source_state" in query:
            import json
            self.state = json.loads(params["payload"])
        return _FakeResult(None)


def test_provider_quota_survives_worker_restart():
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    database = _QuotaConnection()
    engine = _CaptureEngine(database)
    first = ExternalTennisSourcesWorker(storage=object(), engine=engine)
    second = ExternalTennisSourcesWorker(storage=object(), engine=engine)
    assert first._reserve_daily_call("api_tennis", 1) is True
    assert second._reserve_daily_call("api_tennis", 1) is False
    assert database.state["daily_calls"] == 1
    assert database.state["daily_limit"] == 1
    assert database.state["minute_limit"] is None


def test_tml_csv_import_uses_fetch_time_and_keeps_source_rows_versioned():
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    capture = _CaptureConnection()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(capture))
    csv_body = (
        "tourney_id,tourney_name,surface,tourney_date,match_num,winner_id,winner_name,winner_hand,winner_ioc,"
        "winner_rank,loser_id,loser_name,loser_hand,loser_ioc,loser_rank,score,best_of,round,w_ace,l_ace\n"
        "2026-100,Example,Hard,20260112,1,10,A Player,R,USA,20,11,B Player,L,CAN,30,6-2 6-3,3,R32,8,2\n"
        "2026-100,Example,Hard,20260112,2,12,A/B,R,USA,20,13,C/D,L,CAN,30,6-2 6-3,3,R32,8,2\n"
    ).encode()
    count = worker._store_tml_csv(csv_body, "atp", "https://stats.tennismylife.org/data/2026.csv")
    assert count == 1
    match_rows = [params for statement, params in capture.calls if "INSERT INTO ext_tennis_matches" in statement]
    assert len(match_rows) == 1 and len(match_rows[0]) == 1
    match = match_rows[0][0]
    assert match["available"] > match["played"]
    assert match["level"] == "atp"
    observation_rows = [params for statement, params in capture.calls if "INSERT INTO ext_tennis_match_observations" in statement]
    assert len(observation_rows) == 1 and len(observation_rows[0]) == 1
    assert '"retrospective": true' in observation_rows[0][0]["payload"]


def test_tml_csv_import_deduplicates_natural_keys_within_a_batch():
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    capture = _CaptureConnection()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(capture))
    csv_body = (
        "tourney_id,tourney_name,surface,tourney_date,match_num,winner_id,winner_name,loser_id,loser_name,score\n"
        "2025-100,Example,Hard,20250112,1,10,A Player,11,B Player,6-2 6-3\n"
        "2025-100,Example,Hard,20250112,2,10,A Player,12,C Player,6-4 6-4\n"
        "2025-100,Example,Hard,20250112,2,10,A Player,12,C Player,6-3 6-4\n"
    ).encode()

    assert worker._store_tml_csv(csv_body, "atp", "https://tml.test/2025.csv") == 2
    player_rows = [params for statement, params in capture.calls if "INSERT INTO ext_tennis_players" in statement]
    match_rows = [params for statement, params in capture.calls if "INSERT INTO ext_tennis_matches" in statement]
    observation_rows = [params for statement, params in capture.calls if "INSERT INTO ext_tennis_match_observations" in statement]
    assert len(player_rows[0]) == 3  # A Player appears in both source rows.
    assert len(match_rows[0]) == 2
    assert len(observation_rows[0]) == 2
    assert len({row["player_id"] for row in player_rows[0]}) == 3


def test_tml_manifest_does_not_invent_a_data_license():
    import json
    from app.external_tennis_sources import ExternalTennisSourcesWorker

    capture = _CaptureConnection()
    worker = ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(capture))
    worker._save_source_snapshot("tennis_my_life", "season_2026_atp", {
        "status": 200, "content_sha256": "a" * 64, "raw_bytes": 12,
        "key": "raw/key", "body": b"not included in manifest",
    })
    query, params = capture.calls[0]
    manifest = json.loads(params["payload"])
    assert "MIT" not in manifest["license"]
    assert manifest["license"] == "provider_terms"
    assert manifest["usage_review"]["scope"] == "noncommercial_research"
    assert manifest["usage_review"]["confirmed_by"] == "project_owner"
    assert "body" not in manifest


def test_slow_unpartitioned_archive_splits_without_claiming_a_success(monkeypatch):
    import asyncio
    from datetime import date
    from types import SimpleNamespace
    import httpx
    import app.external_tennis_sources as sources
    monkeypatch.setattr("app.source_metrics.try_record_source_metric", lambda *_a, **_k: None)

    capture = _CaptureConnection()
    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(capture))
    monkeypatch.setenv("API_TENNIS_API_KEY", "test-only")
    monkeypatch.setattr(sources.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))
    worker._reserve_daily_call = lambda *_a, **_k: True
    worker.archive.claim = lambda: {"id": "job", "interval_start": date(2026, 10, 22),
                                    "interval_end": date(2026, 10, 22), "event_type_key": "", "priority": 1}
    worker._event_types = [{"key": "265"}, {"key": "266"}]
    requests = []

    class TimeoutClient:
        async def post(self, url, data):
            requests.append(data)
            raise httpx.ReadTimeout("provider did not finish a whole-day response")

    async def client():
        return TimeoutClient()

    worker._client = client
    assert asyncio.run(worker._archive_once()) is True
    assert len(requests) == 1 and requests[0]["timezone"] == "UTC"
    split_updates = [params for sql, params in capture.calls
                     if "UPDATE external_tennis_source_jobs" in sql]
    assert split_updates[0]["reason"] == "TIMEOUT_SPLIT_BY_EVENT_TYPE"
    assert {r["event_type"] for r in capture.calls[1][1]} == {"265", "266"}
    assert all(r["priority"] == 1 for r in capture.calls[1][1])
    assert "status='complete'" not in " ".join(q for q, _ in capture.calls)
    assert worker._split_archive_job({"event_type_key": "265"}, "timeout") is False


def test_ongoing_tml_results_are_normalized_only_after_successful_response(monkeypatch):
    import asyncio
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    imported, saved = [], []

    async def fetch(_source, partition, _url, headers=None):
        return {"status": 503} if partition == "ongoing-challenger" else {"status": 200, "body": b"csv"}

    async def sleep(_seconds):
        pass

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda *_a: saved.append(_a)
    worker._store_tml_csv = lambda body, tour, url: imported.append((body, tour, url)) or 1
    monkeypatch.setattr(sources.asyncio, "sleep", sleep)
    asyncio.run(worker._fetch_tml_ongoing())
    assert {tour for _, tour, _ in imported} == {"atp", "wta"}
    assert len(saved) == 3
    failed = next(item for item in saved if item[1] == "ongoing_challenger")
    assert failed[2]["status"] == 503


def test_tml_html_challenge_does_not_refresh_source_coverage(monkeypatch):
    import asyncio
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    saved, imported = [], []

    async def fetch(_source, _partition, _url, headers=None):
        return {"status": 200, "body": b"<html>challenge</html>"}

    async def sleep(_seconds):
        pass

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _partition, result: saved.append(result)
    monkeypatch.setattr(sources.asyncio, "sleep", sleep)

    asyncio.run(worker._fetch_tml_ongoing())

    assert not imported
    assert len(saved) == 3
    assert all(item["status"] == 0 for item in saved)
    assert all(item["error"] == "ValueError" for item in saved)


def test_tml_uses_http_validators_and_preserves_cached_data_on_304(monkeypatch):
    import asyncio
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    prior = {
        "status": 200, "parser_version": sources.TML_PARSER_VERSION,
        "content_sha256": "a" * 64, "key": "raw/tml.csv.gz",
        "etag": '"tml-v4"', "normalized_matches": 19,
    }
    worker._read_source_snapshot = lambda *_a: prior
    requests, saved, imported = [], [], []

    async def fetch(source, partition, url, *, headers=None):
        requests.append((source, partition, url, headers))
        return {"status": 304, "etag": '"tml-v4"'}

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _partition, result: saved.append(result)
    worker._store_tml_csv = lambda *_args: imported.append(_args)
    asyncio.run(worker._fetch_tml_csv("atp", "season_2026_atp", "https://tml.test/2026.csv"))

    assert requests[0][3] == {"If-None-Match": '"tml-v4"'}
    assert imported == []
    assert saved[0]["status"] == 200
    assert saved[0]["validation_status"] == 304
    assert saved[0]["content_sha256"] == prior["content_sha256"]
    assert saved[0]["data_unchanged"] is True


def test_tml_same_hash_does_not_reparse_or_count_a_publication(monkeypatch):
    import asyncio
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    prior = {"status": 200, "parser_version": sources.TML_PARSER_VERSION,
             "content_sha256": "b" * 64, "key": "raw/tml.csv.gz"}
    worker._read_source_snapshot = lambda *_a: prior
    saved, imported = [], []

    async def fetch(_source, _partition, _url, *, headers=None):
        return {"status": 200, "body": b"unchanged", "content_sha256": "b" * 64,
                "key": "raw/tml.csv.gz", "etag": '"tml"'}

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _partition, result: saved.append(result)
    worker._store_tml_csv = lambda *_args: imported.append(_args)
    asyncio.run(worker._fetch_tml_csv("atp", "ongoing_atp", "https://tml.test/ongoing.csv"))

    assert imported == []
    assert saved[0]["data_unchanged"] is True


def test_tml_validator_falls_back_to_last_modified_and_resets_on_parser_change():
    from app.external_tennis_sources import TML_PARSER_VERSION, _tml_request_headers

    prior = {"status": 200, "parser_version": TML_PARSER_VERSION,
             "content_sha256": "c" * 64, "key": "raw/tml.csv.gz",
             "last_modified": "Sat, 24 Oct 2026 00:00:00 GMT"}
    assert _tml_request_headers(prior) == {"If-Modified-Since": prior["last_modified"]}
    assert _tml_request_headers({**prior, "parser_version": "obsolete"}) == {}


def test_tml_parse_failure_keeps_previous_successful_coverage(monkeypatch):
    import asyncio
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    prior = {"status": 200, "parser_version": sources.TML_PARSER_VERSION,
             "content_sha256": "d" * 64, "key": "raw/previous.csv.gz",
             "normalized_matches": 21}
    worker._read_source_snapshot = lambda *_a: prior
    saved = []

    async def fetch(_source, _partition, _url, *, headers=None):
        return {"status": 200, "body": b"invalid", "content_sha256": "e" * 64,
                "key": "raw/invalid.csv.gz"}

    def reject(*_args):
        raise ValueError("challenge_html")

    worker._archive_external_get = fetch
    worker._save_source_snapshot = lambda _source, _partition, result: saved.append(result)
    worker._store_tml_csv = reject
    asyncio.run(worker._fetch_tml_csv("atp", "season_2026_atp", "https://tml.test/2026.csv"))

    assert len(saved) == 1
    assert saved[0]["status"] == 200
    assert saved[0]["content_sha256"] == prior["content_sha256"]
    assert saved[0]["last_error"] == "ValueError"


def test_tml_unchanged_snapshot_does_not_increment_published_metric(monkeypatch):
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=_CaptureEngine(_CaptureConnection()))
    metrics = []
    monkeypatch.setattr("app.source_metrics.try_record_source_metric",
                        lambda *args, **kwargs: metrics.append((args, kwargs)))

    worker._save_source_snapshot("tennis_my_life", "ongoing_atp", {
        "status": 200, "content_sha256": "f" * 64,
        "key": "raw/unchanged.csv.gz", "data_unchanged": True,
    })
    assert metrics == []

    worker._save_source_snapshot("tennis_my_life", "ongoing_atp", {
        "status": 200, "content_sha256": "1" * 64,
        "key": "raw/changed.csv.gz",
    })
    assert len(metrics) == 1
    assert metrics[0][1] == {"published": 1}


def test_saved_api_tennis_archive_projection_runs_bounded_offline_batches(monkeypatch):
    import asyncio
    import importlib
    from types import SimpleNamespace
    import app.external_tennis_sources as sources

    worker = sources.ExternalTennisSourcesWorker(storage=object(), engine=object())
    snapshots = {"pbp_backfill": {}, "canonical_projection_backfill": {}}
    worker._read_source_snapshot = lambda _source, key: snapshots.get(key, {})
    imported = []

    def fake_import(name):
        def run(*, max_batches):
            imported.append((name, max_batches))
            return {"status": "paused", "rows_scanned": 100}
        return SimpleNamespace(run=run)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    asyncio.run(worker._project_saved_api_tennis_archives())

    assert imported == [
        ("scripts.import_api_tennis_pbp_archive", 5),
        ("scripts.import_api_tennis_canonical_archive", 5),
    ]

    snapshots["pbp_backfill"] = {"status": "complete"}
    snapshots["canonical_projection_backfill"] = {"status": "complete"}
    imported.clear()
    asyncio.run(worker._project_saved_api_tennis_archives())
    assert imported == []


def test_api_tennis_offline_batch_count_is_bounded(monkeypatch):
    from app.external_tennis_sources import _api_tennis_offline_batches_per_tick

    monkeypatch.delenv("API_TENNIS_OFFLINE_BATCHES_PER_TICK", raising=False)
    assert _api_tennis_offline_batches_per_tick() == 5
    monkeypatch.setenv("API_TENNIS_OFFLINE_BATCHES_PER_TICK", "99")
    assert _api_tennis_offline_batches_per_tick() == 5
    monkeypatch.setenv("API_TENNIS_OFFLINE_BATCHES_PER_TICK", "invalid")
    assert _api_tennis_offline_batches_per_tick() == 5
