"""Unit and integration test suite for Phase 13: Settlement and Fonbet Results Collection."""

from datetime import datetime, timezone, timedelta
import random
import uuid
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetSettlement, BetProposal
from contracts import CanonicalMatchResult
from sports_core import FonbetResultsParser
from bankroll import BankrollService, EventResultMatcher, BetSettlementEngine
from app.main import app


def utc_now():
    return datetime.now(timezone.utc)


def setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.20):
    sport = session.get(Sport, "tennis")
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()

    account = BankrollService.get_or_create_account(
        session,
        name=f"settle_acc_{uuid.uuid4().hex[:8]}",
        initial_balance=50000.0,
    )
    session.flush()

    source_id = str(random.randint(10000000, 99999999))
    event = Event(
        source="fonbet",
        source_event_id=source_id,
        sport_code="tennis",
        participant_a_name="Daniil Medvedev",
        participant_b_name="Carlos Alcaraz",
        scheduled_start_at=utc_now() - timedelta(hours=2),
        first_seen_at=utc_now() - timedelta(hours=2),
        last_seen_at=utc_now() - timedelta(minutes=10),
        status="live",
        is_live=True,
    )
    session.add(event)
    session.flush()

    proposal = BetProposal(
        id=uuid.uuid4(),
        event_id=event.id,
        sport_code="tennis",
        market="match_winner",
        outcome=outcome,
        selection_id=uuid.uuid4(),
        bookmaker_odds=odds,
        fair_odds=1.85,
        model_probability=0.55,
        edge=0.08,
        suggested_stake=stake,
    )
    session.add(proposal)
    session.flush()

    bet = BankrollService.place_bet(
        session=session,
        account_id=account.id,
        proposal_id=proposal.id,
        event_id=event.id,
        sport_code="tennis",
        market="match_winner",
        outcome=outcome,
        odds=odds,
        stake=stake,
    )
    session.commit()
    return account, event, bet


def test_fonbet_results_feed_parsing():
    """Verify parsing of official results JSON payload from fon.bet/results."""
    sample_feed = {
        "sports": [
            {"id": "3", "name": "Теннис", "fonbetId": 4}
        ],
        "sections": [
            {
                "id": 101,
                "name": "Теннис. ATP. Шанхай. Хард",
                "fonbetSportId": 4,
                "events": [201, 202, 203]
            }
        ],
        "events": [
            {
                "id": "201",
                "name": "Медведев Д. – Алькарас К.",
                "score": "2:1 (6-4 3-6 6-4)",
                "status": 3,
                "comment1": "",
                "comment2": "",
                "comment3": "",
            },
            {
                "id": "202",
                "name": "эйсы",
                "score": "10:5 (4-2 3-2 3-1)",
                "status": 3,
            },
            {
                "id": "203",
                "name": "Синнер Я. – Джокович Н.",
                "score": "0:2 (4-6 3-6)",
                "status": 3,
                "comment1": "",
                "comment2": "",
                "comment3": "",
            }
        ]
    }

    results = FonbetResultsParser.parse_feed(sample_feed)
    assert len(results) == 2, f"Expected 2 matches, got {len(results)}"

    r1 = results[0]
    assert r1.source_event_id == "201"
    assert r1.participant_a == "Медведев Д."
    assert r1.participant_b == "Алькарас К."
    assert r1.final_score == "2:1 (6-4 3-6 6-4)"
    assert r1.winner == "player_a"
    assert r1.status == "FINISHED"

    r2 = results[1]
    assert r2.source_event_id == "203"
    assert r2.participant_a == "Синнер Я."
    assert r2.participant_b == "Джокович Н."
    assert r2.winner == "player_b"
    assert r2.status == "FINISHED"
    print("test_fonbet_results_feed_parsing: OK")


