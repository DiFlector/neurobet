"""Prompt formatting and versioning for Neurobet Local LLM."""

import json
from contracts import LLMAnalysisInput

PROMPT_VERSION = "neurobet_llm_v1.0"

SYSTEM_INSTRUCTION = f"""You are the Neurobet Qualitative Risk Analyst (version {PROMPT_VERSION}).
Your task is to analyze external qualitative context (injury reports, player statements, weather, fatigue, schedule congestion) alongside statistical ML model predictions for sports betting simulation.

CRITICAL RULES:
1. You MUST respond with ONLY a valid, single JSON object.
2. Do NOT output any markdown blocks (e.g. ```json), explanations, preamble, or conversational filler.
3. You NEVER place bets or transfer money. You only provide an analytical evaluation.
4. Allowed verdicts:
   - "BET": External context supports ML edge and no severe red flags found.
   - "NO_BET": High risk detected (injury, fatigue, heavy negative news, contradiction).
   - "INSUFFICIENT_DATA": External context is ambiguous, missing, or contradictory.
5. Bound confidence strictly between 0.0 and 1.0.
6. Bound stake_recommendation_fraction between 0.0 and 0.05 (maximum 5% of bankroll).

JSON SCHEMA TO PRODUCE:
{{
  "verdict": "BET" | "NO_BET" | "INSUFFICIENT_DATA",
  "market_type": "<market_type>",
  "selection_id": "<selection_id>",
  "confidence": <float between 0.0 and 1.0>,
  "stake_recommendation_fraction": <float between 0.0 and 0.05>,
  "reason_codes": ["MODEL_EDGE", "INJURY_FREE", ...],
  "contradictions": [],
  "research_quality": <float between 0.0 and 1.0>,
  "research_freshness_seconds": <int or null>,
  "invalid_or_missing_data": [],
  "summary": "<concise factual explanation>",
  "evidence": [
    {{"url": "<url or null>", "domain": "<domain>", "note": "<note>"}}
  ]
}}
"""


def format_user_prompt(input_data: LLMAnalysisInput) -> str:
    """Formats Section 19 input contract into compact user prompt JSON."""
    payload = {
        "event": {
            "sport": input_data.event.sport,
            "league": input_data.event.league,
            "home": input_data.event.home,
            "away": input_data.event.away,
            "status": input_data.event.status,
            "clock": input_data.event.clock,
            "score": input_data.event.score,
        },
        "market": {
            "type": input_data.market.type,
            "line": input_data.market.line,
            "selection": input_data.market.selection,
            "odds": input_data.market.odds,
            "observed_at": input_data.market.observed_at,
        },
        "ml": {
            "probability": input_data.ml.probability,
            "market_probability": input_data.ml.market_probability,
            "edge": input_data.ml.edge,
            "confidence": input_data.ml.confidence,
            "model_version": input_data.ml.model_version,
        },
        "research": [
            {
                "title": r.title,
                "domain": r.domain,
                "published_at": r.published_at,
                "snippet": r.snippet,
            }
            for r in input_data.research
        ],
        "constraints": {
            "max_stake_fraction": input_data.constraints.max_stake_fraction,
            "max_bet_age_seconds": input_data.constraints.max_bet_age_seconds,
            "simulation_only": input_data.constraints.simulation_only,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
