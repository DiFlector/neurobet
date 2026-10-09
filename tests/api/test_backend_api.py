"""Test suite for Phase 18: Backend API.
Verifies all REST and SSE endpoints specified in Architecture Section 31 and Roadmap Phase 18:
- Health & readiness
- Sports descriptors
- Events, search, filters & pagination
- Event timeline & odds history (TimescaleDB)
- ML predictions & web research
- Bets, proposals, virtual bankroll & ledger
- Portfolio performance (PnL, ROI, Win Rate, Max Drawdown)
- ML Model registry & training runs
- Simulation reset
- Server-Sent Events (SSE) live stream
"""

import uuid
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from db.connection import SessionLocal
from db.models.events import Event, EventStateSnapshot
from db.models.sports import Sport
from db.models.odds import Market, MarketSelection, OddsSnapshot
from db.models.predictions import MLPrediction
from db.models.research import WebResearchRun, WebEvidence, WebDocument
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal, BetSettlement
from db.models.ml_registry import ModelVersion, TrainingRun
from app.main import app

client = TestClient(app)


def _ensure_sport(session):
    sport = session.query(Sport).filter(Sport.code == "tennis").first()
    if not sport:
        sport = Sport(code="tennis", name="Tennis", is_primary=True)
        session.add(sport)
        session.flush()


def test_health_and_readiness():
    """Verify health & readiness checks reporting database connectivity."""
    resp1 = client.get("/health")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "healthy"

    resp2 = client.get("/api/health")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["primary_sport"] == "tennis"
    print("✓ test_health_and_readiness passed")


def test_sports_endpoints():
    """Verify supported sports listing and detail lookup."""
    resp = client.get("/api/sports")
    assert resp.status_code == 200
    sports = resp.json()
    assert isinstance(sports, list)
    assert any(s["code"] == "tennis" for s in sports)

    # Specific sport detail
    resp_tennis = client.get("/api/sports/tennis")
    assert resp_tennis.status_code == 200
    t_data = resp_tennis.json()
    assert t_data["code"] == "tennis"
    assert t_data["is_primary"] is True

    # Unsupported sport cleanly handled
    resp_unsupported = client.get("/api/sports/curling")
    assert resp_unsupported.status_code == 200
    assert resp_unsupported.json()["supported"] is False
    print("✓ test_sports_endpoints passed")


def test_events_and_filtering_pagination():
    """Verify events list, filters (is_live, status, search), pagination, and detail views."""
    event_live_id = uuid.uuid4()
    event_pre_id = uuid.uuid4()

    with SessionLocal() as session:
        _ensure_sport(session)
        evt_live = Event(
            id=event_live_id,
            source="fonbet",
            source_event_id=f"ext_{event_live_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Alcaraz Carlos",
            participant_b_name="Sinner Jannik",
            scheduled_start_at=datetime.now(timezone.utc),
            status="live",
            is_live=True,
            current_score={"sets": "1:0", "games": "3-2"},
            quality_score=98.5,
        )
        evt_pre = Event(
            id=event_pre_id,
            source="fonbet",
            source_event_id=f"ext_{event_pre_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Medvedev Daniil",
            participant_b_name="Zverev Alexander",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
            is_live=False,
            quality_score=100.0,
        )
        session.add(evt_live)
        session.add(evt_pre)
        session.commit()

    # 1. Query with is_live=true
    resp_live = client.get("/api/events?is_live=true&limit=10")
    assert resp_live.status_code == 200
    data_live = resp_live.json()
    assert data_live["total"] >= 1
    assert any(e["id"] == str(event_live_id) for e in data_live["events"])
    assert all(e["is_live"] is True for e in data_live["events"])

    # 2. Query with search filter
    resp_search = client.get("/api/events?search=Alcaraz")
    assert resp_search.status_code == 200
    search_data = resp_search.json()
    assert any("Alcaraz" in e["participant_a"] for e in search_data["events"])

    # 3. Event detail
    resp_detail = client.get(f"/api/events/{event_live_id}")
    assert resp_detail.status_code == 200
    detail = resp_detail.json()
    assert detail["id"] == str(event_live_id)
    assert detail["participant_a"] == "Alcaraz Carlos"
    assert detail["current_score"]["sets"] == "1:0"

    # 4. 404 for unknown event
    resp_404 = client.get(f"/api/events/{uuid.uuid4()}")
    assert resp_404.status_code == 404
    print("✓ test_events_and_filtering_pagination passed")