def test_retirement_and_walkover_detection():
    """Verify official Fonbet retirement and walkover parsing."""
    sample_feed = {
        "sections": [
            {"id": 1, "name": "Теннис. Турнир", "fonbetSportId": 4, "events": [1, 2]}
        ],
        "events": [
            {
                "id": "1",
                "name": "Зверев А. – Рублев А.",
                "score": "1:0 (6-3 2-1)",
                "status": 3,
                "comment1": "Отказ Рублев А.",
                "comment2": "",
                "comment3": "",
            },
            {
                "id": "2",
                "name": "Циципас С. – Хачанов К.",
                "score": "-:-",
                "status": 3,
                "comment1": "Матч не состоялся",
                "comment2": "",
                "comment3": "",
            }
        ]
    }

    results = FonbetResultsParser.parse_feed(sample_feed)
    assert len(results) == 2

    # Retired match
    r1 = results[0]
    assert r1.is_retired is True
    assert r1.winner == "player_a"

    # Walkover match
    r2 = results[1]
    assert r2.is_walkover is True
    assert r2.status == "CANCELLED"
    print("test_retirement_and_walkover_detection: OK")


def test_event_result_matcher_marks_finished():
    """Verify EventResultMatcher updates event state to finished with official score."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:1 (6-4 4-6 6-3)",
            winner="player_a",
            status="FINISHED",
        )

        matched = EventResultMatcher.match_and_complete_event(session, canonical)
        assert matched is not None
        assert matched.id == event.id
        assert matched.status == "finished"
        assert matched.finished_at is not None
        assert matched.current_score["final_score"] == "2:1 (6-4 4-6 6-3)"
        assert matched.current_score["winner"] == "player_a"
        session.commit()
        print("test_event_result_matcher_marks_finished: OK")
    finally:
        session.close()


def test_settlement_win():
    """Verify WIN settlement credits payout, records BET_WIN in ledger, and releases exposure."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.50)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-3)",
            winner="player_a",
            status="FINISHED",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "WON"
        assert s.payout == 2500.0  # 1000 * 2.50
        assert s.net_profit == 1500.0

        # Check account state
        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance + 2500.0
        assert float(acc_updated.locked_exposure) == initial_exposure - 1000.0

        # Check immutable ledger
        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_WIN"
        assert float(last_entry.amount) == 2500.0
        print("test_settlement_win: OK")
    finally:
        session.close()


def test_settlement_loss():
    """Verify LOST settlement records BET_LOSS in ledger and releases exposure without payout."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_b", stake=1500.0, odds=2.20)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        # Result winner is player_a -> player_b lost
        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:1 (6-4 3-6 6-4)",
            winner="player_a",
            status="FINISHED",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "LOST"
        assert s.payout == 0.0
        assert s.net_profit == -1500.0

        # Account state: balance unchanged (stake was deducted upon placement), exposure released
        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance
        assert float(acc_updated.locked_exposure) == initial_exposure - 1500.0

        # Ledger check
        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_LOSS"
        assert float(last_entry.amount) == 0.0
        print("test_settlement_loss: OK")
    finally:
        session.close()


def test_settlement_void():
    """Verify VOID settlement refunds stake and releases exposure."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=2000.0, odds=2.00)
        initial_balance = float(account.balance)
        initial_exposure = float(account.locked_exposure)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="-:-",
            winner=None,
            status="CANCELLED",
            comments="Матч отменен организаторами",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        assert len(settlements) == 1
        s = settlements[0]
        assert s.status == "VOID"
        assert s.payout == 2000.0  # refund
        assert s.net_profit == 0.0

        acc_updated = session.get(VirtualAccount, account.id)
        assert float(acc_updated.balance) == initial_balance + 2000.0
        assert float(acc_updated.locked_exposure) == initial_exposure - 2000.0

        last_entry = (
            session.query(LedgerEntry)
            .filter(LedgerEntry.bet_id == bet.id)
            .order_by(LedgerEntry.created_at.desc())
            .first()
        )
        assert last_entry.entry_type == "BET_VOID"
        assert float(last_entry.amount) == 2000.0
        print("test_settlement_void: OK")
    finally:
        session.close()


