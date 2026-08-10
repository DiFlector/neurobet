"""
Fonbet 2-Year Historical Data Downloader & Parser
Fetches 730 days (2 full years) of finished sports match data, scores, sub-scores, and outcome labels.
"""

import argparse
import asyncio
import datetime
import json
import logging
import os
import sys
import time
from typing import Dict, Any, List
import httpx

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("historical_scraper")

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

class HistoricalScraper:
    def __init__(self, out_dir: str = "history_db", max_concurrent: int = 15):
        self.out_dir = out_dir
        self.max_concurrent = max_concurrent
        os.makedirs(self.out_dir, exist_ok=True)

    async def _fetch_date_async(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, date_str: str) -> List[Dict[str, Any]]:
        url = f"https://{CDN_HOSTS[0]}/results/v2/getByDate?lang=ru&lineDate={date_str}&scopeMarket=1600"
        async with semaphore:
            for attempt in range(3):
                try:
                    resp = await client.get(url, timeout=12.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        return self._extract_events_from_payload(data, date_str)
                except Exception as e:
                    await asyncio.sleep(0.5)
        return []

    def _extract_events_from_payload(self, data: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
        sports_map = {str(s["id"]): s.get("name", "") for s in data.get("sports", [])}
        comps_map = {str(c["id"]): c for c in data.get("competitions", [])}
        events_list = data.get("events", [])
        miscs_map = {str(m["id"]): m for m in data.get("eventMiscs", [])}

        parsed_day_events = []
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

            if s1 is not None and s2 is not None:
                # Calculate outcome winner: 0: Win 1, 1: Draw, 2: Win 2
                if s1 > s2:
                    winner = 0
                elif s1 == s2:
                    winner = 1
                else:
                    winner = 2

                parsed_day_events.append({
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
                    "winner_1x2": winner,
                    "total_goals": s1 + s2,
                    "is_over_2_5": (s1 + s2 > 2.5),
                    "sub_scores": misc.get("subScores", [])
                })
        return parsed_day_events

    async def scrape_years(self, num_years: int = 2):
        today = datetime.date.today()
        days_to_scrape = num_years * 365
        date_list = [(today - datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_to_scrape)]

        logger.info(f"Starting async download for {days_to_scrape} days ({num_years} years: {date_list[-1]} to {date_list[0]})...")
        t0 = time.time()

        semaphore = asyncio.Semaphore(self.max_concurrent)
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
            tasks = [self._fetch_date_async(client, semaphore, d) for d in date_list]
            results_nested = await asyncio.gather(*tasks)

        all_events = [ev for day_events in results_nested for ev in day_events]
        t1 = time.time()

        logger.info(f"Scraped {len(all_events)} matches across {days_to_scrape} days in {t1 - t0:.2f} seconds!")

        # Save to JSONL dataset
        out_jsonl = os.path.join(self.out_dir, f"fonbet_history_{num_years}years.jsonl")
        with open(out_jsonl, "w", encoding="utf-8") as f:
            for ev in all_events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        logger.info(f"Saved 2-year dataset to {out_jsonl}")
        return out_jsonl

def main():
    parser = argparse.ArgumentParser(description="Fonbet 2-Year Historical Data Downloader")
    parser.add_argument("--years", type=int, default=2, help="Number of historical years to scrape (default: 2)")
    parser.add_argument("--out-dir", default="history_db", help="Output directory for historical data")
    args = parser.parse_args()

    scraper = HistoricalScraper(out_dir=args.out_dir)
    asyncio.run(scraper.scrape_years(num_years=args.years))

if __name__ == "__main__":
    main()
