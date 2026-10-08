"""Comprehensive tests for Phase 6: Generic Sports Layer."""

import importlib.util
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient

# Configure python path for test execution
for p in [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/sports/tennis/src",
    "/srv/neurobet/services/bet-manager",
    "/srv/neurobet/services/backend",
    "/app/packages/contracts/src",
    "/app/packages/sports-core/src",
    "/app/sports/tennis/src",
    "/app/services/bet-manager",
    "/app",
]:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import (
    EventLifecycleManager,
    EventLifecycleState,
    GenericMarketDefinition,
    GenericParticipant,
    SportDescriptor,
    SportLabelProvider,
    registry,
)
import tennis_adapter

# Resolve BetValidator
try:
    from app.validator import BetValidator
except ImportError:
    try:
        from services.bet_manager.app.validator import BetValidator
    except ImportError:
        val_paths = [
            Path("/srv/neurobet/services/bet-manager/app/validator.py"),
            Path("/app/services/bet-manager/app/validator.py"),
            Path("/app/app/validator.py"),
        ]
        BetValidator = None
        for vp in val_paths:
            if vp.exists():
                spec = importlib.util.spec_from_file_location("validator", vp)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                BetValidator = mod.BetValidator
                break
        if not BetValidator:
            raise ImportError("Could not locate BetValidator")

# Resolve FastAPI app
try:
    from app.main import app
except ImportError:
    from services.backend.app.main import app


def test_lifecycle_transitions():
    """Verify permissible and invalid lifecycle state transitions."""
    # Permissible transitions
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.SCHEDULED, EventLifecycleState.LIVE)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.LIVE, EventLifecycleState.PAUSED)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.PAUSED, EventLifecycleState.LIVE)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.LIVE, EventLifecycleState.FINISHED)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.LIVE, EventLifecycleState.SUSPENDED)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.SUSPENDED, EventLifecycleState.LIVE)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.LIVE, EventLifecycleState.CANCELLED)

    # Idempotent state retention is always valid
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.LIVE, EventLifecycleState.LIVE)
    assert EventLifecycleManager.is_valid_transition(EventLifecycleState.FINISHED, EventLifecycleState.FINISHED)

    # Invalid transitions (cannot resurrect finished or cancelled events)
    assert not EventLifecycleManager.is_valid_transition(EventLifecycleState.FINISHED, EventLifecycleState.LIVE)
    assert not EventLifecycleManager.is_valid_transition(EventLifecycleState.CANCELLED, EventLifecycleState.LIVE)
    assert not EventLifecycleManager.is_valid_transition(EventLifecycleState.FINISHED, EventLifecycleState.SCHEDULED)


def test_lifecycle_string_normalization():
    """Verify normalization of various raw state strings."""
    assert EventLifecycleManager.normalize_state_string("prematch") == EventLifecycleState.SCHEDULED
    assert EventLifecycleManager.normalize_state_string("IN_PROGRESS") == EventLifecycleState.LIVE
    assert EventLifecycleManager.normalize_state_string("break") == EventLifecycleState.PAUSED
    assert EventLifecycleManager.normalize_state_string("rain_delay") == EventLifecycleState.SUSPENDED
    assert EventLifecycleManager.normalize_state_string("completed") == EventLifecycleState.FINISHED
    assert EventLifecycleManager.normalize_state_string("walkover") == EventLifecycleState.CANCELLED


def test_generic_participant_and_market_models():
    """Verify GenericParticipant and GenericMarketDefinition serialization and validation."""
    p_tennis = GenericParticipant(
        sport_code="tennis",
        participant_type="individual",
        canonical_name="Даниил Медведев",
        aliases=["Медведев Д.", "D. Medvedev"],
        country="RUS",
    )
    assert p_tennis.participant_type == "individual"
    assert "D. Medvedev" in p_tennis.aliases

    p_football = GenericParticipant(
        sport_code="football",
        participant_type="team",
        canonical_name="Спартак Москва",
        aliases=["Спартак М", "FC Spartak"],
        country="RUS",
    )
    assert p_football.participant_type == "team"

    market_def = GenericMarketDefinition(
        market_type="match_winner",
        name_ru="Победитель матча",
        supported_outcomes=["player_a", "player_b"],
        requires_line_value=False,
    )
    assert market_def.market_type == "match_winner"
    assert market_def.supported_outcomes == ["player_a", "player_b"]


def test_sport_label_provider():
    """Verify Russian localization provider for sports, markets, and outcomes."""
    assert SportLabelProvider.get_sport_name_ru("tennis") == "Теннис"
    assert SportLabelProvider.get_sport_name_ru("football") == "Футбол"
    assert SportLabelProvider.get_sport_name_ru("unknown_sport") == "Unknown_sport"

    assert SportLabelProvider.get_market_name_ru("tennis", "match_winner") == "Победитель матча"
    assert SportLabelProvider.get_market_name_ru("generic", "match_winner") == "Победитель события"

    assert SportLabelProvider.get_outcome_name_ru("tennis", "player_a") == "Игрок 1"
    assert SportLabelProvider.get_outcome_name_ru("generic", "team_a") == "Команда 1"

    assert SportLabelProvider.get_lifecycle_name_ru(EventLifecycleState.LIVE) == "В прямом эфире (Live)"


