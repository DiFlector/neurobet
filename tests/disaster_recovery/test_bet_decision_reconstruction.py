"""
Test Bet Decision Provenance Reconstruction for Phase 31: Disaster Recovery.
Validates AGENTS.md Rule 8 (Reproducibility & Audit Trail):
Any bet in history must answer: "Why did Neurobet make this bet at this exact moment?"
Reconstructs the full forensic lineage given only `bet.id`:
1. Event state and participants
2. Feature vector snapshot at decision time
3. ML Prediction (model version, probability, fair odds, edge)
4. Research Document sources
5. LLM Decision (model version, verdict, reasoning, risk flags, raw JSON)
6. Bet Proposal parameters
7. Bet Manager validation checks (passed / failed gatekeeper policies)
8. Virtual Account state & Financial Ledger debit
9. Bet Settlement & Financial Ledger credit/closure
"""

import uuid
from datetime import datetime, timezone, timedelta
from db.connection import SessionLocal
from db.models.sports import Sport
from db.models.events import Event
from db.models.predictions import FeatureSnapshot, MLPrediction, LLMDecision
from db.models.research import WebResearchRun, WebDocument
from db.models.betting import VirtualAccount, LedgerEntry, Bet, BetProposal, BetValidationResult, BetSettlement
from bankroll import BankrollService

def get_or_create_sport(session, code="tennis"):
    sport = session.get(Sport, code)
    if not sport:
        sport = Sport(code=code, name=code.capitalize(), is_primary=True)
        session.add(sport)
        session.flush()
    return sport

