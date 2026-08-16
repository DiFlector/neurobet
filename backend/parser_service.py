import httpx
import logging
import datetime
import os
import json
import re
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
    # 925/1571 were swapped here for a long time: Fonbet's own catalog
    # (factorsCatalog/sportBasicFactors, market "Двойные шансы") maps
    # factor 925 -> "X2" and factor 1571 -> "12", not the other way around. That
    # swap meant the bot was placing/showing "12" bets that were actually the "X2"
    # market (and vice versa) — confirmed against live data across multiple
    # matches/sports where our stored "925=12" coefficient matched Fonbet's
    # displayed X2 price, not its 12 price. factor 926 has never once appeared in
    # any live payload observed so far — kept mapped in case it's a legacy/rare id,
    # but it's not what "X2" actually comes through as.
    925: "Х2",
    926: "Х2",
    1571: "12",

    # Handicaps
    927: "Фора 1",
    928: "Фора 2",
    910: "Фора 1",
    912: "Фора 2",
    989: "Фора 1",
    991: "Фора 2",
    1569: "Фора 1",
    1572: "Фора 2",
    # 1677/1678 and 1680/1681 were swapped — same class of bug as 925/1571 (see above),
    # confirmed against factorsCatalog/sportBasicFactors market "Форы": 1677/1680 ->
    # "ФОРА 1", 1678/1681 -> "ФОРА 2".
    1677: "Фора 1",
    1678: "Фора 2",
    1680: "Фора 1",
    1681: "Фора 2",

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
    # 1791/1793, 1794/1796, 1797/1802, and 1805 (paired with 1804) were also swapped —
    # confirmed against factorsCatalog market "Тоталы": 1791/1794/1797 -> "М" (Меньше),
    # 1793/1796/1802/1805 -> "Б"/"Тотал" (Больше).
    1791: "Тотал Меньше",
    1793: "Тотал Больше",
    1794: "Тотал Меньше",
    1796: "Тотал Больше",
    1797: "Тотал Меньше",
    1802: "Тотал Больше",
    # 1804 isn't independently confirmed in the fetched catalog (not present in any of
    # the 3 endpoints checked), but 1805 is confirmed "Б" (Больше) — going by every
    # other pair in this map, 1804/1805 read as a matched Больше/Меньше pair, so 1804
    # is inferred as Меньше by elimination. Flagged for re-verification if a live bet
    # on 1804 ever looks wrong the way 925/1571 etc. did.
    1804: "Тотал Меньше",
    1805: "Тотал Больше",

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
    # 1871/1873/1874/1880/1881 were mislabeled "Обе забьют" for a long time — Fonbet's
    # own catalog (factorsCatalog/sportBasicFactors, market "Инд. тотал 2") shows these
    # are actually individual-total-2 over/under factors, an entirely different market
    # with a different true probability. Confirmed against a live water-polo match where
    # the bot placed a real-value "Обе забьют: Да (6.5)" bet on a market Fonbet doesn't
    # even offer for that sport — (6.5) as a BTTS parameter was the tell, BTTS is always
    # parameter-less. The real "Обе забьют" factors are 4241 (Да) / 4242 (Нет).
    1873: "Индивидуальный тотал 2 Больше",
    1880: "Индивидуальный тотал 2 Больше",
    1871: "Индивидуальный тотал 2 Меньше",
    1874: "Индивидуальный тотал 2 Меньше",
    1881: "Индивидуальный тотал 2 Меньше",
    4241: "Обе забьют: Да",
    4242: "Обе забьют: Нет",

    2820: "Проход: Команда 1",
    2821: "Проход: Команда 2",

    # Fonbet's own catalog gives both sides of this market the exact same name
    # ("Итоговая победа"), with nothing to tell them apart — unlike П1/П2 or Фора 1/
    # Фора 2, where the team is baked into the label text itself. Confirmed real user
    # confusion over this (a bet just says "Итоговая победа" with no side shown), so we
    # override with an explicit side here rather than relaying Fonbet's ambiguous text
    # as-is. See FACTORS_FINAL_WIN1/2 in database.py for the matching factor_id sets.
    7035: "Итоговая победа: П1", 933: "Итоговая победа: П1", 7348: "Итоговая победа: П1",
    16617: "Итоговая победа: П1", 16680: "Итоговая победа: П1", 17036: "Итоговая победа: П1",
    7036: "Итоговая победа: П2", 934: "Итоговая победа: П2", 7349: "Итоговая победа: П2",
    16618: "Итоговая победа: П2", 16681: "Итоговая победа: П2", 17037: "Итоговая победа: П2",
}

