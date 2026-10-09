"""ML + LLM Decision Layer: Candidate Selection, Context Enrichment, and Strategy Evaluation."""

from datetime import datetime, timezone, timedelta
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from contracts import (
    CandidateItem,
    DecisionPipelineConfig,
    DecisionResult,
    LLMAnalysisInput,
    LLMConstraints,
    LLMEventContext,
    LLMMarketContext,
    LLMMLContext,
    LLMResearchItem,
    LLMStructuredVerdict,
    StrategyComparisonReport,
    utc_now,
)
from db.models.betting import BetProposal, Bet
from db.models.events import Event
from db.models.predictions import LLMDecision

logger = logging.getLogger("bankroll.decision_pipeline")


class CandidateSelector:
    """
    Evaluates raw sporting events and market odds against ML predictions.
    Filters out options without statistical value early to avoid redundant network/LLM costs.
    """

    @staticmethod
    def evaluate_candidate(
        event_id: str,
        sport_code: str,
        participant_a: str,
        participant_b: str,
        tournament: Optional[str],
        market: str,
        selection: str,
        odds: float,
        model_probability: float,
        confidence: float,
        model_version: str,
        min_edge: float = 0.03,
        min_confidence: float = 0.50,
    ) -> CandidateItem:
        """
        Calculates implied market probability, edge, and determines shortlist qualification.
        """
        if odds <= 1.0:
            market_probability = 1.0
            edge = -1.0
        else:
            market_probability = 1.0 / odds
            edge = model_probability - market_probability

        is_shortlisted = (edge >= min_edge) and (confidence >= min_confidence)

        return CandidateItem(
            event_id=event_id,
            sport_code=sport_code,
            participant_a=participant_a,
            participant_b=participant_b,
            tournament=tournament,
            market=market,
            selection=selection,
            odds=odds,
            model_probability=round(model_probability, 4),
            market_probability=round(market_probability, 4),
            edge=round(edge, 4),
            confidence=round(confidence, 4),
            model_version=model_version,
            is_shortlisted=is_shortlisted,
        )

    @staticmethod
    def shortlist_candidates(
        candidates: List[CandidateItem],
        config: Optional[DecisionPipelineConfig] = None,
    ) -> List[CandidateItem]:
        """Filters list of CandidateItem instances based on min_edge and min_confidence."""
        min_edge = config.min_edge if config else 0.03
        min_conf = config.min_confidence if config else 0.50
        shortlisted = []
        for c in candidates:
            if (c.edge >= min_edge) and (c.confidence >= min_conf):
                shortlisted.append(c if c.is_shortlisted else c.model_copy(update={"is_shortlisted": True}))
        return shortlisted


