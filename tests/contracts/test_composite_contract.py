"""The composite contract is one model state, not three registry rows."""

from contracts.composite import CompositeModelState


def test_public_state_names_the_model_and_its_lines():
    state = CompositeModelState(
        phase="2/3",
        display_phase="2/3",
        selected=False,
        market="match_winner",
        side="player_a",
        agreement_count=2,
        components=[
            {"component_id": "logistic", "label": "Логистическая", "side": "player_a", "confirmed": True, "probability": 0.61, "edge": 0.04, "verdict": "подтвердила player_a"},
            {"component_id": "boosting", "label": "Бустинг", "side": "player_a", "confirmed": True, "probability": 0.58, "edge": 0.03, "verdict": "подтвердила player_a"},
            {"component_id": "residual_mlp", "label": "Residual MLP 64→32", "side": None, "confirmed": False, "probability": 0.52, "edge": 0.01, "verdict": "ещё нет"},
        ],
    )
    assert state.label == "Составная модель"
    assert [item.label for item in state.components] == ["Логистическая", "Бустинг", "Residual MLP 64→32"]
    assert state.schema_version == "1.0.0"


if __name__ == "__main__":
    test_public_state_names_the_model_and_its_lines()
    print("test_composite_contract: OK")
