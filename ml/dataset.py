"""
Dataset Preprocessor & Cleaner for Sports Odds ML/DL Models
Supports both Prematch (pure odds modeling) and Live models to prevent score-bias leakage.
"""

import json
import os
import time
import numpy as np
import pandas as pd
from typing import Tuple, List, Dict, Any

FEATURE_FACTOR_IDS = [
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
    1873  # BTTS No
]

VIRTUAL_KEYWORDS = [
    "cyber", "fc 26", "fc 25", "e-football", "e-basketball", "e-hockey", "fifa", "nba 2k",
    "игры 24/7", "быстрые игры", "mortal kombat", "rocket league", "симуляторы",
    "виртуальный", "dice", "стрела", "буллиты", "состязание бросков"
]

class OddsDatasetLoader:
    def __init__(self, data_file: str = "history_db/fonbet_history_2years.jsonl"):
        self.data_file = data_file

    def load_dataset(
        self,
        max_samples: int = 500000,
        filter_noise: bool = True,
        mode: str = "prematch"  # "prematch" (pure odds, no score bias) or "live"
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[Dict[str, Any]], List[str]]:
        """
        Loads dataset.
        mode="prematch": Ignores live scores during training to predict overall match outcome based on odds & market probabilities.
        mode="live": Includes live score and elapsed game state.
        """
        if not os.path.exists(self.data_file):
            raise FileNotFoundError(f"Dataset file not found: {self.data_file}")

        feature_rows = []
        targets_1x2 = []
        targets_over = []
        valid_samples = []

        if mode == "live":
            feature_names = ["score_1", "score_2", "score_diff", "score_sum"]
        else:
            feature_names = []

        for fid in FEATURE_FACTOR_IDS:
            feature_names.extend([
                f"f_{fid}_odds",
                f"f_{fid}_implied_p",
                f"f_{fid}_param"
            ])

        file_size_mb = os.path.getsize(self.data_file) / (1024 * 1024)
        print(f"📂 Dataset file: {self.data_file} ({file_size_mb:.1f} MB, mode={mode})", flush=True)

        count = 0
        filtered_count = 0
        t0 = time.time()
        log_interval = max(10000, max_samples // 10)

        with open(self.data_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                sample = json.loads(line)
                score_1 = sample.get("score_1", 0)
                score_2 = sample.get("score_2", 0)
                t1 = sample.get("team_1", "").strip()
                t2 = sample.get("team_2", "").strip()
                sport = sample.get("sport_path", "").lower()
                is_finished = sample.get("is_finished", True)

                # Strict noise filtering
                if filter_noise:
                    if not is_finished:
                        filtered_count += 1
                        continue

                    if not t1 or not t2 or sport == "спорт":
                        filtered_count += 1
                        continue

                    if any(vk in sport for vk in VIRTUAL_KEYWORDS):
                        filtered_count += 1
                        continue

                # Determine 1X2 Target
                if "winner_1x2" in sample:
                    target_1x2 = sample["winner_1x2"]
                else:
                    if score_1 > score_2:
                        target_1x2 = 0
                    elif score_1 == score_2:
                        target_1x2 = 1
                    else:
                        target_1x2 = 2

                target_over = 1 if (score_1 + score_2) > 2.5 else 0

                odds_dict = sample.get("odds_vector", {})
                params_dict = sample.get("odds_params", {})

                if mode == "live":
                    row = [score_1, score_2, score_1 - score_2, score_1 + score_2]
                else:
                    row = []

                for fid in FEATURE_FACTOR_IDS:
                    fid_str = str(fid)
                    odds_val = odds_dict.get(fid_str)
                    param_val = params_dict.get(fid_str)

                    if odds_val and float(odds_val) > 1.0:
                        o = float(odds_val)
                        p_implied = 1.0 / o
                    else:
                        o = 0.0
                        p_implied = 0.0

                    p_num = 0.0
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
                if count % log_interval == 0 or count == max_samples:
                    elapsed = time.time() - t0
                    speed = count / max(0.001, elapsed)
                    percent = (count / max_samples) * 100
                    print(f" ⌛ [{count:7,d} / {max_samples:7,d}] ({percent:5.1f}%) | Отфильтровано: {filtered_count:,} | Скорость: {speed:6.0f} матчей/сек | {elapsed:4.1f}с", flush=True)

                if count >= max_samples:
                    break

        print(f" ✅ Загружено {len(feature_rows):,} чистых матчей для режима '{mode}' (отфильтровано {filtered_count:,}).", flush=True)

        X = np.array(feature_rows, dtype=np.float32)
        y_1x2 = np.array(targets_1x2, dtype=np.int64)
        y_over = np.array(targets_over, dtype=np.float32)

        return X, y_1x2, y_over, valid_samples, feature_names
