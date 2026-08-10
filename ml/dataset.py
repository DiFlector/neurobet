"""
Fonbet Dataset Loader & Preprocessor
Parses history_db JSONL files, extracts odds features, targets, score context,
filters out non-match noise (empty headers, cyber fast games, unfinished games),
and prevents live score data leakage via prematch mode.
"""

import json
import logging
import os
import sys
import time
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
logger = logging.getLogger("dataset_loader")

FEATURE_FACTOR_IDS = [
    921, 922, 923, 924, 925, 926,
    927, 928, 910, 912, 989, 991,
    930, 931, 1696, 1697, 1727, 1728,
    1809, 1810, 1854, 1871, 1873
]

def build_feature_names() -> List[str]:
    names = []
    for fid in FEATURE_FACTOR_IDS:
        names.extend([f"odds_{fid}", f"prob_{fid}", f"param_{fid}"])
    return names

class OddsDatasetLoader:
    def __init__(self, data_file: str = "history_db/fonbet_history_2years.jsonl"):
        self.data_file = data_file

    def is_noise_record(self, item: Dict[str, Any]) -> bool:
        """Determines if a dataset record is non-match noise or incomplete."""
        if item.get("is_finished") is False:
            return True

        score = item.get("score")
        if not score or ":" not in str(score):
            return True

        team1 = str(item.get("team_1") or item.get("team1") or "").strip()
        team2 = str(item.get("team_2") or item.get("team2") or "").strip()

        if not team1 or not team2 or team1.lower() in ["футбол", "хоккей", "теннис", "баскетбол", "спорт"]:
            return True

        title = str(item.get("match_name", "")).lower() + " " + team1.lower() + " " + team2.lower()
        
        cyber_keywords = [
            "cyber", "кибер", "fifa", "fc 24", "fc 25", "fc 26", "esports", "2x2", "3x3", "4x4", "5x5", "nba 2k", "nhl 2k", "fast", "быстрый"
        ]
        for kw in cyber_keywords:
            if kw in title:
                return True

        return False

    def load_dataset(
        self,
        max_samples: int = 0,
        filter_noise: bool = True,
        mode: str = "prematch"
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[Dict[str, Any]], List[str]]:
        if not os.path.exists(self.data_file):
            raise FileNotFoundError(f"Dataset file not found: {self.data_file}")

        feature_names = build_feature_names()
        feature_rows = []
        targets_1x2 = []
        targets_over = []
        valid_samples = []

        filtered_count = 0
        count = 0
        t0 = time.time()

        total_lines_approx = 2024764
        limit_target = max_samples if max_samples > 0 else total_lines_approx

        print(f"📦 Загрузка датасета '{self.data_file}' (Режим: '{mode}')...", flush=True)

        with open(self.data_file, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line.strip())
                except Exception:
                    continue

                if filter_noise and self.is_noise_record(item):
                    filtered_count += 1
                    continue

                # Get targets
                target_1x2 = item.get("winner_1x2")
                score_str = str(item.get("score", ""))

                if target_1x2 is None:
                    try:
                        s1, s2 = map(int, score_str.split(":"))
                        if s1 > s2:
                            target_1x2 = 0
                        elif s1 == s2:
                            target_1x2 = 1
                        else:
                            target_1x2 = 2
                    except Exception:
                        filtered_count += 1
                        continue

                target_over = item.get("is_over_2_5")
                if target_over is None:
                    try:
                        s1, s2 = map(int, score_str.split(":"))
                        target_over = 1.0 if (s1 + s2) > 2.5 else 0.0
                    except Exception:
                        target_over = 0.0
                else:
                    target_over = 1.0 if target_over else 0.0

                team1 = item.get("team_1") or item.get("team1") or ""
                team2 = item.get("team_2") or item.get("team2") or ""

                odds = item.get("odds", {})
                if not isinstance(odds, dict):
                    odds = {}

                sample = {
                    "match_name": item.get("match_name"),
                    "team1": team1,
                    "team2": team2,
                    "score": score_str,
                    "target_1x2": target_1x2,
                    "target_over": target_over,
                    "odds": odds
                }

                row = []
                for fid in FEATURE_FACTOR_IDS:
                    fid_str = str(fid)
                    odds_val = odds.get(fid_str)

                    if odds_val is not None and str(odds_val).replace(".", "").isdigit():
                        o = float(odds_val)
                        p_implied = 1.0 / max(0.001, o)
                    else:
                        o = 0.0
                        p_implied = 0.0

                    p_num = 0.0
                    param_val = item.get("parameters", {}).get(fid_str)
                    if param_val is not None:
                        try:
                            p_num = float(str(param_val).replace("+", ""))
                        except ValueError:
                            pass

                    row.extend([o, p_implied, p_num])

                feature_rows.append(row)
                targets_1x2.append(target_1x2)
                targets_over.append(target_over)
                valid_samples.append(sample)

                count += 1
                if count % 200000 == 0:
                    elapsed = time.time() - t0
                    speed = count / max(0.001, elapsed)
                    percent = (count / limit_target) * 100
                    print(f" ⌛ [{count:7,d} / {limit_target:7,d}] ({percent:5.1f}%) | Отфильтровано шума: {filtered_count:,} | Скорость: {speed:6.0f} матчей/сек | {elapsed:4.1f}с", flush=True)

                if max_samples > 0 and count >= max_samples:
                    break

        print(f" ✅ Загружено {len(feature_rows):,} чистых матчей для режима '{mode}' (отфильтровано шума: {filtered_count:,}).", flush=True)

        X = np.array(feature_rows, dtype=np.float32)
        y_1x2 = np.array(targets_1x2, dtype=np.int64)
        y_over = np.array(targets_over, dtype=np.float32)

        return X, y_1x2, y_over, valid_samples, feature_names

if __name__ == "__main__":
    loader = OddsDatasetLoader()
    X, y_1x2, y_over, samples, fnames = loader.load_dataset(max_samples=50000, mode="prematch")
    print(f"X shape: {X.shape}, y_1x2 shape: {y_1x2.shape}, y_over shape: {y_over.shape}")