# Factors that we deliberately never parse at all — not shown, not bet on, not trained
# on. "Очко %P" (factorsCatalog "tables" group "Основные ставки", table #7800) is "who's
# leading once the set's point count reaches P" — ungradable in principle for us: it
# needs the score at the exact moment total points hit P, and we only ever see periodic
# snapshots of each set's *final* score, never a point-by-point log. Confirmed present
# in real data only as 3024-3031 (table tennis), but excluding the full family (other
# sports' variants of the same "Очко %P" row from the catalog) so it's caught wherever
# Fonbet offers it, not just where we've happened to see it so far.
EXCLUDED_FACTOR_IDS = {
    3024, 3025, 3027, 3028, 3030, 3031, 3033, 3034, 3036, 3037, 3039, 3040,
    9774, 9775, 9777, 9778, 9780, 9781, 9784, 9785, 9787, 9788, 9790, 9791,
    15321, 15322, 15324, 15325, 15327, 15328, 15330, 15331, 15333, 15334,
    15337, 15338, 15340, 15341, 15343, 15344, 15346, 15347, 15349, 15350,
    16549, 16550, 16551, 16552, 16554, 16555, 16556, 16557, 16559, 16560, 16561, 16562,

    # "Кто выиграет гейм %P" (tennis, factorsCatalog "tables" row under "Геймы") — same
    # class of problem as "Очко %P" above: needs the score at the exact moment a specific
    # game number is reached within the set, but we only ever see each set's periodic
    # *final* score (period_scores), never a game-by-game log. Confirmed via real bets
    # that sat permanently "не рассчитана" under the generic fallback label "Основные
    # ставки (N)" (factors_group_map breadcrumb, since these aren't in CORE_FACTOR_MAP).
    1609, 1610, 1747, 1748, 1750, 1751, 1753, 1754, 9961, 9962,

    # NOTE: 1848/1849 ("Основные ставки | Тотал") was excluded here for a while on the
    # assumption a points-total line was unresolvable for sports scored in sets — turned
    # out wrong: period_scores for these sports (see PERIODIC_POINT_SPORTS in
    # database.py) already holds each set's own point score, so summing it gives the
    # true match-wide point total. It's graded now (FACTORS_TOTAL_OVER/UNDER in
    # database.py) instead of excluded.

    # "Кто выше" (16154/16155) — swimming/cycling stage "who places higher" market.
    # There's no score concept for it at all: score_1/score_2 stay 0:0 for the whole
    # event (it's a race/standings result, not a scoreline), so nothing in our data model
    # can ever resolve it. Confirmed: only ever seen under "... Кто выше?" sport_path
    # entries (плавание/велоспорт), never a real head-to-head match.
    16154, 16155,

    # "Гейм %P" (tennis, catalog table under "Основные ставки") — same class as "Кто
    # выиграет гейм %P" above (which turned out to only cover 5 of the real rows): who's
    # leading once a specific game number is reached, needing a point-by-point log within
    # that game we don't have. Confirmed via real history: sat permanently "не рассчитана"
    # under the generic fallback label "Основные ставки (N - очко M)".
    2995, 2996, 2998, 2999, 3001, 3002, 3004, 3005, 3007, 3008, 3010, 3011,

    # Tiebreaks (671/673), setballs (914/915), matchballs (675/677) and card counts
    # (3274/3275, 9886/9887) — all rows of the same "Показатели" catalog table as
    # "Кол-во сетов" (see FACTORS_SETS_TOTAL_OVER/UNDER in database.py), but for counts
    # we don't track anywhere at all (no tiebreak/setball/matchball/card event log, only
    # the sets tally that "Кол-во сетов" itself uses) — permanently unresolvable.
    671, 673, 914, 915, 675, 677, 3274, 3275, 9886, 9887,

    # "Проход" (advancing out of a two-legged tie) — the outcome depends on the
    # aggregate score across BOTH legs (plus away-goals/ET/penalties if level), which we
    # have no way to compute: we only ever see one leg's score at a time, never the tie
    # as a whole. resolve_outcome() already fell through to None for this (unrecognized
    # factor), but it was never worth betting on in the first place.
    2820, 2821,

    # "Кто выиграет фрейм %P" (billiards, "Свободная пирамида" etc) — same class as
    # tennis's "Гейм %P": who's ahead once a specific frame number starts, needing a
    # frame-by-frame log we don't have (only score_1/score_2 = frames won overall).
    1612, 1613, 1756, 1757, 1759, 1760, 3155, 3156,
}