class CombinedDecisionPipeline:
    """
    Coordinates candidate selection, optional research enrichment, local LLM evaluation,
    database audit persistence, and BetProposal generation strictly for bet-manager.
    """

    def __init__(self, config: Optional[DecisionPipelineConfig] = None):
        self.config = config or DecisionPipelineConfig()

    def process_candidate(
        self,
        session: Session,
        candidate: CandidateItem,
        research_snippets: Optional[List[Dict[str, str]]] = None,
        llm_client: Optional[Any] = None,
    ) -> DecisionResult:
        """
        Processes an evaluated candidate through the decision pipeline.
        - If not shortlisted -> FILTERED_OUT early.
        - If LLM disabled -> PROPOSED via ML-only.
        - If LLM enabled -> queries LLM, records llm_decisions, filters red flags.
        """
        # 1. Reject non-shortlisted candidates early
        if not candidate.is_shortlisted:
            return DecisionResult(
                candidate=candidate,
                status="FILTERED_OUT",
                reason=f"Candidate edge {candidate.edge:.2%} or confidence {candidate.confidence:.2f} below hurdle",
            )

        # 2. Check if LLM is enabled
        if not self.config.llm_enabled:
            # ML-Only Mode: create proposal directly without calling LLM
            proposal = self._create_proposal(session, candidate, llm_decision_id=None)
            return DecisionResult(
                candidate=candidate,
                status="PROPOSED",
                proposal_id=str(proposal.id),
                reason="Approved by ML-Only strategy (LLM disabled in config)",
            )

        # 3. ML + LLM Mode: Enrich with qualitative context
        snippets = research_snippets or []
        llm_input = LLMAnalysisInput(
            event=LLMEventContext(
                sport=candidate.sport_code,
                league=candidate.tournament,
                home=candidate.participant_a,
                away=candidate.participant_b,
                status="live",
            ),
            market=LLMMarketContext(
                type=candidate.market,
                selection=candidate.selection,
                odds=candidate.odds,
            ),
            ml=LLMMLContext(
                probability=candidate.model_probability,
                market_probability=candidate.market_probability,
                edge=candidate.edge,
                confidence=candidate.confidence,
                model_version=candidate.model_version,
            ),
            research=[
                LLMResearchItem(
                    title=s.get("title", "News"),
                    domain=s.get("domain", "sports.com"),
                    snippet=s.get("snippet", ""),
                )
                for s in snippets
            ],
            constraints=LLMConstraints(
                max_stake_fraction=self.config.max_stake_fraction,
                simulation_only=True,
            ),
        )

        # 4. Query LLM
        verdict = self._query_llm(llm_input, llm_client)

        # 5. Persist record to PostgreSQL llm_decisions table
        llm_decision_record = self._persist_llm_decision(session, candidate, verdict)

        # 6. Apply qualitative gatekeeper decision
        if verdict.verdict == "BET":
            # Approved by LLM
            proposal = self._create_proposal(
                session, candidate, llm_decision_id=llm_decision_record.id, verdict=verdict
            )
            return DecisionResult(
                candidate=candidate,
                status="PROPOSED",
                llm_verdict=verdict.verdict,
                llm_decision_id=str(llm_decision_record.id),
                proposal_id=str(proposal.id),
                reason=f"Approved by ML+LLM: {verdict.summary}",
            )
        elif verdict.verdict == "NO_BET":
            # Suppressed by LLM due to injury, fatigue, or red flags
            return DecisionResult(
                candidate=candidate,
                status="LLM_REJECTED",
                llm_verdict=verdict.verdict,
                llm_decision_id=str(llm_decision_record.id),
                reason=f"Suppressed by LLM: {verdict.summary} (Codes: {', '.join(verdict.reason_codes)})",
            )
        else:
            # INSUFFICIENT_DATA
            if self.config.llm_required:
                return DecisionResult(
                    candidate=candidate,
                    status="LLM_REJECTED",
                    llm_verdict="INSUFFICIENT_DATA",
                    llm_decision_id=str(llm_decision_record.id),
                    reason="Suppressed: LLM returned INSUFFICIENT_DATA and LLM_REQUIRED is active",
                )
            else:
                # Fallback to ML-only
                proposal = self._create_proposal(session, candidate, llm_decision_id=llm_decision_record.id)
                return DecisionResult(
                    candidate=candidate,
                    status="PROPOSED",
                    llm_verdict="INSUFFICIENT_DATA",
                    llm_decision_id=str(llm_decision_record.id),
                    proposal_id=str(proposal.id),
                    reason="Fallback to ML-Only: LLM returned INSUFFICIENT_DATA but LLM_REQUIRED=False",
                )

    evaluate_candidate = process_candidate

    def _query_llm(self, llm_input: LLMAnalysisInput, llm_client: Optional[Any]) -> LLMStructuredVerdict:
        """Dispatches request to LLM client or fallback deterministic mock."""
        if llm_client and hasattr(llm_client, "analyze"):
            return llm_client.analyze(llm_input)

        # Check for injury keywords in research
        injury_keywords = ["травм", "боль", "снялся", "снялась", "injury", "retire"]
        has_injury = False
        for r in llm_input.research:
            if any(k in r.snippet.lower() for k in injury_keywords):
                has_injury = True
                break

        if has_injury:
            return LLMStructuredVerdict(
                verdict="NO_BET",
                market_type=llm_input.market.type,
                selection_id=llm_input.market.selection,
                confidence=0.35,
                stake_recommendation_fraction=0.0,
                reason_codes=["INJURY_RISK"],
                contradictions=["Player injury reported in qualitative context"],
                summary=f"Injury risk identified for {llm_input.market.selection}. Recommendation: NO_BET.",
                model_identifier="mock-llm-engine",
            )

        return LLMStructuredVerdict(
            verdict="BET",
            market_type=llm_input.market.type,
            selection_id=llm_input.market.selection,
            confidence=round(llm_input.ml.confidence, 2),
            stake_recommendation_fraction=min(0.015, self.config.max_stake_fraction),
            reason_codes=["MODEL_EDGE", "NO_NEGATIVE_SIGNALS"],
            summary=f"Qualitative analysis supports ML edge of {llm_input.ml.edge:.2%}.",
            model_identifier="mock-llm-engine",
        )

    def _persist_llm_decision(
        self, session: Session, candidate: CandidateItem, verdict: LLMStructuredVerdict
    ) -> LLMDecision:
        """Stores analytical output into PostgreSQL llm_decisions table."""
        # Convert event_id to UUID if string
        try:
            evt_uuid = uuid.UUID(candidate.event_id)
        except Exception:
            evt_uuid = uuid.uuid4()

        injury_risk = "high" if "INJURY_RISK" in verdict.reason_codes else "low"
        fatigue_risk = "high" if "SCHEDULE_FATIGUE" in verdict.reason_codes else "low"

        decision_row = LLMDecision(
            id=uuid.uuid4(),
            event_id=evt_uuid,
            llm_version=f"{verdict.model_identifier}:{verdict.prompt_version}",
            verdict=verdict.verdict,
            confidence_adjustment=round(verdict.confidence - candidate.confidence, 4),
            injury_risk=injury_risk,
            fatigue_risk=fatigue_risk,
            reasoning=verdict.summary[:1000],
            raw_json=verdict.model_dump(mode="json"),
            created_at=utc_now(),
        )
        session.add(decision_row)
        session.flush()
        return decision_row

    def _create_proposal(
        self,
        session: Session,
        candidate: CandidateItem,
        llm_decision_id: Optional[uuid.UUID] = None,
        verdict: Optional[LLMStructuredVerdict] = None,
    ) -> BetProposal:
        """Constructs BetProposal record for bet-manager validation."""
        try:
            evt_uuid = uuid.UUID(candidate.event_id)
        except Exception:
            evt_uuid = uuid.uuid4()

        fair_odds = round(1.0 / candidate.model_probability, 4) if candidate.model_probability > 0 else candidate.odds
        suggested_stake = 1000.0  # Base stake in simulation

        proposal = BetProposal(
            id=uuid.uuid4(),
            event_id=evt_uuid,
            sport_code=candidate.sport_code,
            market=candidate.market,
            outcome=candidate.selection,
            selection_id=uuid.uuid4(),
            bookmaker_odds=candidate.odds,
            fair_odds=fair_odds,
            model_probability=candidate.model_probability,
            edge=candidate.edge,
            suggested_stake=suggested_stake,
            llm_decision_id=llm_decision_id,
            created_at=utc_now(),
        )
        session.add(proposal)
        session.flush()
        return proposal


