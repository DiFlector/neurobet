"""
Neural Network & Machine Learning Exporter for Fonbet Live Line Data.
Generates JSONL datasets, standardized feature vector JSON, and dense tabular CSV matrices.
"""

import json
import os
import csv
from typing import List, Dict, Any

CORE_FEATURE_FACTORS = [
    921,  # Win 1
    922,  # Draw
    923,  # Win 2
    924,  # 1X
    925,  # 12
    926,  # X2
    927,  # Handicap 1
    928,  # Handicap 2
    930,  # Total Over
    931,  # Total Under
    1871, # BTTS Yes
    1873, # BTTS No
    1809, # Ind Total 1 Over
    1810, # Ind Total 1 Under
    1812, # Ind Total 2 Over
    1813  # Ind Total 2 Under
]

class AIExporter:
    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def export(self, parsed_events: List[Dict[str, Any]], factor_vocabulary: Dict[str, Any], timestamp_str: str) -> Dict[str, str]:
        """Export all ML/AI format files."""
        files_created = {}

        # 1. Export JSONL (Dataset streaming / LLM fine-tuning format)
        jsonl_path = os.path.join(self.output_dir, "live_odds_ai.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for ev in parsed_events:
                ai_item = self._transform_to_ai_schema(ev, timestamp_str)
                f.write(json.dumps(ai_item, ensure_ascii=False) + "\n")
        files_created["jsonl"] = jsonl_path

        # 2. Export Standardized AI JSON (Vocabulary + Feature Objects)
        json_ai_path = os.path.join(self.output_dir, "live_odds_ai.json")
        ai_events = [self._transform_to_ai_schema(ev, timestamp_str) for ev in parsed_events]
        with open(json_ai_path, "w", encoding="utf-8") as f:
            json.dump({
                "schema_version": "1.0",
                "timestamp": timestamp_str,
                "total_events": len(ai_events),
                "factor_vocabulary": factor_vocabulary,
                "events": ai_events
            }, f, ensure_ascii=False, indent=2)
        files_created["json_ai"] = json_ai_path

        # 3. Export Dense Matrix CSV (Tabular PyTorch / XGBoost / Pandas ready format)
        matrix_path = os.path.join(self.output_dir, "live_odds_ai_matrix.csv")
        self._export_matrix_csv(matrix_path, parsed_events, timestamp_str)
        files_created["matrix_csv"] = matrix_path

        return files_created

    def _transform_to_ai_schema(self, ev: Dict[str, Any], timestamp_str: str) -> Dict[str, Any]:
        """Converts an event object into a clean normalized feature dictionary."""
        odds_dict = {}
        params_dict = {}
        labels_dict = {}

        for odd in ev.get("odds", []):
            fid = str(odd["factor_id"])
            odds_dict[fid] = odd["coefficient"]
            if odd.get("parameter") is not None:
                params_dict[fid] = odd["parameter"]
            labels_dict[fid] = odd["label"]

        return {
            "event_id": ev["event_id"],
            "timestamp": timestamp_str,
            "sport_id": ev.get("sport_id"),
            "sport_path": ev.get("sport_path"),
            "match_name": ev.get("match_name"),
            "team_1": ev.get("team_1"),
            "team_2": ev.get("team_2"),
            "score_1": ev.get("score_1"),
            "score_2": ev.get("score_2"),
            "is_live": ev.get("is_live", True),
            "timer": ev.get("timer", ""),
            "num_available_odds": len(odds_dict),
            "odds_vector": odds_dict,      # { "921": 2.50, "922": 3.20, ... }
            "odds_params": params_dict,    # { "927": "-1.5", "930": "2.5" }
            "odds_labels": labels_dict     # { "921": "П1", ... }
        }

    def _export_matrix_csv(self, file_path: str, events: List[Dict[str, Any]], timestamp_str: str):
        """Generates a tabular matrix where columns are fixed standard feature IDs."""
        header = [
            "event_id", "sport_id", "team_1", "team_2", "score_1", "score_2"
        ]
        
        # Add feature columns for core factor odds and parameters
        for fid in CORE_FEATURE_FACTORS:
            header.append(f"f_{fid}_odds")
            header.append(f"f_{fid}_param")

        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)

            for ev in events:
                odds_by_fid = {odd["factor_id"]: odd for odd in ev.get("odds", [])}
                row = [
                    ev.get("event_id"),
                    ev.get("sport_id"),
                    ev.get("team_1", ""),
                    ev.get("team_2", ""),
                    ev.get("score_1", 0),
                    ev.get("score_2", 0)
                ]

                for fid in CORE_FEATURE_FACTORS:
                    odd_info = odds_by_fid.get(fid)
                    if odd_info:
                        row.append(odd_info.get("coefficient", ""))
                        row.append(odd_info.get("parameter", ""))
                    else:
                        row.append("")
                        row.append("")

                writer.writerow(row)
