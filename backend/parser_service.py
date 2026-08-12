import httpx
import logging
import datetime
import os
import json
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("parser_service")

CDN_HOSTS = [
    "line-lb61-w.bk6bba-resources.com",
    "line-lb54-w.bk6bba-resources.com",
    "line-lb01.bk6bba-resources.com",
    "line01.bkfonbet.com"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://fon.bet",
    "Referer": "https://fon.bet/live"
}

CORE_FACTOR_MAP: Dict[int, str] = {
    # Main outcomes
    921: "П1",
    922: "Х",
    923: "П2",
    924: "1Х",
    925: "12",
    926: "Х2",
    
    # Handicaps
    927: "Фора 1",
    928: "Фора 2",
    910: "Фора 1",
    912: "Фора 2",
    989: "Фора 1",
    991: "Фора 2",
    1569: "Фора 1",
    1572: "Фора 2",
    1677: "Фора 2",
    1678: "Фора 1",
    1680: "Фора 2",
    1681: "Фора 1",
    
    # Totals
    930: "Тотал Больше",
    931: "Тотал Меньше",
    1696: "Тотал Больше",
    1697: "Тотал Меньше",
    1727: "Тотал Больше",
    1728: "Тотал Меньше",
    1730: "Тотал Больше",
    1731: "Тотал Меньше",
    1733: "Тотал Больше",
    1734: "Тотал Меньше",
    1736: "Тотал Больше",
    1737: "Тотал Меньше",
    1739: "Тотал Больше",
    1740: "Тотал Меньше",
    1791: "Тотал Больше",
    1793: "Тотал Меньше",
    1794: "Тотал Больше",
    1796: "Тотал Меньше",
    1797: "Тотал Больше",
    1802: "Тотал Меньше",
    1804: "Тотал Больше",
    1805: "Тотал Меньше",

    # Individual Totals
    1809: "Индивидуальный тотал 1 Больше",
    1810: "Индивидуальный тотал 1 Меньше",
    1812: "Индивидуальный тотал 1 Больше",
    1813: "Индивидуальный тотал 1 Меньше",
    1815: "Индивидуальный тотал 1 Больше",
    1816: "Индивидуальный тотал 1 Меньше",
    1818: "Индивидуальный тотал 1 Больше",
    1819: "Индивидуальный тотал 1 Меньше",
    1821: "Индивидуальный тотал 1 Больше",
    1822: "Индивидуальный тотал 1 Меньше",
    
    1854: "Индивидуальный тотал 2 Больше",
    1855: "Индивидуальный тотал 2 Меньше",
    1857: "Индивидуальный тотал 2 Больше",
    1858: "Индивидуальный тотал 2 Меньше",
    1860: "Индивидуальный тотал 2 Больше",
    1861: "Индивидуальный тотал 2 Меньше",
    
    1871: "Обе забьют: Да",
    1873: "Обе забьют: Нет",
    1874: "Обе забьют: Да",
    1880: "Обе забьют: Нет",
    1881: "Обе забьют: Да",
    
    2820: "Проход: Команда 1",
    2821: "Проход: Команда 2"
}

