"""
Fonbet Automated Odds Parser
Main entry point for scraping and exporting ALL Fonbet betting odds including sub-markets.
"""

import argparse
import datetime
import logging
import os
import sys
from typing import Dict, Any, List, Tuple

from fonbet_fetcher import FonbetFetcher
from fonbet_catalog import FonbetCatalog
from exporters.human_exporter import HumanExporter
from exporters.ai_exporter import AIExporter

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("fonbet_parser")

class FonbetParser:
    def __init__(self, out_dir: str = "output"):
        self.out_dir = out_dir
        self.fetcher = FonbetFetcher()
        self.catalog = FonbetCatalog()

    def _extract_live_score_and_timer(
        self, eid: int, ev: Dict[str, Any], live_infos_map: Dict[int, Any], event_miscs_map: Dict[int, Any]
    ) -> Tuple[int, int, str, str]:
        """Accurately retrieves current score (score_1, score_2), score string, and live timer."""
        misc = event_miscs_map.get(eid, {})
        live = live_infos_map.get(eid, {})

        s1 = misc.get("score1")
        s2 = misc.get("score2")

        if s1 is None or s2 is None:
            scores_arr = live.get("scores")
            if scores_arr and isinstance(scores_arr, list) and len(scores_arr) > 0:
                first_set = scores_arr[0]
                if isinstance(first_set, list) and len(first_set) > 0:
                    s1 = first_set[0].get("c1")
                    s2 = first_set[0].get("c2")

        if s1 is None:
            s1 = ev.get("score1", 0)
        if s2 is None:
            s2 = ev.get("score2", 0)

        try:
            s1_int = int(s1)
        except Exception:
            s1_int = 0

        try:
            s2_int = int(s2)
        except Exception:
            s2_int = 0

        score_str = f"{s1_int}:{s2_int}"
        timer_str = live.get("timer") or misc.get("comment") or ev.get("comment", "")

        return s1_int, s2_int, score_str, timer_str

    def _parse_factors_for_event(self, eid: int, market_prefix: str, custom_factors_map: Dict[int, Any]) -> List[Dict[str, Any]]:
        """Parses factors array for a given event ID."""
        raw_factors = custom_factors_map.get(eid, [])
        parsed = []
        for f in raw_factors:
            fid = f.get("f")
            val = f.get("v")
            pt = f.get("pt")
            p_raw = f.get("p")
            
            if fid is None or val is None:
                continue

            base_label = self.catalog.resolve_factor_label(fid, pt)
            full_label = f"{market_prefix} | {base_label}" if market_prefix else base_label

            parsed.append({
                "factor_id": fid,
                "label": full_label,
                "market_prefix": market_prefix,
                "parameter": pt if pt is not None else p_raw,
                "coefficient": val
            })
        return parsed

    def run(self, place: str = "live", sport_filter: str = "all", export_format: str = "all"):
        timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Starting Fonbet odds parser (place={place}, sport={sport_filter}, format={export_format})")

        # Step 1: Fetch live events data
        raw_data = self.fetcher.fetch_live_events(place=place)
        
        # Step 2: Fetch and build catalog metadata
        try:
            catalogs = self.fetcher.fetch_factors_catalog()
            self.catalog.build_catalog_from_responses(catalogs)
        except Exception as e:
            logger.warning(f"Could not load fresh factor catalog, using cached/fallback mapping: {e}")

        # Step 3: Parse sports hierarchy
        sports_map = {s["id"]: s for s in raw_data.get("sports", [])}
        
        def get_sport_path(sport_id: int) -> str:
            sport = sports_map.get(sport_id)
            if not sport:
                return "Другие виды спорта"
            names = [sport.get("name", "")]
            pid = sport.get("parentId")
            while pid and pid in sports_map:
                psport = sports_map[pid]
                names.append(psport.get("name", ""))
                pid = psport.get("parentId")
            return " / ".join(reversed([n for n in names if n]))

        # Step 4: Group events and sub-events
        events_map = {e["id"]: e for e in raw_data.get("events", [])}
        custom_factors_map = {cf["e"]: cf.get("factors", []) for cf in raw_data.get("customFactors", [])}
        live_infos_map = {li["eventId"]: li for li in raw_data.get("liveEventInfos", []) if "eventId" in li}
        event_miscs_map = {em["id"]: em for em in raw_data.get("eventMiscs", []) if "id" in em}

        sub_events_by_parent: Dict[int, List[Dict[str, Any]]] = {}
        for eid, ev in events_map.items():
            pid = ev.get("parentId")
            if pid:
                if pid not in sub_events_by_parent:
                    sub_events_by_parent[pid] = []
                sub_events_by_parent[pid].append(ev)

        parsed_events = []
        total_odds_parsed = 0

        for eid, ev in events_map.items():
            ev_place = ev.get("place", "live")
            if place != "all" and ev_place != place:
                continue

            # Process main events (kind == 1)
            kind = ev.get("kind", 1)
            if kind != 1 and ev.get("parentId"):
                # Sub-events are handled under their main parent
                continue

            sport_path = get_sport_path(ev.get("sportId"))

            if sport_filter.lower() != "all" and sport_filter.lower() not in sport_path.lower():
                continue

            t1 = ev.get("team1", "").strip()
            t2 = ev.get("team2", "").strip()
            match_name = f"{t1} — {t2}" if (t1 and t2) else (ev.get("name") or f"Event #{eid}")

            # Extract accurate score and timer
            s1_int, s2_int, score_str, timer_str = self._extract_live_score_and_timer(
                eid, ev, live_infos_map, event_miscs_map
            )

            # Main event odds
            all_match_odds = self._parse_factors_for_event(eid, "Основной матч", custom_factors_map)

            # Sub-events odds (1-й тайм, 2-й тайм, угловые, желтые карточки, сеты и т.д.)
            child_sub_events = sub_events_by_parent.get(eid, [])
            sub_markets_summary = []

            for se in child_sub_events:
                se_id = se["id"]
                se_name = se.get("name", "Доп. маркет")
                se_odds = self._parse_factors_for_event(se_id, se_name, custom_factors_map)
                if se_odds:
                    all_match_odds.extend(se_odds)
                    sub_markets_summary.append({
                        "sub_event_id": se_id,
                        "market_name": se_name,
                        "odds_count": len(se_odds)
                    })

            if not all_match_odds:
                continue

            total_odds_parsed += len(all_match_odds)

            parsed_events.append({
                "event_id": eid,
                "sport_id": ev.get("sportId"),
                "sport_path": sport_path,
                "match_name": match_name,
                "team_1": t1,
                "team_2": t2,
                "score_1": s1_int,
                "score_2": s2_int,
                "score": score_str,
                "timer": timer_str,
                "is_live": (ev_place == "live"),
                "sub_markets": sub_markets_summary,
                "total_odds_count": len(all_match_odds),
                "odds": all_match_odds
            })

        logger.info(f"Successfully parsed {len(parsed_events)} live matches with ALL sub-markets ({total_odds_parsed} total odds).")

        # Step 5: Export into requested formats
        created_files = {}

        if export_format in ["all", "human"]:
            logger.info("Generating human-readable exports (JSON, TXT, CSV, HTML)...")
            human_exp = HumanExporter(output_dir=self.out_dir)
            h_files = human_exp.export(parsed_events, timestamp_str)
            created_files.update(h_files)

        if export_format in ["all", "ai"]:
            logger.info("Generating neural-network & ML friendly exports (JSONL, JSON, Matrix CSV)...")
            ai_exp = AIExporter(output_dir=self.out_dir)
            vocab = {str(k): {"name": v} for k, v in self.catalog.factors_map.items()}
            ai_files = ai_exp.export(parsed_events, vocab, timestamp_str)
            created_files.update(ai_files)

        logger.info("Export complete! Files generated:")
        for fmt, filepath in created_files.items():
            logger.info(f"  [{fmt}] {os.path.abspath(filepath)}")

        return created_files

def main():
    parser = argparse.ArgumentParser(description="Fonbet Automated Odds Parser")
    parser.add_argument("--place", choices=["live", "line", "all"], default="live", help="Line segment (default: live)")
    parser.add_argument("--sport", default="all", help="Filter by sport name (e.g. Футбол, теннис, all)")
    parser.add_argument("--out-dir", default="output", help="Output directory for generated files")
    parser.add_argument("--format", choices=["all", "human", "ai"], default="all", help="Export format selection")
    args = parser.parse_args()

    app = FonbetParser(out_dir=args.out_dir)
    app.run(place=args.place, sport_filter=args.sport, export_format=args.format)

if __name__ == "__main__":
    main()