def test_event_timeline_and_odds_history():
    """Verify chronological event state timeline and odds movements endpoints."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Rublev Andrey",
            participant_b_name="Tsitsipas Stefanos",
            scheduled_start_at=datetime.now(timezone.utc),
            status="live",
            is_live=True,
        )
        session.add(evt)

        # Timeline snapshots
        snap1 = EventStateSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            source_event_id=evt.source_event_id,
            sport_code="tennis",
            status="live",
            current_period=1,
            score="1-0",
            observed_at=datetime.now(timezone.utc),
        )
        snap2 = EventStateSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            source_event_id=evt.source_event_id,
            sport_code="tennis",
            status="live",
            current_period=1,
            score="2-0",
            observed_at=datetime.now(timezone.utc),
        )
        session.add(snap1)
        session.add(snap2)

        # Odds snapshots
        odds1 = OddsSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            market_id=uuid.uuid4(),
            selection_id=uuid.uuid4(),
            market_type="match_winner",
            outcome="player_a",
            odds=1.80,
            probability_implied=0.5555,
            observed_at=datetime.now(timezone.utc),
        )
        odds2 = OddsSnapshot(
            id=uuid.uuid4(),
            event_id=event_id,
            market_id=uuid.uuid4(),
            selection_id=uuid.uuid4(),
            market_type="match_winner",
            outcome="player_a",
            odds=1.65,
            probability_implied=0.6060,
            observed_at=datetime.now(timezone.utc),
        )
        session.add(odds1)
        session.add(odds2)
        session.commit()

    # Timeline endpoint
    resp_timeline = client.get(f"/api/events/{event_id}/timeline")
    assert resp_timeline.status_code == 200
    timeline = resp_timeline.json()
    assert len(timeline) >= 2
    assert timeline[0]["score"] == "1-0"

    # Odds endpoint
    resp_odds = client.get(f"/api/events/{event_id}/odds?market_type=match_winner")
    assert resp_odds.status_code == 200
    odds_items = resp_odds.json()
    assert len(odds_items) >= 2
    assert odds_items[0]["odds"] == 1.80
    assert odds_items[1]["odds"] == 1.65
    print("✓ test_event_timeline_and_odds_history passed")


def test_predictions_and_research():
    """Verify ML predictions, web research evidence, and research trigger endpoints."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Rune Holger",
            participant_b_name="Ruud Casper",
            scheduled_start_at=datetime.now(timezone.utc),
            status="prematch",
        )
        session.add(evt)

        # ML Prediction
        pred = MLPrediction(
            id=uuid.uuid4(),
            event_id=event_id,
            sport_code="tennis",
            model_version="tennis_v1.0",
            market="match_winner",
            predicted_outcome="player_a",
            model_probability=0.64,
            bookmaker_odds=1.90,
            fair_odds=1.5625,
            edge=0.1137,
            expected_value=0.1137,
            confidence=0.78,
            has_positive_edge=True,
        )
        session.add(pred)
        session.flush()

        # Research Document & Evidence
        run = WebResearchRun(
            id=uuid.uuid4(),
            event_id=event_id,
            query="Rune vs Ruud injury form",
            status="completed",
            documents_found=1,
        )
        session.add(run)
        session.flush()

        doc = WebDocument(
            id=uuid.uuid4(),
            run_id=run.id,
            url="https://tennis.com/rune-preview",
            title="Rune preview",
            source_domain="tennis.com",
            content_hash="hash123",
        )
        session.add(doc)
        session.flush()

        evidence = WebEvidence(
            id=uuid.uuid4(),
            document_id=doc.id,
            event_id=event_id,
            snippet="Rune reported feeling 100% physically recovered.",
            relevance_score=0.95,
            freshness_score=0.90,
        )
        session.add(evidence)
        session.commit()

    # Predictions
    resp_preds = client.get(f"/api/events/{event_id}/predictions")
    assert resp_preds.status_code == 200
    preds = resp_preds.json()
    assert len(preds) >= 1
    assert preds[0]["predicted_outcome"] == "player_a"
    assert preds[0]["edge"] > 0.10

    # Research evidence
    resp_research = client.get(f"/api/events/{event_id}/research")
    assert resp_research.status_code == 200
    ev_list = resp_research.json()
    assert len(ev_list) >= 1
    assert "physically recovered" in ev_list[0]["snippet"]

    # Trigger research
    resp_run = client.post("/api/research/run", json={"event_id": str(event_id), "query": "Rune form"})
    assert resp_run.status_code == 200
    assert resp_run.json()["status"] == "success"
    print("✓ test_predictions_and_research passed")