def test_sport_registry_support_and_descriptors():
    """Verify sport registry distinguishes supported tennis from unsupported sports."""
    assert registry.is_supported("tennis")
    assert not registry.is_supported("cricket")
    assert not registry.is_supported("curling")

    # Supported descriptor
    desc_tennis = registry.get_descriptor("tennis")
    assert desc_tennis.supported is True
    assert desc_tennis.status == "ACTIVE"
    assert desc_tennis.name_ru == "Теннис"
    assert len(desc_tennis.supported_markets) >= 1

    # Unsupported descriptor
    desc_cricket = registry.get_descriptor("cricket")
    assert desc_cricket.supported is False
    assert desc_cricket.status == "UNSUPPORTED"
    assert desc_cricket.supported_markets == []


def test_bet_validator_unsupported_sport_rejection():
    """Verify bet validator rejects unsupported sports with UNSUPPORTED_SPORT reason."""
    validator = BetValidator(max_stake=5000.0)

    # Valid tennis proposal
    tennis_proposal = {
        "proposal_id": "prop_tennis_1",
        "event_id": "fonbet_tennis_1001",
        "sport_code": "tennis",
        "market": "match_winner",
        "selection": "player_a",
        "odds": 1.85,
        "stake": 1000.0,
    }
    result_tennis = validator.validate_proposal(tennis_proposal, current_bankroll=50000.0)
    assert result_tennis.is_valid is True
    assert result_tennis.status == "ACCEPTED"
    assert result_tennis.rejection_reason is None

    # Unsupported cricket proposal
    cricket_proposal = {
        "proposal_id": "prop_cricket_1",
        "event_id": "fonbet_cricket_999",
        "sport_code": "cricket",
        "market": "match_winner",
        "selection": "team_a",
        "odds": 2.10,
        "stake": 500.0,
    }
    result_cricket = validator.validate_proposal(cricket_proposal, current_bankroll=50000.0)
    assert result_cricket.is_valid is False
    assert result_cricket.status == "REJECTED"
    assert result_cricket.rejection_reason == "UNSUPPORTED_SPORT"

    # Unsupported curling proposal
    curling_proposal = {
        "proposal_id": "prop_curling_1",
        "event_id": "fonbet_curling_888",
        "sport_code": "curling",
        "market": "match_winner",
        "selection": "team_a",
        "odds": 1.90,
        "stake": 500.0,
    }
    result_curling = validator.validate_proposal(curling_proposal, current_bankroll=50000.0)
    assert result_curling.is_valid is False
    assert result_curling.status == "REJECTED"
    assert result_curling.rejection_reason == "UNSUPPORTED_SPORT"

    # Tennis proposal violating tennis sport rules (e.g. invalid draw selection)
    invalid_tennis_rule = {
        "proposal_id": "prop_tennis_bad",
        "event_id": "fonbet_tennis_1001",
        "sport_code": "tennis",
        "market": "match_winner",
        "selection": "draw",  # Tennis match_winner has no draw!
        "odds": 3.50,
        "stake": 500.0,
    }
    result_bad_tennis = validator.validate_proposal(invalid_tennis_rule, current_bankroll=50000.0)
    assert result_bad_tennis.is_valid is False
    assert result_bad_tennis.status == "REJECTED"
    assert result_bad_tennis.rejection_reason == "SPORT_RULE_VIOLATION"


def test_backend_sports_endpoints():
    """Verify backend returns supported sports and handles unknown sports gracefully."""
    client = TestClient(app)

    # 1. List sports
    resp = client.get("/api/sports")
    assert resp.status_code == 200
    sports = resp.json()
    assert isinstance(sports, list)
    sport_codes = [s["sport_code"] for s in sports]
    assert "tennis" in sport_codes

    # 2. Get supported sport details
    resp_tennis = client.get("/api/sports/tennis")
    assert resp_tennis.status_code == 200
    tennis_data = resp_tennis.json()
    assert tennis_data["sport_code"] == "tennis"
    assert tennis_data["status"] == "ACTIVE"
    assert tennis_data["supported"] is True
    assert tennis_data["is_primary"] is True
    assert len(tennis_data["supported_markets"]) > 0

    # 3. Get unsupported sport details (graceful response)
    resp_unsupported = client.get("/api/sports/cricket")
    assert resp_unsupported.status_code == 200
    unsupported_data = resp_unsupported.json()
    assert unsupported_data["sport_code"] == "cricket"
    assert unsupported_data["status"] == "UNSUPPORTED"
    assert unsupported_data["supported"] is False
    assert unsupported_data["is_primary"] is False
    assert unsupported_data["supported_markets"] == []


if __name__ == "__main__":
    test_lifecycle_transitions()
    print("test_lifecycle_transitions: OK")
    test_lifecycle_string_normalization()
    print("test_lifecycle_string_normalization: OK")
    test_generic_participant_and_market_models()
    print("test_generic_participant_and_market_models: OK")
    test_sport_label_provider()
    print("test_sport_label_provider: OK")
    test_sport_registry_support_and_descriptors()
    print("test_sport_registry_support_and_descriptors: OK")
    test_bet_validator_unsupported_sport_rejection()
    print("test_bet_validator_unsupported_sport_rejection: OK")
    test_backend_sports_endpoints()
    print("test_backend_sports_endpoints: OK")
    print("ALL GENERIC LAYER TESTS PASSED!")