def test_bet_decision_reconstruction():
    print("============================================================")
    print("🧪 TEST: BET DECISION RECONSTRUCTION FROM AUDIT RECORDS")
    print("============================================================")

    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        # 1. Setup Sport & Event
        get_or_create_sport(session, "tennis")
        event = Event(
            source="fonbet",
            source_event_id=f"dr_audit_evt_{uuid.uuid4().hex[:8]}",
            sport_code="tennis",
            participant_a_name="Carlos Alcaraz",
            participant_b_name="Daniil Medvedev",
            metadata_json={"tournament": "US Open - Semifinals"},
            scheduled_start_at=now - timedelta(hours=2),
            status="completed",
            is_live=False,
            current_score={"sets": [6, 4], "games": [4, 6], "current": "completed"},
        )
        session.add(event)
        session.flush()

        # 2. Point-in-time Feature Snapshot
        feat_snapshot = FeatureSnapshot(
            observed_at=now - timedelta(hours=1, minutes=30),
            event_id=event.id,
            sport_code="tennis",
            feature_version="2.1.0",
            features={
                "first_serve_pct_a": 0.68,
                "first_serve_pct_b": 0.61,
                "break_points_saved_ratio_a": 0.75,
                "h2h_win_ratio_a": 0.60,
                "surface_rating_diff": 12.5,
            },
        )
        session.add(feat_snapshot)
        session.flush()

        # 3. Numeric ML Prediction
        ml_pred = MLPrediction(
            event_id=event.id,
            sport_code="tennis",
            model_version="tennis_gradient_boosting_v1.0.0",
            market="match_winner",
            predicted_outcome="Carlos Alcaraz",
            model_probability=0.6400,
            bookmaker_odds=1.8500,
            fair_odds=1.5625,
            edge=0.0995,
            expected_value=0.1840,
            confidence=0.8200,
            has_positive_edge=True,
            is_shadow=False,
            outcomes_detail={"Carlos Alcaraz": 0.64, "Daniil Medvedev": 0.36},
            created_at=now - timedelta(hours=1, minutes=29),
        )
        session.add(ml_pred)
        session.flush()

        # 4. Web Research Run & Document
        res_run = WebResearchRun(
            event_id=event.id,
            query="Carlos Alcaraz form injury US Open 2026",
            status="completed",
            documents_found=1,
            created_at=now - timedelta(hours=1, minutes=35),
        )
        session.add(res_run)
        session.flush()

        web_doc = WebDocument(
            run_id=res_run.id,
            url="https://tennisworld.com/news/alcaraz-ready-for-semis",
            title="Alcaraz fully recovered and confident ahead of semifinal",
            source_domain="tennisworld.com",
            content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            published_at=now - timedelta(hours=3),
            fetched_at=now - timedelta(hours=1, minutes=35),
            raw_storage_path="snapshots/news/alcaraz_semis.html",
        )
        session.add(web_doc)
        session.flush()

        # 5. Local LLM Decision
        llm_decision = LLMDecision(
            event_id=event.id,
            llm_version="qwen2.5-coder-7b-instruct-q4",
            verdict="BET",
            confidence_adjustment=0.05,
            injury_risk="low",
            fatigue_risk="low",
            reasoning="Strong medical form, excellent H2H matchup on hard court, no signs of muscular fatigue.",
            raw_json={
                "verdict": "BET",
                "edge_confirmed": True,
                "stake_scale": 1.0,
                "reason_codes": ["FORM_ADVANTAGE", "H2H_POSITIVE"],
            },
            created_at=now - timedelta(hours=1, minutes=28),
        )
        session.add(llm_decision)
        session.flush()

        # 6. Bet Proposal
        proposal = BetProposal(
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="Carlos Alcaraz",
            selection_id=uuid.uuid4(),
            bookmaker_odds=1.8500,
            fair_odds=1.5625,
            model_probability=0.6400,
            edge=0.0995,
            suggested_stake=5000.00,
            ml_prediction_id=ml_pred.id,
            llm_decision_id=llm_decision.id,
            created_at=now - timedelta(hours=1, minutes=27),
        )
        session.add(proposal)
        session.flush()

        # 7. Bet Manager Validation Gate
        validation = BetValidationResult(
            proposal_id=proposal.id,
            is_accepted=True,
            rejection_code=None,
            checks_passed=[
                "EVENT_EXISTS_AND_ACTIVE",
                "ODDS_NOT_STALE",
                "BANKROLL_SUFFICIENT",
                "RISK_LIMITS_OK",
                "LLM_VERDICT_CONFIRMED",
                "SCHEMA_CONTRACT_VALID",
            ],
            checks_failed=[],
            created_at=now - timedelta(hours=1, minutes=26),
        )
        session.add(validation)
        session.flush()

        # 8. Virtual Account & Bet Execution
        acc_name = f"dr_forensic_acc_{uuid.uuid4().hex[:8]}"
        account = BankrollService.get_or_create_account(session, name=acc_name, initial_balance=100000.00)
        session.commit()

        bet = BankrollService.place_bet(
            session=session,
            account_id=account.id,
            proposal_id=proposal.id,
            event_id=event.id,
            sport_code="tennis",
            market="match_winner",
            outcome="Carlos Alcaraz",
            odds=1.85,
            stake=5000.00,
        )
        session.commit()

        # 9. Settlement & Payout
        BankrollService.settle_bet(session, bet.id, "WON", reason="MATCH_COMPLETED_NORMAL")
        session.commit()

        target_bet_id = bet.id
        print(f"[+] Setup completed. Auditing registered bet: {target_bet_id}")

    # =========================================================================
    # FORENSIC AUDIT RECONSTRUCTION TEST
    # Given ONLY `target_bet_id`, reconstruct the entire rationale and timeline.
    # =========================================================================
    with SessionLocal() as session:
        print("[*] Initiating forensic audit reconstruction from bet.id...")

        # 1. Fetch Bet
        reconstructed_bet = session.get(Bet, target_bet_id)
        assert reconstructed_bet is not None, "Target bet not found in database!"
        assert float(reconstructed_bet.stake) == 5000.00
        assert float(reconstructed_bet.odds) == 1.85
        assert reconstructed_bet.status == "WON"

        # 2. Reconstruct Event Context
        rec_event = session.get(Event, reconstructed_bet.event_id)
        assert rec_event is not None
        assert rec_event.participant_a_name == "Carlos Alcaraz"
        assert rec_event.participant_b_name == "Daniil Medvedev"
        assert rec_event.metadata_json.get("tournament") == "US Open - Semifinals"
        print(f"[+] Reconstructed Event: {rec_event.participant_a_name} vs {rec_event.participant_b_name}")

        # 3. Reconstruct Bet Proposal & Gate Validation
        rec_proposal = session.get(BetProposal, reconstructed_bet.proposal_id)
        assert rec_proposal is not None
        rec_validation = session.query(BetValidationResult).filter_by(proposal_id=rec_proposal.id).one()
        assert rec_validation.is_accepted is True
        assert "LLM_VERDICT_CONFIRMED" in rec_validation.checks_passed
        print(f"[+] Reconstructed Gate Validation: Accepted={rec_validation.is_accepted}, Passed {len(rec_validation.checks_passed)} checks")

        # 4. Reconstruct ML Prediction & Probabilities
        assert rec_proposal.ml_prediction_id is not None
        rec_ml = session.get(MLPrediction, rec_proposal.ml_prediction_id)
        assert rec_ml is not None
        assert rec_ml.model_version == "tennis_gradient_boosting_v1.0.0"
        assert float(rec_ml.model_probability) == 0.6400
        assert float(rec_ml.fair_odds) == 1.5625
        assert float(rec_ml.edge) == 0.0995
        print(f"[+] Reconstructed ML Model: {rec_ml.model_version} (P={rec_ml.model_probability}, Edge={rec_ml.edge})")

        # 5. Reconstruct Feature Snapshot at inference time
        rec_feat = session.query(FeatureSnapshot).filter_by(event_id=rec_event.id).first()
        assert rec_feat is not None
        assert rec_feat.feature_version == "2.1.0"
        assert rec_feat.features["first_serve_pct_a"] == 0.68
        print(f"[+] Reconstructed Feature Snapshot: Version {rec_feat.feature_version}, {len(rec_feat.features)} features")

        # 6. Reconstruct Qualitative LLM Verdict
        assert rec_proposal.llm_decision_id is not None
        rec_llm = session.get(LLMDecision, rec_proposal.llm_decision_id)
        assert rec_llm is not None
        assert rec_llm.verdict == "BET"
        assert rec_llm.llm_version == "qwen2.5-coder-7b-instruct-q4"
        assert rec_llm.injury_risk == "low"
        assert "hard court" in rec_llm.reasoning
        print(f"[+] Reconstructed LLM Verdict: {rec_llm.verdict} ({rec_llm.llm_version}), Reasoning: {rec_llm.reasoning[:50]}...")

        # 7. Reconstruct Financial Ledger Journey
        ledger_entries = session.query(LedgerEntry).filter_by(bet_id=reconstructed_bet.id).order_by(LedgerEntry.created_at.asc()).all()
        assert len(ledger_entries) == 2  # BET_PLACED (-5,000) and BET_WIN (+9,250)
        assert ledger_entries[0].entry_type == "BET_PLACED"
        assert float(ledger_entries[0].amount) == -5000.00
        assert ledger_entries[1].entry_type == "BET_WIN"
        assert float(ledger_entries[1].amount) == 9250.00
        print(f"[+] Reconstructed Financial Ledger: 2 immutable entries (Debit: {ledger_entries[0].amount}, Credit: {ledger_entries[1].amount})")

        # 8. Reconstruct Settlement
        settlement = session.query(BetSettlement).filter_by(bet_id=reconstructed_bet.id).one()
        assert settlement.status == "WON"
        assert float(settlement.payout) == 9250.00
        assert float(settlement.net_profit) == 4250.00
        print(f"[+] Reconstructed Settlement: Payout={settlement.payout} RUB, Net Profit={settlement.net_profit} RUB")

        # 9. Reconstruct Research Source Evidence
        rec_research = session.query(WebDocument).join(WebResearchRun).filter(WebResearchRun.event_id == rec_event.id).first()
        assert rec_research is not None
        assert "alcaraz" in rec_research.url.lower()
        print(f"[+] Reconstructed Research Source: {rec_research.source_domain} -> {rec_research.title}")

    print("============================================================")
    print("✅ TEST BET DECISION RECONSTRUCTION: PASSED (100% Provenance)")
    print("============================================================")

if __name__ == "__main__":
    test_bet_decision_reconstruction()