class LLMStrategyComparator:
    """
    Evaluates and compares ML-Only vs ML+LLM strategies on historical and simulation datasets.
    Computes incremental value, ROI delta, and false positive reduction.
    """

    @staticmethod
    def compare_strategies(
        candidates_with_outcomes: List[Dict[str, Any]],
    ) -> StrategyComparisonReport:
        """
        Accepts evaluated items with:
        {
          "candidate": CandidateItem,
          "ml_selected": bool,
          "llm_approved": bool,
          "actual_outcome": "WON" | "LOST" | "VOID",
          "stake": float,
          "odds": float
        }
        Returns StrategyComparisonReport.
        """
        total_candidates = len(candidates_with_outcomes)

        def is_ml(item: Dict[str, Any]) -> bool:
            return bool(item.get("ml_selected") or item.get("ml_only"))

        def is_llm(item: Dict[str, Any]) -> bool:
            return bool(item.get("llm_approved") or item.get("llm_accepted"))

        ml_only_bets = [item for item in candidates_with_outcomes if is_ml(item)]
        ml_llm_bets = [item for item in candidates_with_outcomes if is_ml(item) and is_llm(item)]
        llm_rejected = [item for item in candidates_with_outcomes if is_ml(item) and not is_llm(item)]

        def calc_pnl(bets: List[Dict[str, Any]]) -> Tuple[float, float]:
            total_stake = 0.0
            pnl = 0.0
            for b in bets:
                stake = float(b.get("stake", 1000.0))
                odds = float(b.get("odds", 2.0))
                outcome = b.get("actual_outcome", "LOST")
                total_stake += stake
                if outcome == "WON":
                    pnl += stake * (odds - 1.0)
                elif outcome == "LOST":
                    pnl -= stake
                elif outcome == "VOID":
                    pnl += 0.0
            roi = (pnl / total_stake * 100.0) if total_stake > 0 else 0.0
            return round(pnl, 2), round(roi, 2)

        pnl_ml, roi_ml = calc_pnl(ml_only_bets)
        pnl_llm, roi_llm = calc_pnl(ml_llm_bets)

        incremental_pnl = round(pnl_llm - pnl_ml, 2)
        incremental_roi_delta = round(roi_llm - roi_ml, 2)

        # False positive reduction: how many of the rejected bets were actually LOST
        losses_avoided = sum(1 for b in llm_rejected if b.get("actual_outcome") == "LOST")
        fp_reduction_rate = round(losses_avoided / len(llm_rejected), 4) if llm_rejected else 0.0

        summary = (
            f"Evaluated {total_candidates} candidates: ML-Only placed {len(ml_only_bets)} bets (PnL: {pnl_ml:+.2f} RUB, ROI: {roi_ml:.2f}%), "
            f"ML+LLM placed {len(ml_llm_bets)} bets (PnL: {pnl_llm:+.2f} RUB, ROI: {roi_llm:.2f}%). "
            f"LLM filtered {len(llm_rejected)} bets (avoided {losses_avoided} losses), "
            f"yielding incremental PnL of {incremental_pnl:+.2f} RUB and ROI delta of {incremental_roi_delta:+.2f}%."
        )

        return StrategyComparisonReport(
            total_candidates=total_candidates,
            ml_only_bets_count=len(ml_only_bets),
            ml_llm_bets_count=len(ml_llm_bets),
            llm_filtered_count=len(llm_rejected),
            ml_only_pnl=pnl_ml,
            ml_llm_pnl=pnl_llm,
            incremental_pnl=incremental_pnl,
            incremental_roi_delta=incremental_roi_delta,
            false_positive_reduction_rate=fp_reduction_rate,
            summary=summary,
        )

    compare = compare_strategies

