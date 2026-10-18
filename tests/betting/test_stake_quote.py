"""Tennis stake quote: positive EV is sized, a losing price is not."""

from tennis_adapter.policy import TennisPolicy
from tennis_adapter.stake import expected_value, fractional_kelly, quote_stake


def test_negative_ev_is_not_a_stake():
    quote = quote_stake(balance=10_000, odds=1.80, probability=0.40, policy=TennisPolicy())
    assert quote["stake"] == 0.0
    assert quote["reason"] == "NEGATIVE_EV"
    assert expected_value(0.40, 1.80) < 0


def test_kelly_is_capped_by_bank_and_quality():
    policy = TennisPolicy(kelly_fraction=0.25, max_stake_fraction=0.05, min_stake=50)
    full = fractional_kelly(0.70, 1.80, 1.0)
    assert full > policy.max_stake_fraction
    quote = quote_stake(
        balance=10_000,
        odds=1.80,
        probability=0.70,
        data_quality=80,
        research_quality=1.0,
        llm_confidence=1.0,
        llm_verdict="BET",
        policy=policy,
    )
    assert quote["stake"] == 400.0
    assert quote["potential_win"] == 320.0
    assert quote["max_loss"] == 400.0
    assert quote["reason"] == ""


def test_neutral_research_does_not_drop_below_the_minimum():
    policy = TennisPolicy(kelly_fraction=0.25, max_stake_fraction=0.05, min_stake=50)
    quote = quote_stake(
        balance=1000,
        odds=1.50,
        probability=0.939,
        data_quality=100,
        research_quality=0.5,
        llm_confidence=0.929,
        llm_verdict="BET",
        policy=policy,
    )
    assert quote["stake"] == 50.0
    assert quote["reason"] == ""


def test_context_risk_blocks_the_stake():
    quote = quote_stake(
        balance=10_000,
        odds=1.80,
        probability=0.70,
        injury_risk=0.9,
        policy=TennisPolicy(),
    )
    assert quote["stake"] == 0.0
    assert quote["reason"] == "CONTEXT_RISK"


if __name__ == "__main__":
    test_negative_ev_is_not_a_stake()
    test_kelly_is_capped_by_bank_and_quality()
    test_neutral_research_does_not_drop_below_the_minimum()
    test_context_risk_blocks_the_stake()
    print("stake quote ok")