# Market *scopes* (not factor_ids — these reuse the same 1x2/total/handicap factor_ids
# as everywhere else, just under a market_prefix that isn't a part-of-match period) we
# never parse: they need a score dimension we don't track at all — corner count, card
# count, foul count, shots, offsides, etc. never get their own tally anywhere in our
# data (only the match's actual goal/point score and, since this session, each period's
# own score). Unlike periods, there's no fallback data source that could ever grade
# these, so — same reasoning as EXCLUDED_FACTOR_IDS above — better excluded outright than
# left permanently void. Found while auditing "не рассчитана" history: 549 "N-я карта"
# bets had actually been silently mis-graded against period_scores before the ordinal
# parser was tightened (see _PERIOD_ORDINAL_RE in database.py) to stop treating "1-я
# карта" as if it meant period 1.
EXCLUDED_MARKET_PREFIXES = {
    "угловые", "фолы", "желтые карты", "вброс аутов", "удары в створ",
    "удары от ворот", "офсайды", "1-я карта", "2-я карта", "3-я карта",
    "штанги или перекладины", "оказание помощи мед. бригады на поле",
    "кол-во 2-мин удалений",

    # Same reasoning, different shape: these don't have a period_scores-style score at
    # all (no ordinal prefix, so the period path was never going to touch them) — but
    # they still shouldn't be bet on, since nothing will ever grade them. "Голы в
    # большинстве" (power-play goals) and "серия буллитов"/"серия пенальти" (shootout)
    # need a score we don't track separately from the main scoreline; "овертайм" needs
    # its own period entry Fonbet doesn't appear to expose to us; "эйсы"/"двойные
    # ошибки" are tennis point-type stats, not part of the games/sets score at all.
    "голы в большинстве", "овертайм", "серия пенальти", "серия буллитов",
    "эйсы", "двойные ошибки",

    # "Попытки" (rugby tries) — reuses the same universal total/handicap/itotal
    # factor_ids as the main points market (930/931, 974, ...), just under this scope,
    # so it can't be told apart by factor_id. We only track match points (score_1/
    # score_2), never a separate try count, and points don't map 1:1 to tries (a try is
    # 5, a conversion 2, a penalty 3, a drop goal 3 — ambiguous to reverse-engineer).
    "попытки",

    # "заб/3-х очковые" (basketball made-3-pointers submarket) — same shape as "попытки"
    # above: reuses the universal Total factor_ids (930/931) under this scope, but we
    # only track match points (score_1/score_2), never a separate made-3-pointers count,
    # and points don't map 1:1 to made threes (a 3-pointer is 3 points, same as some
    # other patterns aren't, and free throws/2-pointers add points too) — confirmed via
    # a real stuck bet ("Капиата Буллз — Амамбай", score 35:85, Тотал Больше/Меньше 9.5
    # sitting permanently "не рассчитана" with no way to derive the made-3s count from
    # the final score alone).
    "заб/3-х очковые",

    # "Дополнительное время" (football extra time) — needs the score for JUST the ET
    # period, not the cumulative match score we actually have; ET isn't a numbered
    # ordinal period ("1-й тайм" etc) so period_scores never covers it either. "Видео-
    # просмотры" (VAR review count) is a stat we don't track at all, same as corners/
    # cards/fouls above.
    "дополнительное время", "видеопросмотры",
}

# Whole events we never parse at all — not shown live, not bet on, not archived, not
# trained on. Confirmed via history audit: matches under these formats overwhelmingly
# finish (or transition between periods) faster than our 60s poll interval, so
# period_scores stays permanently empty — 35000+ "не рассчитана" bets traced back to
# exactly this. Per explicit instruction, these are excluded outright rather than just
# disabling their period-scoped markets.
# "Шорт-хоккей" was here too originally, on the assumption "short" meant a compressed
# simulated format like the others — turned out wrong: watching a real "Шорт-хоккей"
# match live (RHL. West. 3x10 — a real shootout, see the resolve_outcome named_scores
# work) showed a normal ~30-minute game with 3 real periods, OT and a shootout. "Short"
# just means shorter periods (an amateur/streetball-style league format), not a
# compressed simulation — same "occasional gap, not a structural failure" shape as
# IPBL/NBA2K/CS-H2H below, which were checked and also NOT excluded. Removed.
_FAST_FORMAT_SPORT_PATH_SUBSTRINGS = (
    "setka cup", "world tennis", "tt cup", "online live league",
)
# Esports simulated fixtures (FC 24/26, NHL 26, NBA 2K, H2H CS, ...) advertise their own
# compressed real-time duration right in the league name — "2x4 мин.", "3x4 мин.",
# "4X5 мин", etc. — a reliable, sport-agnostic tell that the whole match plays out in a
# couple of real minutes, same root problem as the named leagues above.
_FAST_ESPORTS_FORMAT_RE = re.compile(r"\d+\s*[xх]\s*\d+\s*мин", re.IGNORECASE)