def test_bankroll_bets_proposals_and_performance():
    """Verify account, ledger, bets, proposals, and portfolio performance calculations."""
    event_id = uuid.uuid4()
    with SessionLocal() as session:
        _ensure_sport(session)
        from bankroll import BankrollService
        account = BankrollService.get_or_create_account(session)
        session.flush()

        evt = Event(
            id=event_id,
            source="fonbet",
            source_event_id=f"ext_{event_id.hex[:8]}",
            sport_code="tennis",
            participant_a_name="Dimitrov G.",
            participant_b_name="Tiafoe F.",
            scheduled_start_at=datetime.now(timezone.utc),
            status="finished",
        )
        session.add(evt)
        session.flush()

        # Proposal
        prop = BetProposal(
            id=uuid.uuid4(),
            event_id=event_id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            selection_id=uuid.uuid4(),
            bookmaker_odds=2.00,
            fair_odds=1.70,
            model_probability=0.5882,
            edge=0.0882,
            suggested_stake=1000.0,
        )
        session.add(prop)
        session.flush()

        # Placed & Settled WON bet
        bet = Bet(
            id=uuid.uuid4(),
            account_id=account.id,
            proposal_id=prop.id,
            event_id=event_id,
            sport_code="tennis",
            market="match_winner",
            outcome="player_a",
            odds=2.00,
            stake=1000.0,
            potential_payout=2000.0,
            status="WON",
        )
        session.add(bet)
        session.flush()
        bet_id = str(bet.id)

        settlement = BetSettlement(
            id=uuid.uuid4(),
            bet_id=bet.id,
            status="WON",
            payout=2000.0,
            net_profit=1000.0,
        )
        session.add(settlement)
        session.commit()

    # Account
    resp_acc = client.get("/api/account")
    assert resp_acc.status_code == 200
    assert "balance" in resp_acc.json()

    # Bets list and detail
    resp_bets = client.get("/api/bets?status=WON")
    assert resp_bets.status_code == 200
    bets = resp_bets.json()
    assert len(bets) >= 1

    resp_bet_detail = client.get(f"/api/bets/{bet_id}")
    assert resp_bet_detail.status_code == 200
    b_detail = resp_bet_detail.json()
    assert b_detail["id"] == bet_id
    assert b_detail["settlement"]["status"] == "WON"
    assert b_detail["settlement"]["net_profit"] == 1000.0

    # Proposals
    resp_props = client.get(f"/api/proposals?event_id={event_id}")
    assert resp_props.status_code == 200
    assert len(resp_props.json()) >= 1

    # Portfolio Performance
    resp_perf = client.get("/api/performance")
    assert resp_perf.status_code == 200
    perf = resp_perf.json()
    assert perf["total_bets"] >= 1
    assert perf["won_bets"] >= 1
    assert "win_rate" in perf
    assert "net_pnl" in perf
    assert "roi" in perf
    assert "max_drawdown" in perf
    print("✓ test_bankroll_bets_proposals_and_performance passed")


def test_models_registry_and_training_runs():
    """Verify ML model versions and training runs endpoints."""
    with SessionLocal() as session:
        mv = ModelVersion(
            id=uuid.uuid4(),
            model_name="tennis_gbdt",
            version_tag=f"v_test_{uuid.uuid4().hex[:6]}",
            sport_code="tennis",
            market="match_winner",
            artifact_path="/models/checkpoints/tennis_gbdt.joblib",
            metrics={"brier_score": 0.18, "roc_auc": 0.76},
            hyperparameters={"learning_rate": 0.05, "max_depth": 5},
            status="ACTIVE",
        )
        session.add(mv)
        session.flush()

        tr = TrainingRun(
            id=uuid.uuid4(),
            model_version_id=mv.id,
            sport_code="tennis",
            status="completed",
            metrics_train={"loss": 0.42},
            metrics_val={"loss": 0.48},
        )
        session.add(tr)
        session.commit()

    resp_models = client.get("/api/models")
    assert resp_models.status_code == 200
    models = resp_models.json()
    assert len(models) >= 1
    assert any(m["model_name"] == "tennis_gbdt" for m in models)

    resp_runs = client.get("/api/training/runs")
    assert resp_runs.status_code == 200
    runs = resp_runs.json()
    assert len(runs) >= 1

    resp_train = client.post("/api/models/train", json={"sport_code": "tennis"})
    assert resp_train.status_code == 200
    assert resp_train.json()["status"] == "queued"
    print("✓ test_models_registry_and_training_runs passed")


def test_simulation_reset_endpoint():
    """Verify /api/simulation/reset resets virtual bankroll to initial deposit."""
    resp = client.post("/api/simulation/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["new_balance"] == 100000.0
    assert data["locked_exposure"] == 0.0
    print("✓ test_simulation_reset_endpoint passed")


def test_sse_live_streaming():
    """Verify Server-Sent Events /api/live/stream broadcasts heartbeat frames."""
    resp = client.get("/api/live/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    content = resp.text
    assert "event: connected" in content
    assert "event: heartbeat" in content
    print("✓ test_sse_live_streaming passed")


if __name__ == "__main__":
    test_health_and_readiness()
    test_sports_endpoints()
    test_events_and_filtering_pagination()
    test_event_timeline_and_odds_history()
    test_predictions_and_research()
    test_bankroll_bets_proposals_and_performance()
    test_models_registry_and_training_runs()
    test_simulation_reset_endpoint()
    test_sse_live_streaming()
    print("\n🎉 ALL PHASE 18 BACKEND API TESTS PASSED SUCCESSFULLY!")