class FonbetParserService:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.client = httpx.Client(headers=HEADERS, timeout=self.timeout, follow_redirects=True)
        self.active_host = CDN_HOSTS[0]
        self.factors_map: Dict[int, str] = {}
        self.factors_group_map: Dict[int, str] = {}

    def fetch_live_events(self) -> Dict[str, Any]:
        endpoint = "ma/events/listBase?lang=ru&scopeMarket=1600&place=live"
        last_error = None
        for host in CDN_HOSTS:
            url = f"https://{host}/{endpoint}"
            try:
                logger.info(f"Querying Fonbet LIVE API: {url}")
                resp = self.client.get(url)
                if resp.status_code == 200:
                    self.active_host = host
                    data = resp.json()
                    if isinstance(data, dict) and "events" in data:
                        return data
            except Exception as e:
                logger.warning(f"Failed host {host}: {e}")
                last_error = e

        raise RuntimeError(f"Could not fetch Fonbet data from any host. Last error: {last_error}")

    def fetch_factors_catalog(self) -> List[Dict[str, Any]]:
        catalogs = []
        endpoints = [
            "ma/line/factorsCatalog/tables?version=0&lang=ru&sysId=21",
            "ma/line/factorsCatalog/sportBasicFactors?version=0&lang=ru&systemType=desktop",
            "ma/line/factorsCatalog/independentFactors?version=0&lang=ru&sysId=21&scopeMarket=1600"
        ]
        for ep in endpoints:
            url = f"https://{self.active_host}/{ep}"
            try:
                resp = self.client.get(url)
                if resp.status_code == 200:
                    catalogs.append(resp.json())
            except Exception as e:
                logger.warning(f"Catalog fetch error {ep}: {e}")
        return catalogs

    def update_catalog_responses(self, items_or_data):
        if not isinstance(items_or_data, list):
            items_or_data = [items_or_data]

        def process_node(node, current_path=""):
            if isinstance(node, dict):
                fid = node.get("factorId") or node.get("f") or node.get("id")
                fname = node.get("name") or node.get("title") or node.get("caption") or node.get("shortName")
                new_path = f"{current_path} | {fname}" if (fname and current_path) else (fname or current_path)
                
                if fid and isinstance(fid, (int, str)) and str(fid).isdigit():
                    int_fid = int(fid)
                    if fname and int_fid not in self.factors_map:
                        self.factors_map[int_fid] = str(fname)
                    if new_path and int_fid not in self.factors_group_map:
                        self.factors_group_map[int_fid] = str(new_path)

                for k, v in node.items():
                    process_node(v, new_path)
            elif isinstance(node, list):
                for item in node:
                    process_node(item, current_path)

        for data in items_or_data:
            process_node(data)

    def resolve_factor_label(self, factor_id: int, param_str: Optional[str] = None) -> str:
        title = CORE_FACTOR_MAP.get(factor_id) or self.factors_group_map.get(factor_id) or self.factors_map.get(factor_id) or f"Исход {factor_id}"
        
        if param_str is not None and str(param_str).strip() != "":
            p_clean = str(param_str).strip()
            if "(" in title and ")" in title:
                return title
            return f"{title} ({p_clean})"
        return title

    def _extract_live_score_and_timer(
        self, eid: int, ev: Dict[str, Any], live_infos_map: Dict[int, Any], event_miscs_map: Dict[int, Any]
    ) -> Tuple[int, int, str, str]:
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
        timer_str = str(live.get("timer") or misc.get("comment") or ev.get("comment", ""))

        return s1_int, s2_int, score_str, timer_str

    def _parse_factors_for_event(self, eid: int, market_prefix: str, custom_factors_map: Dict[int, Any]) -> List[Dict[str, Any]]:
        raw_factors = custom_factors_map.get(eid, [])
        parsed = []
        for f in raw_factors:
            fid = f.get("f")
            val = f.get("v")
            pt = f.get("pt")
            p_raw = f.get("p")
            
            if fid is None or val is None:
                continue

            param_val = pt if pt is not None else p_raw
            base_label = self.resolve_factor_label(fid, param_val)
            full_label = f"{market_prefix} | {base_label}" if market_prefix else base_label

            parsed.append({
                "factor_id": fid,
                "label": base_label,
                "full_label": full_label,
                "market_prefix": market_prefix,
                "parameter": str(param_val) if param_val is not None else "",
                "coefficient": float(val)
            })
        return parsed

    def parse_live(self) -> List[Dict[str, Any]]:
        raw_data = self.fetch_live_events()

        try:
            catalogs = self.fetch_factors_catalog()
            self.update_catalog_responses(catalogs)
        except Exception as e:
            logger.warning(f"Catalog update skipped: {e}")

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

        for eid, ev in events_map.items():
            ev_place = ev.get("place", "live")
            if ev_place != "live":
                continue

            kind = ev.get("kind", 1)
            if kind != 1 and ev.get("parentId"):
                # Sub-events handled under parent
                continue

            sport_path = get_sport_path(ev.get("sportId"))

            t1 = ev.get("team1", "").strip()
            t2 = ev.get("team2", "").strip()
            match_name = f"{t1} — {t2}" if (t1 and t2) else (ev.get("name") or f"Event #{eid}")

            s1_int, s2_int, score_str, timer_str = self._extract_live_score_and_timer(
                eid, ev, live_infos_map, event_miscs_map
            )

            all_match_odds = self._parse_factors_for_event(eid, "Основной матч", custom_factors_map)

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
                "is_live": True,
                "sub_markets": sub_markets_summary,
                "total_odds_count": len(all_match_odds),
                "odds": all_match_odds
            })

        logger.info(f"Parsed {len(parsed_events)} live matches from Fonbet.")
        return parsed_events