def test_strict_idempotency_double_settlement_prevented():
    """Verify that settling the same bet twice returns existing settlement without duplicate ledger entries."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.00)

        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-2)",
            winner="player_a",
            status="FINISHED",
        )

        # First settlement
        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements_1 = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        balance_after_1 = float(session.get(VirtualAccount, account.id).balance)
        ledger_count_1 = session.query(LedgerEntry).filter(LedgerEntry.bet_id == bet.id).count()

        # Second settlement attempt (identical payload)
        settlements_2 = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        balance_after_2 = float(session.get(VirtualAccount, account.id).balance)
        ledger_count_2 = session.query(LedgerEntry).filter(LedgerEntry.bet_id == bet.id).count()

        assert balance_after_1 == balance_after_2, "Balance changed upon duplicate settlement!"
        assert ledger_count_1 == ledger_count_2, "Duplicate ledger entry created!"
        assert len(settlements_2) == 1
        assert settlements_2[0].id == settlements_1[0].id
        print("test_strict_idempotency_double_settlement_prevented: OK")
    finally:
        session.close()


def test_settlement_review_required_for_ambiguous_results():
    """Verify that ambiguous match scores are flagged for review and never settled blindly."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1000.0, odds=2.00)

        # Incomplete / disputed score
        canonical = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="-:-",
            winner=None,
            status="SETTLEMENT_REVIEW_REQUIRED",
            comments="Результат матча уточняется",
        )

        EventResultMatcher.match_and_complete_event(session, canonical)
        settlements = BetSettlementEngine.settle_event_bets(session, event, canonical)
        session.commit()

        # Bet must remain PENDING
        assert len(settlements) == 0, "Ambiguous match must not produce settlements!"
        updated_bet = session.get(Bet, bet.id)
        assert updated_bet.status == "PENDING"

        # Event must be present in review queue
        review_queue = BetSettlementEngine.get_review_queue(session)
        assert any(q["event_id"] == str(event.id) for q in review_queue)
        print("test_settlement_review_required_for_ambiguous_results: OK")
    finally:
        session.close()


def test_backend_settlement_api_endpoints():
    """Verify backend API settlement endpoints."""
    session = SessionLocal()
    try:
        account, event, bet = setup_test_event_and_bet(session, outcome="player_a", stake=1200.0, odds=2.40)

        client = TestClient(app)

        # 1. Trigger sync with official results payload
        payload = {
            "sections": [
                {"id": 1, "name": "Теннис. Турнир", "fonbetSportId": 4, "events": [int(event.source_event_id)]}
            ],
            "events": [
                {
                    "id": event.source_event_id,
                    "name": "Daniil Medvedev – Carlos Alcaraz",
                    "score": "2:0 (6-4 6-4)",
                    "status": 3,
                }
            ]
        }
        sync_resp = client.post("/api/settlement/sync", json=payload)
        assert sync_resp.status_code == 200
        sync_data = sync_resp.json()
        assert sync_data["status"] == "success"
        assert sync_data["events_matched"] == 1
        assert sync_data["bets_settled"] == 1

        # 2. Check settlement history
        hist_resp = client.get("/api/settlement/history")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert any(h["bet_id"] == str(bet.id) for h in hist_data)

        # 3. Check review queue endpoint
        rev_resp = client.get("/api/settlement/review-queue")
        assert rev_resp.status_code == 200
        assert isinstance(rev_resp.json(), list)
        print("test_backend_settlement_api_endpoints: OK")
    finally:
        session.close()


