import json
import os
from typing import Dict, Any
from contracts import (
    Event,
    EventState,
    OddsSnapshot,
    FeatureVector,
    MLPrediction,
    ResearchPacket,
    LLMDecision,
    BetProposal,
    BetValidationResult,
    VirtualBet,
    Settlement,
)


def export_all_schemas(output_dir: str = "schemas") -> Dict[str, Any]:
    """Generates JSON Schema representations for all canonical contracts."""
    models = {
        "event": Event,
        "event_state": EventState,
        "odds_snapshot": OddsSnapshot,
        "feature_vector": FeatureVector,
        "ml_prediction": MLPrediction,
        "research_packet": ResearchPacket,
        "llm_decision": LLMDecision,
        "bet_proposal": BetProposal,
        "bet_validation_result": BetValidationResult,
        "virtual_bet": VirtualBet,
        "settlement": Settlement,
    }

    os.makedirs(output_dir, exist_ok=True)
    schemas = {}
    for name, model in models.items():
        schema = model.model_json_schema()
        schemas[name] = schema
        with open(os.path.join(output_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(schema, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return schemas


if __name__ == "__main__":
    export_all_schemas()
    print("Exported JSON schemas successfully.")
