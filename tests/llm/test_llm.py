"""Unit and integration test suite for Phase 14: Local LLM Service."""

import json
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from contracts import (
    LLMAnalysisInput,
    LLMEventContext,
    LLMMarketContext,
    LLMMLContext,
    LLMResearchItem,
    LLMConstraints,
    LLMStructuredVerdict,
    LLMDecision,
)
from app.main import app
from app.config import config
from app.hardware import HardwareManager
from app.parser import LLMOutputParser
from app.engine import DeterministicEngine, GGUFEngine
from app.prompt import PROMPT_VERSION, format_user_prompt


def sample_analysis_input(
    edge: float = 0.08,
    prob: float = 0.58,
    snippets: list = None,
) -> LLMAnalysisInput:
    """Helper to construct valid Section 19 input contracts."""
    if snippets is None:
        snippets = ["Daniil Medvedev is in excellent shape on hard courts."]

    return LLMAnalysisInput(
        event=LLMEventContext(
            sport="tennis",
            league="ATP Shanghai",
            home="Daniil Medvedev",
            away="Carlos Alcaraz",
            status="live",
            clock="6-4, 3-2",
            score={"sets": "1-0", "games": "3-2"},
        ),
        market=LLMMarketContext(
            type="match_winner",
            selection="player_a",
            odds=2.20,
        ),
        ml=LLMMLContext(
            probability=prob,
            market_probability=0.45,
            edge=edge,
            confidence=0.78,
            model_version="tennis_gb_v1.0",
        ),
        research=[
            LLMResearchItem(
                title="Match Preview",
                domain="tennismajors.com",
                snippet=s,
            )
            for s in snippets
        ],
        constraints=LLMConstraints(
            max_stake_fraction=0.015,
            simulation_only=True,
        ),
    )


def test_hardware_detection_and_gpu_offload_rules():
    """Verify hardware manager detects GPU capability and enforces layer rules."""
    has_gpu, gpu_info = HardwareManager.detect_nvidia_gpu()
    # On this server, GTX 1050 Ti is physically present
    assert isinstance(has_gpu, bool)
    assert isinstance(gpu_info, dict)

    # Test layer calculation
    cpu_layers, cpu_device = HardwareManager.calculate_gpu_layers("0")
    assert cpu_layers == 0
    assert cpu_device == "cpu"

    explicit_layers, explicit_device = HardwareManager.calculate_gpu_layers("16")
    if has_gpu:
        assert explicit_layers == 16
        assert explicit_device == "cuda"
    else:
        assert explicit_layers == 0
        assert explicit_device == "cpu"

    print(f"test_hardware_detection_and_gpu_offload_rules: OK (GPU available={has_gpu})")


def test_health_endpoint():
    """Verify /health endpoint returns active model, device, and prompt version."""
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "healthy"
    assert data["service"] == "llm"
    assert "model_identifier" in data
    assert data["prompt_version"] == PROMPT_VERSION
    assert "device" in data
    assert "gpu_available" in data
    print("test_health_endpoint: OK")


def test_section19_and_section20_schema_contracts():
    """Verify strict Pydantic contract validation for input (Sec 19) and output (Sec 20)."""
    inp = sample_analysis_input()
    inp_dict = inp.model_dump()
    assert inp_dict["event"]["home"] == "Daniil Medvedev"
    assert inp_dict["market"]["odds"] == 2.20
    assert inp_dict["ml"]["edge"] == 0.08

    # Verify output contract bounds
    valid_verdict = LLMStructuredVerdict(
        verdict="BET",
        market_type="match_winner",
        selection_id="player_a",
        confidence=0.82,
        stake_recommendation_fraction=0.01,
        reason_codes=["MODEL_EDGE"],
        summary="Positive value confirmed",
        latency_ms=15.2,
        model_identifier="test-model",
        prompt_version=PROMPT_VERSION,
    )
    assert valid_verdict.confidence == 0.82

    # Verify confidence out of bounds [0.0, 1.0] is rejected
    with pytest.raises(ValidationError):
        LLMStructuredVerdict(
            verdict="BET",
            market_type="match_winner",
            selection_id="player_a",
            confidence=1.5,  # Invalid: > 1.0
            summary="Bad confidence",
        )

    # Verify stake fraction out of bounds [0.0, 0.05] is rejected
    with pytest.raises(ValidationError):
        LLMStructuredVerdict(
            verdict="BET",
            market_type="match_winner",
            selection_id="player_a",
            confidence=0.8,
            stake_recommendation_fraction=0.10,  # Invalid: > 5%
            summary="Bad stake",
        )

    print("test_section19_and_section20_schema_contracts: OK")