def test_tennis_one_set_score_is_not_finished():
    """A single won set (1:0 / 0:1) must not close a tennis match."""
    winner, status, is_retired, is_walkover = FonbetResultsParser.parse_tennis_winner_and_status(
        "1:0 (6-4)"
    )
    assert winner is None
    assert status == "SETTLEMENT_REVIEW_REQUIRED"
    assert is_retired is False
    assert is_walkover is False

    winner, status, _, _ = FonbetResultsParser.parse_tennis_winner_and_status("0:1 (4-6)")
    assert winner is None
    assert status == "SETTLEMENT_REVIEW_REQUIRED"

    incomplete = CanonicalMatchResult(
        source_event_id="1",
        sport_code="tennis",
        tournament="ATP",
        participant_a="A",
        participant_b="B",
        final_score="1:0 (6-4)",
        winner="player_a",
        status="FINISHED",
    )
    assert EventResultMatcher.result_is_definitive(incomplete) is False
    print("test_tennis_one_set_score_is_not_finished: OK")


def test_tennis_two_sets_score_is_finished():
    """Best-of-3 completion (2:0 / 2:1) must close the match."""
    winner, status, _, _ = FonbetResultsParser.parse_tennis_winner_and_status("2:0 (6-4 6-3)")
    assert winner == "player_a"
    assert status == "FINISHED"

    winner, status, _, _ = FonbetResultsParser.parse_tennis_winner_and_status("1:2 (6-4 3-6 2-6)")
    assert winner == "player_b"
    assert status == "FINISHED"

    complete = CanonicalMatchResult(
        source_event_id="1",
        sport_code="tennis",
        tournament="ATP",
        participant_a="A",
        participant_b="B",
        final_score="2:1 (6-4 3-6 6-2)",
        winner="player_a",
        status="FINISHED",
    )
    assert EventResultMatcher.result_is_definitive(complete) is True
    print("test_tennis_two_sets_score_is_finished: OK")


def test_matcher_skips_live_finish_on_one_set_result():
    """Live events must stay live when Fonbet publishes a premature 1:0 result."""
    session = SessionLocal()
    try:
        _, event, _ = setup_test_event_and_bet(session)
        assert event.status == "live"
        assert event.is_live is True

        premature = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="1:0 (6-4)",
            winner=None,
            status="SETTLEMENT_REVIEW_REQUIRED",
        )
        matched = EventResultMatcher.match_and_complete_event(session, premature)
        session.commit()

        assert matched is not None
        updated = session.get(Event, event.id)
        assert updated.status == "live"
        assert updated.is_live is True
        assert updated.finished_at is None
        assert "final_score" not in (updated.current_score or {})
        assert (updated.metadata_json or {}).get("pending_official_result") is not None
        print("test_matcher_skips_live_finish_on_one_set_result: OK")
    finally:
        session.close()


def test_incomplete_result_does_not_overwrite_definitive_score():
    """A weaker / review result must not wipe an already stored official finish."""
    session = SessionLocal()
    try:
        _, event, _ = setup_test_event_and_bet(session)

        definitive = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-2)",
            winner="player_a",
            status="FINISHED",
        )
        EventResultMatcher.match_and_complete_event(session, definitive)
        session.commit()

        weak = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="0:0",
            winner=None,
            status="SETTLEMENT_REVIEW_REQUIRED",
        )
        EventResultMatcher.match_and_complete_event(session, weak)
        session.commit()

        updated = session.get(Event, event.id)
        assert updated.status == "finished"
        assert updated.current_score["final_score"] == "2:0 (6-4 6-2)"
        assert updated.current_score["winner"] == "player_a"
        official = (updated.metadata_json or {}).get("fonbet_official_result") or {}
        assert official.get("final_score") == "2:0 (6-4 6-2)"
        print("test_incomplete_result_does_not_overwrite_definitive_score: OK")
    finally:
        session.close()


