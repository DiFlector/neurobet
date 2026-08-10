"""
Fonbet Results & Bet Settlement Fetcher
Fetches finished match results and evaluates winning outcomes for neural net training.
"""

import argparse
import datetime
import json
import logging
import os
import sys
import httpx
from typing import Dict, Any, List, Optional, Tuple

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("fonbet_results")

CDN_HOSTS = [
    "clientsapi-lb54-w.bk6bba-resources.com",
    "clientsapi-lb61-w.bk6bba-resources.com",
    "clientsapi.bkfonbet.com"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://fon.bet",
    "Referer": "https://fon.bet/results"
}

class FonbetResults:
    def __init__(self, out_dir: str = "output"):
        self.out_dir = out_dir
        os.makedirs(self.out_dir, exist_ok=True)
        self.client = httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True)

    def fetch_results_by_date(self, date_str: str) -> Dict[int, Dict[str, Any]]:
        """Fetches all finished events for a given YYYY-MM-DD date."""
        last_error = None
        for host in CDN_HOSTS:
            url = f"https://{host}/results/v2/getByDate?lang=ru&lineDate={date_str}&scopeMarket=1600"
            try:
                logger.info(f"Querying Fonbet Results API: {url}")
                resp = self.client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return self._process_raw_results(data, date_str)
            except Exception as e:
                logger.warning(f"Error fetching results from {host}: {e}")
                last_error = e

        raise RuntimeError(f"Could not fetch results from any host. Last error: {last_error}")

    def _process_raw_results(self, data: Dict[str, Any], date_str: str) -> Dict[int, Dict[str, Any]]:
        sports_map = {str(s["id"]): s.get("name", "") for s in data.get("sports", [])}
        comps_map = {str(c["id"]): c for c in data.get("competitions", [])}
        events_list = data.get("events", [])
        miscs_map = {str(m["id"]): m for m in data.get("eventMiscs", [])}

        results_by_id = {}
        for ev in events_list:
            eid = int(ev["id"])
            status = ev.get("status")
            misc = miscs_map.get(str(eid)) or {}

            comp_id = ev.get("competitionId")
            comp = comps_map.get(str(comp_id)) or {}
            sport_name = sports_map.get(str(comp.get("sportId")), "Спорт")
            comp_name = comp.get("name", "")
            sport_path = f"{sport_name} / {comp_name}" if comp_name else sport_name

            s1 = misc.get("score1")
            s2 = misc.get("score2")
            sub_scores = misc.get("subScores", [])

            if s1 is not None and s2 is not None:
                results_by_id[eid] = {
                    "event_id": eid,
                    "date": date_str,
                    "sport_path": sport_path,
                    "match_name": ev.get("name") or f"{ev.get('team1')} – {ev.get('team2')}",
                    "team_1": ev.get("team1", ""),
                    "team_2": ev.get("team2", ""),
                    "is_finished": (status == 2),
                    "score_1": s1,
                    "score_2": s2,
                    "score": f"{s1}:{s2}",
                    "sub_scores": sub_scores,
                    "first_goal": misc.get("firstGoal")
                }

        logger.info(f"Loaded {len(results_by_id)} finished match results for {date_str}.")
        return results_by_id

    def evaluate_factor_outcome(self, factor_id: int, param: Optional[str], score_1: int, score_2: int) -> str:
        """
        Evaluates factor outcome based on final scores:
        Returns 'win', 'loss', or 'void'.
        """
        # 1X2 outcomes
        if factor_id == 921:  # Win 1
            return "win" if score_1 > score_2 else "loss"
        if factor_id == 922:  # Draw
            return "win" if score_1 == score_2 else "loss"
        if factor_id == 923:  # Win 2
            return "win" if score_2 > score_1 else "loss"
        if factor_id == 924:  # 1X
            return "win" if score_1 >= score_2 else "loss"
        if factor_id == 925:  # 12
            return "win" if score_1 != score_2 else "loss"
        if factor_id == 926:  # X2
            return "win" if score_2 >= score_1 else "loss"

        # Both teams to score
        if factor_id in [1871, 1874, 1881]:  # BTTS Yes
            return "win" if score_1 > 0 and score_2 > 0 else "loss"
        if factor_id in [1873, 1880]:        # BTTS No
            return "win" if score_1 == 0 or score_2 == 0 else "loss"

        # Numeric parameter factors (Handicap / Total)
        p_val = None
        if param is not None:
            try:
                p_val = float(str(param).replace("+", ""))
            except ValueError:
                pass

        if p_val is not None:
            # Handicaps
            if factor_id in [927, 910, 989, 1569, 1678, 1681]:  # Handicap 1
                diff = (score_1 + p_val) - score_2
                if diff > 0: return "win"
                if diff == 0: return "void"
                return "loss"

            if factor_id in [928, 912, 991, 1572, 1677, 1680]:  # Handicap 2
                diff = (score_2 + p_val) - score_1
                if diff > 0: return "win"
                if diff == 0: return "void"
                return "loss"

            # Totals
            if factor_id in [930, 1697, 1728, 1731, 1734, 1736, 1739, 1793, 1796]:  # Total Over
                total = score_1 + score_2
                if total > p_val: return "win"
                if total == p_val: return "void"
                return "loss"

            if factor_id in [931, 1696, 1727, 1730, 1733, 1737, 1791, 1794, 1797]:  # Total Under
                total = score_1 + score_2
                if total < p_val: return "win"
                if total == p_val: return "void"
                return "loss"

            # Individual Totals 1
            if factor_id in [1809, 1812, 1815, 1818, 1821]:  # Ind Total 1 Over
                if score_1 > p_val: return "win"
                if score_1 == p_val: return "void"
                return "loss"

            if factor_id in [1810, 1813, 1816, 1819, 1822]:  # Ind Total 1 Under
                if score_1 < p_val: return "win"
                if score_1 == p_val: return "void"
                return "loss"

        return "unknown"

    def match_and_export_settlements(self, date_str: str, odds_file: str = "output/live_odds_ai.json"):
        """Matches parsed odds with finished results and generates ML settlement dataset."""
        results_by_id = self.fetch_results_by_date(date_str)

        odds_events = []
        if os.path.exists(odds_file):
            with open(odds_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                odds_events = data.get("events", [])

        settled_events = []
        matched_count = 0

        for odds_ev in odds_events:
            eid = odds_ev["event_id"]
            res = results_by_id.get(eid)
            
            if res:
                matched_count += 1
                s1 = res["score_1"]
                s2 = res["score_2"]

                outcomes_vector = {}
                winning_factors = []

                for fid_str, coeff in odds_ev.get("odds_vector", {}).items():
                    fid = int(fid_str)
                    param = odds_ev.get("odds_params", {}).get(fid_str)
                    outcome = self.evaluate_factor_outcome(fid, param, s1, s2)
                    outcomes_vector[fid_str] = outcome
                    if outcome == "win":
                        winning_factors.append(fid)

                settled_events.append({
                    "event_id": eid,
                    "date": date_str,
                    "sport_path": odds_ev.get("sport_path"),
                    "match_name": odds_ev.get("match_name"),
                    "team_1": odds_ev.get("team_1"),
                    "team_2": odds_ev.get("team_2"),
                    "final_score": res["score"],
                    "score_1": s1,
                    "score_2": s2,
                    "sub_scores": res.get("sub_scores", []),
                    "odds_vector": odds_ev.get("odds_vector", {}),
                    "odds_params": odds_ev.get("odds_params", {}),
                    "outcomes_vector": outcomes_vector,  # { "921": "win", "922": "loss" }
                    "winning_factors": winning_factors
                })

        # Save human-readable results
        res_human_path = os.path.join(self.out_dir, "results_human.json")
        with open(res_human_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": date_str,
                "total_finished_matches": len(results_by_id),
                "matched_with_odds": matched_count,
                "results": list(results_by_id.values())
            }, f, ensure_ascii=False, indent=2)

        # Save settled ML dataset
        res_ai_path = os.path.join(self.out_dir, "settled_dataset_ai.jsonl")
        with open(res_ai_path, "w", encoding="utf-8") as f:
            for item in settled_events:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        logger.info(f"Settlement complete. Matched {matched_count} events with odds. Output saved to {res_human_path} and {res_ai_path}")
        return {
            "human_results": res_human_path,
            "settled_ai_dataset": res_ai_path
        }

def main():
    parser = argparse.ArgumentParser(description="Fonbet Match Results & Settlement Fetcher")
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    parser.add_argument("--date", default=today_str, help=f"Date YYYY-MM-DD (default: {today_str})")
    parser.add_argument("--out-dir", default="output", help="Output directory")
    args = parser.parse_args()

    app = FonbetResults(out_dir=args.out_dir)
    app.match_and_export_settlements(date_str=args.date)

if __name__ == "__main__":
    main()