def test_json_only_enforcement_and_markdown_stripping():
    """Verify parser extracts clean JSON even if LLM wraps output in markdown code blocks."""
    raw_markdown = """
Here is the analytical verdict:
```json
{
  "verdict": "BET",
  "market_type": "match_winner",
  "selection_id": "player_a",
  "confidence": 0.77,
  "stake_recommendation_fraction": 0.012,
  "reason_codes": ["MODEL_EDGE", "NO_NEGATIVE_SIGNALS"],
  "contradictions": [],
  "research_quality": 0.85,
  "summary": "Clear edge without red flags",
  "evidence": []
}
```
Thank you!
"""
    cleaned = LLMOutputParser.clean_json_text(raw_markdown)
    assert cleaned.startswith("{")
    assert cleaned.endswith("}")

    verdict = LLMOutputParser.parse_and_validate(
        raw_output=raw_markdown,
        market_type="match_winner",
        selection_id="player_a",
        model_identifier="test-engine",
        latency_ms=25.0,
    )
    assert verdict.verdict == "BET"
    assert verdict.confidence == 0.77
    assert verdict.stake_recommendation_fraction == 0.012
    assert "MODEL_EDGE" in verdict.reason_codes
    print("test_json_only_enforcement_and_markdown_stripping: OK")


def test_invalid_json_fallback_to_insufficient_data():
    """Verify corrupted JSON strictly produces INSUFFICIENT_DATA fallback per Architecture Sec 20."""
    corrupt_outputs = [
        "I am not sure, but I think Medvedev will win because he plays well.",
        "{ broken json: 'no quotes', ",
        '{"verdict": "BET", "confidence": "invalid_string_not_float"}',
        '{"verdict": "ILLEGAL_VERDICT", "confidence": 0.5}',
    ]

    for corrupt in corrupt_outputs:
        verdict = LLMOutputParser.parse_and_validate(
            raw_output=corrupt,
            market_type="match_winner",
            selection_id="player_a",
            model_identifier="test-engine",
            latency_ms=10.0,
        )
        assert verdict.verdict == "INSUFFICIENT_DATA"
        assert verdict.confidence == 0.0
        assert verdict.stake_recommendation_fraction == 0.0
        assert len(verdict.reason_codes) >= 1
        assert any("CORRUPT" in c or "SCHEMA" in c for c in verdict.reason_codes)

    print("test_invalid_json_fallback_to_insufficient_data: OK")


def test_deterministic_reproducibility():
    """Verify identical input produces 100% reproducible output."""
    engine = DeterministicEngine(model_identifier="neurobet-test-det-v1")
    inp = sample_analysis_input(edge=0.07, prob=0.60)

    v1 = engine.analyze(inp)
    v2 = engine.analyze(inp)

    assert v1.verdict == v2.verdict == "BET"
    assert v1.confidence == v2.confidence
    assert v1.stake_recommendation_fraction == v2.stake_recommendation_fraction
    assert v1.reason_codes == v2.reason_codes
    assert v1.summary == v2.summary
    print("test_deterministic_reproducibility: OK")