def _is_fast_format_sport_path(sport_path: str) -> bool:
    sp = (sport_path or "").lower()
    if any(s in sp for s in _FAST_FORMAT_SPORT_PATH_SUBSTRINGS):
        return True
    return bool(_FAST_ESPORTS_FORMAT_RE.search(sp))


# Fonbet's live list has a display-side lag for combat sports: a match can drop off
# the live feed for a minute or more (well past EVENT_MISS_THRESHOLD/GRACE) while still
# actually in progress, then reappear under the same event_id. That gap makes the
# generic miss/finalize logic in database.py archive the event on a stale mid-fight
# score, permanently settling any bets on it against the wrong outcome with no way to
# reconcile later. Rather than special-case the miss/settlement logic per sport, exclude
# these events outright at parse time — not shown live, not bet on, not archived, not
# trained on — same treatment as the fast-format exclusion above.
_EXCLUDED_SPORT_PATH_SUBSTRINGS = (
    "единоборства", "mma", "ufc", "бокс",
)


def _is_excluded_sport_path(sport_path: str) -> bool:
    sp = (sport_path or "").lower()
    return any(s in sp for s in _EXCLUDED_SPORT_PATH_SUBSTRINGS)

# Groups mutually-exclusive outcomes of the same market (e.g. Total Over/Under on the
# same line, or the two sides of a handicap) so the neurobets ranking can show only the
# single best pick per market instead of betting on both sides at once. Factor ids not
# listed here (unknown/catalog-resolved markets) are treated as their own standalone group.
OUTCOME_FAMILY_MAP: Dict[int, str] = {
    921: "1x2", 922: "1x2", 923: "1x2", 924: "1x2", 925: "1x2", 926: "1x2", 1571: "1x2",

    # "Итоговая победа" (hockey/floorball match winner incl. OT/shootout) — see
    # FACTORS_FINAL_WIN1/2 in database.py.
    7035: "final_win", 933: "final_win", 7348: "final_win", 16617: "final_win",
    16680: "final_win", 17036: "final_win",
    7036: "final_win", 934: "final_win", 7349: "final_win", 16618: "final_win",
    16681: "final_win", 17037: "final_win",

    # Cross-referenced against the catalog's "Основные ставки | Фора" table the same way
    # as "total" below — this only had 6 of the ~21 real pairs before.
    927: "handicap", 928: "handicap", 910: "handicap", 912: "handicap",
    989: "handicap", 991: "handicap", 1569: "handicap", 1572: "handicap",
    1677: "handicap", 1678: "handicap", 1680: "handicap", 1681: "handicap",
    937: "handicap", 938: "handicap", 1672: "handicap", 1675: "handicap",
    1683: "handicap", 1684: "handicap", 1686: "handicap", 1687: "handicap",
    1689: "handicap", 1690: "handicap", 1692: "handicap", 1718: "handicap",
    1723: "handicap", 1724: "handicap", 1845: "handicap", 1846: "handicap",
    16211: "handicap", 16212: "handicap", 16214: "handicap", 16215: "handicap",
    16217: "handicap", 16218: "handicap", 16220: "handicap", 16221: "handicap",
    16223: "handicap", 16224: "handicap", 16226: "handicap", 16227: "handicap",
    16229: "handicap", 16230: "handicap", 16232: "handicap", 16233: "handicap",

    # Verified against Fonbet's own factorsCatalog "tables" (the "Основные ставки |
    # Тотал" table, rows paired as [param, Б-value, М-value]) — 1739 pairs with 1791
    # (not 1740) and 1802 pairs with 1803 (not 1804); 1740/1804 don't exist in the
    # current catalog and never appeared in any real parsed data, so they were almost
    # certainly a guess that was never checked. 940/941, 1799/1800, 1805/1806 were
    # missing from this map entirely despite being real catalog-confirmed pairs.
    930: "total", 931: "total", 940: "total", 941: "total",
    1696: "total", 1697: "total", 1727: "total", 1728: "total",
    1730: "total", 1731: "total", 1733: "total", 1734: "total",
    1736: "total", 1737: "total", 1739: "total", 1791: "total",
    1793: "total", 1794: "total", 1796: "total", 1797: "total",
    1799: "total", 1800: "total", 1802: "total", 1803: "total",
    1805: "total", 1806: "total", 1848: "total", 1849: "total",

    1809: "itotal1", 1810: "itotal1", 1812: "itotal1", 1813: "itotal1",
    1815: "itotal1", 1816: "itotal1", 1818: "itotal1", 1819: "itotal1",
    1821: "itotal1", 1822: "itotal1",
    974: "itotal1", 976: "itotal1", 1824: "itotal1", 1825: "itotal1",
    1827: "itotal1", 1828: "itotal1", 1830: "itotal1", 1831: "itotal1",
    2203: "itotal1", 2204: "itotal1",

    1854: "itotal2", 1855: "itotal2", 1857: "itotal2", 1858: "itotal2",
    1860: "itotal2", 1861: "itotal2",
    1871: "itotal2", 1873: "itotal2", 1874: "itotal2", 1880: "itotal2", 1881: "itotal2",
    978: "itotal2", 980: "itotal2", 1883: "itotal2", 1884: "itotal2",
    1886: "itotal2", 1887: "itotal2", 1893: "itotal2", 1894: "itotal2",
    1896: "itotal2", 1897: "itotal2", 1899: "itotal2", 1900: "itotal2",
    2209: "itotal2", 2210: "itotal2",

    4241: "btts", 4242: "btts",

    2820: "proход", 2821: "proход",

    # "Кол-во сетов" (total sets played) and "Фора по сетам" (sets handicap) — see
    # FACTORS_SETS_* in database.py.
    917: "sets_total", 918: "sets_total", 10409: "sets_total", 10410: "sets_total",
    2421: "sets_handicap", 2422: "sets_handicap",
    2424: "sets_handicap", 2425: "sets_handicap",
    2427: "sets_handicap", 2428: "sets_handicap",
    2430: "sets_handicap", 2431: "sets_handicap",
    2433: "sets_handicap", 2434: "sets_handicap",
    2436: "sets_handicap", 2437: "sets_handicap",
    3262: "sets_handicap", 3263: "sets_handicap",
    3265: "sets_handicap", 3266: "sets_handicap",
    3244: "sets_handicap", 3245: "sets_handicap",
    3247: "sets_handicap", 3248: "sets_handicap",
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
        # factors_map[fid] is this exact factor's own name (e.g. "Индивидуальный тотал 1
        # больше 0.5") — the actual selection. factors_group_map[fid] is just the menu
        # breadcrumb leading to it (e.g. "Основные ставки | Исходы"), shared by every
        # factor under that category, so it's a worse fallback and must be tried last.
        title = CORE_FACTOR_MAP.get(factor_id) or self.factors_map.get(factor_id) or self.factors_group_map.get(factor_id) or f"Исход {factor_id}"
        
        if param_str is not None and str(param_str).strip() != "":
            p_clean = str(param_str).strip()
            if "(" in title and ")" in title:
                return title
            return f"{title} ({p_clean})"
        return title

    def _extract_live_score_and_timer(
        self, eid: int, ev: Dict[str, Any], live_infos_map: Dict[int, Any], event_miscs_map: Dict[int, Any]
    ) -> Tuple[int, int, str, str, List[Tuple[int, int]], Dict[str, Tuple[int, int]]]:
        misc = event_miscs_map.get(eid, {})
        live = live_infos_map.get(eid, {})

        s1 = misc.get("score1")
        s2 = misc.get("score2")

        scores_arr = live.get("scores")

        if s1 is None or s2 is None:
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

        # scores[1] (when present) is each period's *own* score, not a running total —
        # confirmed against a real match where the final score and the 1st-half entry
        # were both 1:0 while the 2nd-half entry was 0:0 (all scoring happened in the
        # first half). This is what lets period-scoped bets ("1-й тайм", "2-й период",
        # ...) be graded at all — resolve_outcome() in database.py indexes into this by
        # the ordinal number parsed out of the bet's market_prefix.
        period_scores: List[Tuple[int, int]] = []
        if scores_arr and isinstance(scores_arr, list) and len(scores_arr) > 1:
            periods_raw = scores_arr[1]
            if isinstance(periods_raw, list):
                for p in periods_raw:
                    if not isinstance(p, dict):
                        continue
                    try:
                        period_scores.append((int(p.get("c1", 0)), int(p.get("c2", 0))))
                    except Exception:
                        period_scores.append((0, 0))

        # liveEventInfos.subscores is a separate, richer breakdown from scores[1] above —
        # named by kindName ("1-й период", "эйсы", "3-й сет двойные ошибки", ...) rather
        # than positional. Captured here (not yet consumed anywhere) specifically to
        # investigate whether it exposes a named entry for shootout/overtime ("Серия
        # буллитов" / "Овертайм") when a tied match reaches that phase — if it does, that
        # would finally let "Итоговая победа" resolve on a tied regulation score instead
        # of staying permanently unresolvable. Needs confirming against a real match
        # that's actually in that phase before any grading logic is built on it, to
        # avoid guessing at a data shape and getting it backwards (see the FACTORS_SETS_*
        # comment in database.py for what happened last time a shape was assumed instead
        # of verified).
        named_scores: Dict[str, Tuple[int, int]] = {}
        subscores_raw = live.get("subscores")
        if isinstance(subscores_raw, list):
            for s in subscores_raw:
                if not isinstance(s, dict):
                    continue
                name = s.get("kindName")
                if not name:
                    continue
                try:
                    named_scores[name] = (int(s.get("c1", 0)), int(s.get("c2", 0)))
                except Exception:
                    pass

        return s1_int, s2_int, score_str, timer_str, period_scores, named_scores

    def _parse_factors_for_event(self, eid: int, market_prefix: str, custom_factors_map: Dict[int, Any]) -> List[Dict[str, Any]]:
        if (market_prefix or "").strip().lower() in EXCLUDED_MARKET_PREFIXES:
            return []
        raw_factors = custom_factors_map.get(eid, [])
        parsed = []
        for f in raw_factors:
            fid = f.get("f")
            val = f.get("v")
            pt = f.get("pt")
            p_raw = f.get("p")
            
            if fid is None or val is None:
                continue
            if fid in EXCLUDED_FACTOR_IDS:
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

    def parse_live(self) -> Tuple[List[Dict[str, Any]], set]:
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
        seen_live_ids: set = set()

        for eid, ev in events_map.items():
            ev_place = ev.get("place", "live")
            if ev_place != "live":
                continue

            kind = ev.get("kind", 1)
            if kind != 1 and ev.get("parentId"):
                # Sub-events handled under parent
                continue

            # Event is genuinely present in this snapshot even if it ends up with
            # zero odds below (e.g. a quarter-break line pull) — record it as "seen"
            # so the caller doesn't treat a temporary odds gap as the match ending.
            seen_live_ids.add(eid)

            sport_path = get_sport_path(ev.get("sportId"))
            if _is_fast_format_sport_path(sport_path):
                # These formats reliably finish faster than our 60s poll interval —
                # period_scores (and sometimes even a single real score snapshot) never
                # gets captured, so period-scoped bets on them are permanently
                # unresolvable. Per explicit instruction: skip the whole event, not just
                # the period-scoped markets — not shown live, not bet on, not archived,
                # not trained on.
                continue
            if _is_excluded_sport_path(sport_path):
                # Combat sports (see _EXCLUDED_SPORT_PATH_SUBSTRINGS above): Fonbet's
                # live-list display lag causes premature/incorrect settlement.
                continue

            t1 = ev.get("team1", "").strip()
            t2 = ev.get("team2", "").strip()
            if not t2:
                # Tournament-outright entries ("Итоги турнира" / "Результат турнира" —
                # betting on one named player/team to win the whole tournament, not a
                # single match) show up in the live feed with an empty team2 and score
                # stuck at 0:0 for the entire event — there's no head-to-head match here
                # to derive a scoreline from at all. Confirmed via history: these always
                # sit permanently "не рассчитана" (factor 7035/7036 and friends, tied
                # 0:0 forever), so skip them outright.
                continue
            match_name = f"{t1} — {t2}"

            s1_int, s2_int, score_str, timer_str, period_scores, named_scores = self._extract_live_score_and_timer(
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
                "period_scores": period_scores,
                "named_scores": named_scores,
                "is_live": True,
                "sub_markets": sub_markets_summary,
                "total_odds_count": len(all_match_odds),
                "odds": all_match_odds
            })

        logger.info(f"Parsed {len(parsed_events)} live matches from Fonbet ({len(seen_live_ids)} live events in snapshot).")
        return parsed_events, seen_live_ids