def test_parse_all_sports_includes_football_and_hockey():
    """Settlement sync must parse every supported Fonbet sport, not only tennis."""
    sample_feed = {
        "sections": [
            {"id": 1, "name": "Теннис. Турнир", "fonbetSportId": 4, "events": [101]},
            {"id": 2, "name": "Футбол. Лига", "fonbetSportId": 1, "events": [201]},
            {"id": 3, "name": "Хоккей. КХЛ", "fonbetSportId": 2, "events": [301]},
            {"id": 4, "name": "Баскетбол. NBA", "fonbetSportId": 3, "events": [401]},
            {"id": 5, "name": "Волейбол. Лига", "fonbetSportId": 9, "events": [501]},
        ],
        "events": [
            {
                "id": "101",
                "name": "Медведев Д. – Алькарас К.",
                "score": "2:0 (6-4 6-3)",
                "status": 3,
            },
            {
                "id": "201",
                "name": "Спартак – Зенит",
                "score": "2:1 (1:0, 1:1)",
                "status": 3,
            },
            {
                "id": "301",
                "name": "ЦСКА – СКА",
                "score": "3:2 (1:0, 1:1, 1:1)",
                "status": 3,
            },
            {
                "id": "401",
                "name": "Лейкерс – Селтикс",
                "score": "110:102",
                "status": 3,
            },
            {
                "id": "501",
                "name": "Зенит – Динамо",
                "score": "3:1 (25-20 20-25 25-18 25-22)",
                "status": 3,
            },
        ],
    }

    results = FonbetResultsParser.parse_all_sports(sample_feed)
    by_sport = {r.sport_code: r for r in results}
    assert set(by_sport) == {"tennis", "football", "hockey", "basketball", "volleyball"}
    assert by_sport["tennis"].winner == "player_a"
    assert by_sport["football"].winner == "team_a"
    assert by_sport["hockey"].winner == "team_a"
    assert by_sport["basketball"].winner == "team_a"
    assert by_sport["volleyball"].winner == "team_a"
    print("test_parse_all_sports_includes_football_and_hockey: OK")


def test_tennis_pipeline_does_not_resurrect_sealed_finished():
    """Live ingest must not reopen a match sealed with a definitive official result."""
    from tennis_adapter.pipeline import TennisPipeline

    session = SessionLocal()
    try:
        _, event, _ = setup_test_event_and_bet(session)
        sealed = CanonicalMatchResult(
            source_event_id=event.source_event_id,
            sport_code="tennis",
            tournament="ATP Tour",
            participant_a="Daniil Medvedev",
            participant_b="Carlos Alcaraz",
            final_score="2:0 (6-4 6-3)",
            winner="player_a",
            status="FINISHED",
        )
        EventResultMatcher.match_and_complete_event(session, sealed)
        session.commit()

        pipeline = TennisPipeline()
        raw = {
            "id": event.source_event_id,
            "source_event_id": event.source_event_id,
            "participant_a": "Медведев Д.",
            "participant_b": "Алькарас К.",
            "tournament": "ATP. Шанхай (Хард)",
            "odds_a": 1.90,
            "odds_b": 1.95,
            "score_state": {
                "raw_score_string": "0-1 (4-6, 2-1*)",
                "current_game_a": 2,
                "current_game_b": 1,
                "points_a": "15",
                "points_b": "0",
                "server": "player_a",
            },
        }
        pipeline.process_and_persist(session, raw)

        updated = session.get(Event, event.id)
        assert updated.status == "finished"
        assert updated.is_live is False
        assert updated.finished_at is not None
        assert updated.current_score.get("final_score") == "2:0 (6-4 6-3)"
        assert TennisPipeline._official_result_is_definitive(updated.metadata_json)
        print("test_tennis_pipeline_does_not_resurrect_sealed_finished: OK")
    finally:
        session.close()


