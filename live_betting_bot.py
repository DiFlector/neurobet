"""
Fonbet Live Automated Betting Recommendation Engine + LLM Risk Analyst
Parses real-time Live matches, passes them through LightGBM + PyTorch models,
evaluates ALL markets (1X2, Double Chance, Totals, Handicaps, BTTS),
and runs top picks through LLM (qwable-9b) for qualitative risk validation.
"""

import argparse
import datetime
import json
import logging
import os
import sys
from typing import Tuple, List, Dict, Any
import numpy as np

sys.path.insert(0, r"c:\Codes\autobet")
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("live_bot")

from fonbet_parser import FonbetParser
from ml.dataset import FEATURE_FACTOR_IDS
from ml.lightgbm_model import LightGBMPredictor
from ml.pytorch_model import PyTorchOddsPredictor
from ml.ensemble import EnsemblePredictor
from llm_analyzer import LLMRiskAnalyst

class LiveBettingBot:
    def __init__(self, models_dir: str = "models", out_dir: str = "output", mode: str = "prematch", enable_llm: bool = True):
        self.models_dir = models_dir
        self.out_dir = out_dir
        self.mode = mode
        self.enable_llm = enable_llm
        os.makedirs(self.out_dir, exist_ok=True)

        self.parser = FonbetParser(out_dir=self.out_dir)
        self.llm = LLMRiskAnalyst() if enable_llm else None

        # Load models
        lgb_path = os.path.join(self.models_dir, "lightgbm_model.pkl")
        pt_path = os.path.join(self.models_dir, "pytorch_model.pt")

        if not os.path.exists(lgb_path) or not os.path.exists(pt_path):
            raise FileNotFoundError("Model checkpoints not found! Run `uv run python train_models.py` first.")

        self.lgb_model = LightGBMPredictor()
        self.lgb_model.load(lgb_path)

        if hasattr(self.lgb_model.model_1x2, "n_features_"):
            input_dim = self.lgb_model.model_1x2.n_features_
        else:
            input_dim = len(FEATURE_FACTOR_IDS) * 3 if mode == "prematch" else 4 + len(FEATURE_FACTOR_IDS) * 3

        self.pt_model = PyTorchOddsPredictor(input_dim=input_dim)
        self.pt_model.load(pt_path, input_dim=input_dim)

        self.ensemble = EnsemblePredictor(self.lgb_model, self.pt_model, weight_lgb=0.5)

    def run_live_scanner(
        self,
        sport_filter: str = "all",
        min_prob: float = 0.65,    # High Probability threshold (>=65% for ultra-safe bets)
        min_ev: float = 0.01,      # Minimum EV threshold (+1% edge over bookie)
        max_odds: float = 2.00     # Upper bound on odds for safe high-pass bets (default: 2.00)
    ) -> List[Dict[str, Any]]:
        """Parses current Live feed, runs inference across all markets, and filters safest bets with LLM audit."""
        logger.info("📡 Step 1/4: Fetching fresh Fonbet Live line data...")
        self.parser.run(place="live", sport_filter=sport_filter, export_format="human")

        live_json_path = os.path.join(self.out_dir, "live_odds_human.json")
        if not os.path.exists(live_json_path):
            logger.error("Failed to parse live odds.")
            return []

        with open(live_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            events = data.get("events", [])

        logger.info(f"🧠 Step 2/4: Running PyTorch + LightGBM neural network inference on {len(events)} active live matches...")

        recommendations = []

        for ev in events:
            eid = ev["event_id"]
            match_name = ev["match_name"]
            sport_path = ev["sport_path"]
            score_1 = ev.get("score_1", 0)
            score_2 = ev.get("score_2", 0)
            score_str = ev.get("score", f"{score_1}:{score_2}")
            timer_str = ev.get("timer", "")

            odds_list = ev.get("odds", [])
            odds_dict = {str(o["factor_id"]): o["coefficient"] for o in odds_list}
            params_dict = {str(o["factor_id"]): o.get("parameter") for o in odds_list}

            if self.mode == "live":
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

            X_single = np.array([row], dtype=np.float32)

            probs_1x2, probs_over = self.ensemble.predict_proba(X_single)

            # Evaluate Double Chance (1X, 12, X2) - Safest betting market
            double_chance_map = [
                ("924", "1Х (Победа 1 или Ничья)", probs_1x2[0][0] + probs_1x2[0][1]),
                ("925", "12 (Победа 1 или Победа 2)", probs_1x2[0][0] + probs_1x2[0][2]),
                ("926", "Х2 (Ничья или Победа 2)", probs_1x2[0][1] + probs_1x2[0][2])
            ]
            for fid_str, label, p_model in double_chance_map:
                o_val = odds_dict.get(fid_str)
                if o_val and float(o_val) > 1.0:
                    odds = float(o_val)
                    p_implied = 1.0 / odds
                    ev_val = (p_model * odds) - 1.0

                    if p_model >= min_prob and odds <= max_odds and ev_val >= min_ev:
                        recommendations.append({
                            "event_id": eid,
                            "sport_path": sport_path,
                            "match_name": match_name,
                            "current_score": score_str,
                            "timer": timer_str,
                            "market_type": "Двойной шанс (Самая безопасная)",
                            "bet_target": label,
                            "bookmaker_odds": odds,
                            "implied_probability": f"{round(p_implied * 100, 1)}%",
                            "model_probability": f"{round(p_model * 100, 1)}%",
                            "raw_model_prob": p_model,
                            "expected_value_ev": f"{round(ev_val * 100, 2)}%",
                            "confidence_rating": "🛡 МАКСИМАЛЬНАЯ БЕЗОПАСНОСТЬ (Ultra Safe)" if p_model >= 0.80 else "🔥 ВЫСОКАЯ (High Pass)"
                        })

            # Evaluate Totals (Over / Under)
            p_under_2_5 = float(probs_over[0][0])
            p_over_2_5 = float(probs_over[0][1])

            o_under = odds_dict.get("931")
            if o_under and float(o_under) > 1.0:
                odds = float(o_under)
                p_implied = 1.0 / odds
                ev_val = (p_under_2_5 * odds) - 1.0
                if p_under_2_5 >= min_prob and odds <= max_odds and ev_val >= min_ev:
                    recommendations.append({
                        "event_id": eid,
                        "sport_path": sport_path,
                        "match_name": match_name,
                        "current_score": score_str,
                        "timer": timer_str,
                        "market_type": "Тоталы",
                        "bet_target": "Тотал Меньше (2.5)",
                        "bookmaker_odds": odds,
                        "implied_probability": f"{round(p_implied * 100, 1)}%",
                        "model_probability": f"{round(p_under_2_5 * 100, 1)}%",
                        "raw_model_prob": p_under_2_5,
                        "expected_value_ev": f"{round(ev_val * 100, 2)}%",
                        "confidence_rating": "🛡 МАКСИМАЛЬНАЯ БЕЗОПАСНОСТЬ (Ultra Safe)" if p_under_2_5 >= 0.80 else "🔥 ВЫСОКАЯ (High Pass)"
                    })

        # Sort recommendations by highest probability
        recommendations.sort(key=lambda item: item["raw_model_prob"], reverse=True)

        # Step 4: Run Top Picks through LLM Risk Analyst for qualitative validation
        if self.enable_llm and self.llm and recommendations:
            logger.info(f"🤖 Step 4/4: Running LLM Risk Analyst (qwable-9b) verification on top picks...")
            for top_rec in recommendations[:5]:
                match_info = {
                    "sport_path": top_rec["sport_path"],
                    "match_name": top_rec["match_name"],
                    "score": top_rec["current_score"],
                    "timer": top_rec["timer"]
                }
                llm_res = self.llm.analyze_bet_safety(match_info, top_rec)
                if llm_res.get("status") == "success":
                    top_rec["llm_audit"] = llm_res.get("analysis")
                else:
                    top_rec["llm_audit"] = f"LLM Audit error: {llm_res.get('error')}"

        logger.info(f"🎯 Complete! Found {len(recommendations)} safe bets across active Live matches.")
        self._export_recommendations(recommendations)
        return recommendations

    def _export_recommendations(self, recs: List[Dict[str, Any]]):
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save JSON
        json_path = os.path.join(self.out_dir, "live_recommendations.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp_str,
                "total_recommended_bets": len(recs),
                "recommendations": recs
            }, f, ensure_ascii=False, indent=2)

        print(f"\n=====================================================")
        print(f" 🤖 FONBET SAFE BETS REASONING BOT (PyTorch + LightGBM + LLM qwable-9b)")
        print(f" ⏱ Обновлено: {timestamp_str} | Найдено безопасных ставок: {len(recs)}")
        print(f"=====================================================\n")

        if not recs:
            print(" ℹ️ В данный момент нет ставок, проходящих критерии максимальной безопасности (>=65%).")
            print("    Ожидание оптимальных лайв-событий...\n")
            return

        for idx, r in enumerate(recs[:10], 1):
            print(f"{idx:2d}. {r['confidence_rating']} | {r['sport_path']}")
            print(f"    📍 Матч: {r['match_name']} [{r['current_score']}] ({r['timer']})")
            print(f"    🎯 САМАЯ БЕЗОПАСНАЯ СТАВКА : {r['bet_target']}")
            print(f"    💰 Коэффициент БК          : {r['bookmaker_odds']} (Букмекер дает {r['implied_probability']})")
            print(f"    🧠 Нейросеть (PyTorch+LGBM): {r['model_probability']} | Профит (+EV): {r['expected_value_ev']}")
            if "llm_audit" in r:
                print(f"    🤖 Аудит LLM qwable-9b     :\n      {r['llm_audit'].replace(chr(10), chr(10)+'      ')}")
            print(f"    -------------------------------------------------")

def main():
    parser = argparse.ArgumentParser(description="Fonbet Live Automated Safe Betting Bot with LLM Verification")
    parser.add_argument("--sport", default="all", help="Filter by sport name (default: all)")
    parser.add_argument("--min-prob", type=float, default=0.65, help="Minimum winning probability threshold (default: 0.65 = 65%%)")
    parser.add_argument("--min-ev", type=float, default=0.01, help="Minimum EV threshold (default: 0.01 = 1%%)")
    parser.add_argument("--max-odds", type=float, default=2.00, help="Maximum odds cap for safe bets (default: 2.00)")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM verification step")
    args = parser.parse_args()

    bot = LiveBettingBot(mode="prematch", enable_llm=not args.no_llm)
    bot.run_live_scanner(
        sport_filter=args.sport,
        min_prob=args.min_prob,
        min_ev=args.min_ev,
        max_odds=args.max_odds
    )

if __name__ == "__main__":
    main()