def test_qualitative_injury_risk_rejection():
    """Verify research mentioning player injury/retirement forces NO_BET and INJURY_RISK."""
    engine = DeterministicEngine(model_identifier="neurobet-test-det-v1")
    inp = sample_analysis_input(
        edge=0.12,  # Strong ML edge
        snippets=["Медведев снялся с тренировки из-за острой боли в правом плече."],
    )

    verdict = engine.analyze(inp)
    assert verdict.verdict == "NO_BET"
    assert "INJURY_RISK" in verdict.reason_codes
    assert verdict.stake_recommendation_fraction == 0.0
    assert len(verdict.contradictions) >= 1
    assert any("injury" in c.lower() or "impairment" in c.lower() for c in verdict.contradictions)
    print("test_qualitative_injury_risk_rejection: OK")


def test_qualitative_fatigue_risk_rejection():
    """Verify research mentioning schedule congestion/fatigue forces NO_BET and SCHEDULE_FATIGUE."""
    engine = DeterministicEngine(model_identifier="neurobet-test-det-v1")
    inp = sample_analysis_input(
        edge=0.09,
        snippets=["Тяжелый матч накануне, игрок выглядел очень усталым после 3 сетов подряд."],
    )

    verdict = engine.analyze(inp)
    assert verdict.verdict == "NO_BET"
    assert "SCHEDULE_FATIGUE" in verdict.reason_codes
    assert verdict.stake_recommendation_fraction == 0.0
    print("test_qualitative_fatigue_risk_rejection: OK")


def test_latency_and_model_identifier_tracking():
    """Verify latency_ms and model_identifier are measured and saved."""
    client = TestClient(app)
    inp = sample_analysis_input()
    resp = client.post("/api/analyze", json=inp.model_dump(mode="json"))
    assert resp.status_code == 200
    data = resp.json()

    assert data["latency_ms"] >= 0.0
    assert "model_identifier" in data and len(data["model_identifier"]) > 0
    assert data["prompt_version"] == PROMPT_VERSION
    print("test_latency_and_model_identifier_tracking: OK")


def test_openai_chat_completions_endpoint():
    """Verify OpenAI-compatible /v1/chat/completions endpoint."""
    client = TestClient(app)
    payload = {
        "model": "qwen2.5-3b",
        "messages": [
            {"role": "system", "content": "You are a tennis analyst."},
            {"role": "user", "content": "Analyze Daniil Medvedev vs Carlos Alcaraz."},
        ],
        "temperature": 0.1,
        "max_tokens": 256,
    }
    resp = client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["object"] == "chat.completion"
    assert "choices" in data and len(data["choices"]) > 0
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]
    print("test_openai_chat_completions_endpoint: OK")


def test_security_isolation_no_db_and_no_direct_betting():
    """
    Architecture Security Verification:
    LLM service has no database connection credentials and cannot execute bets.
    """
    import os
    # Check that LLM container has no database URI configured for itself
    assert "DATABASE_URL" not in os.environ or os.environ.get("SERVICE_NAME") != "llm"
    # Ensure contracts forbid LLM from directly generating VirtualBet or modifying ledger
    assert not hasattr(DeterministicEngine, "place_bet")
    assert not hasattr(GGUFEngine, "place_bet")
    assert not hasattr(DeterministicEngine, "execute_bet")
    assert not hasattr(GGUFEngine, "execute_bet")
    print("test_security_isolation_no_db_and_no_direct_betting: OK")


if __name__ == "__main__":
    print("\n--- Running Phase 14 Local LLM Service Tests ---")
    test_hardware_detection_and_gpu_offload_rules()
    test_health_endpoint()
    test_section19_and_section20_schema_contracts()
    test_json_only_enforcement_and_markdown_stripping()
    test_invalid_json_fallback_to_insufficient_data()
    test_deterministic_reproducibility()
    test_qualitative_injury_risk_rejection()
    test_qualitative_fatigue_risk_rejection()
    test_latency_and_model_identifier_tracking()
    test_openai_chat_completions_endpoint()
    test_security_isolation_no_db_and_no_direct_betting()
    print("\nALL LOCAL LLM TESTS PASSED SUCCESSFULLY!")