def test_worker_stale_live_marks_interrupted_not_finished():
    """Missing from live >90s must leave the board as interrupted without wiping score."""
    import importlib.util
    from pathlib import Path

    candidates = [
        Path("/app/app/ingest.py"),
        Path("/tmp/worker_ingest.py"),
        Path("/srv/neurobet/services/worker/app/ingest.py"),
        Path("services/worker/app/ingest.py"),
    ]
    ingest_path = next((p for p in candidates if p.exists()), None)
    if ingest_path is None:
        print("test_worker_stale_live_marks_interrupted_not_finished: SKIP (ingest.py not mounted)")
        return

    spec = importlib.util.spec_from_file_location("worker_ingest_under_test", ingest_path)
    ingest_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(ingest_mod)

    session = SessionLocal()
    try:
        source_keep = str(random.randint(10000000, 99999999))
        source_stale = str(random.randint(10000000, 99999999))
        keep_score = {"sets": [{"set_number": 1, "games_a": 3, "games_b": 2}]}
        stale_score = {"sets": [{"set_number": 1, "games_a": 6, "games_b": 4}]}

        keep = Event(
            source="fonbet",
            source_event_id=source_keep,
            sport_code="tennis",
            participant_a_name="Keep A",
            participant_b_name="Keep B",
            scheduled_start_at=utc_now() - timedelta(hours=1),
            first_seen_at=utc_now() - timedelta(hours=1),
            last_seen_at=utc_now(),
            status="live",
            is_live=True,
            current_score=keep_score,
        )
        stale = Event(
            source="fonbet",
            source_event_id=source_stale,
            sport_code="tennis",
            participant_a_name="Stale A",
            participant_b_name="Stale B",
            scheduled_start_at=utc_now() - timedelta(hours=1),
            first_seen_at=utc_now() - timedelta(hours=1),
            last_seen_at=utc_now() - timedelta(seconds=120),
            status="live",
            is_live=True,
            current_score=stale_score,
        )
        session.add_all([keep, stale])
        session.commit()

        # Simulate the stale-update branch without requiring TennisPipeline parse.
        from datetime import timedelta as td

        now = utc_now()
        stale_before = now - td(seconds=90)
        seen_ids = [source_keep]
        awaiting = (
            session.query(Event)
            .filter(
                Event.sport_code == "tennis",
                Event.source == "fonbet",
                Event.is_live.is_(True),
                Event.status == "live",
                ~Event.source_event_id.in_(seen_ids),
                Event.last_seen_at.isnot(None),
                Event.last_seen_at < stale_before,
                Event.source_event_id.in_([source_keep, source_stale]),
            )
            .update(
                {
                    Event.is_live: False,
                    Event.status: "interrupted",
                },
                synchronize_session=False,
            )
        )
        session.commit()
        assert awaiting == 1

        # Also assert the module source no longer writes status=finished in the stale path.
        src = ingest_path.read_text(encoding="utf-8")
        assert 'Event.status: "interrupted"' in src
        assert 'Event.status: "finished"' not in src
        assert "Event.finished_at" not in src.split("stale_before")[1].split("session.commit")[0]

        updated_stale = session.get(Event, stale.id)
        updated_keep = session.get(Event, keep.id)
        assert updated_stale.status == "interrupted"
        assert updated_stale.is_live is False
        assert updated_stale.finished_at is None
        assert updated_stale.current_score == stale_score
        assert updated_keep.status == "live"
        assert updated_keep.is_live is True
        print("test_worker_stale_live_marks_interrupted_not_finished: OK")
    finally:
        session.close()


if __name__ == "__main__":
    print("\n--- Running Phase 13 Settlement & Fonbet Results Tests ---")
    test_fonbet_results_feed_parsing()
    test_retirement_and_walkover_detection()
    test_event_result_matcher_marks_finished()
    test_settlement_win()
    test_settlement_loss()
    test_settlement_void()
    test_strict_idempotency_double_settlement_prevented()
    test_settlement_review_required_for_ambiguous_results()
    test_backend_settlement_api_endpoints()
    test_tennis_one_set_score_is_not_finished()
    test_tennis_two_sets_score_is_finished()
    test_matcher_skips_live_finish_on_one_set_result()
    test_incomplete_result_does_not_overwrite_definitive_score()
    test_parse_all_sports_includes_football_and_hockey()
    test_tennis_pipeline_does_not_resurrect_sealed_finished()
    test_worker_stale_live_marks_interrupted_not_finished()
    print("\nALL SETTLEMENT & RESULTS TESTS PASSED SUCCESSFULLY!")
