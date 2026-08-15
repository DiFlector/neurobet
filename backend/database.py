import json
import math
import os
import re
import logging
from typing import List, Dict, Any, Optional, Iterable, Tuple
from datetime import datetime, timezone

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from settings import settings
from parser_service import OUTCOME_FAMILY_MAP

logger = logging.getLogger("database")

_pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, dsn=settings.DATABASE_URL)

def get_connection():
    conn = _pg_pool.getconn()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO live, public")
    return conn

def get_finished_connection():
    conn = _pg_pool.getconn()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO finished, public")
    return conn

def release_connection(conn):
    _pg_pool.putconn(conn)

def init_db():
    # Schema is owned by Alembic migrations (db/migrations/versions/) now — this is a
    # no-op kept so existing callers (backend/main.py) don't need changing.
    logger.info("Database schema is managed by Alembic migrations; skipping init_db DDL.")

MAIN_MARKET_PREFIX = "Основной матч"

# Виды спорта (по префиксу sport_path, в нижнем регистре), для которых score_1/score_2
# в нашей БД реально означают очки/голы, а тотал по ним считается корректно.
# Теннис/волейбол/настольный теннис/крикет и т.п. хранят счёт по сетам/партиям —
# тотал по такому "счёту" считать нельзя. Гандбол и водное поло добавлены — там счёт
# точно так же аддитивные голы, как в футболе/хоккее (подтверждено на реальных матчах:
# счета вида 8:9, 15:5 — явно накопленные голы за игру, не партии/сеты). Регби
# добавлено по той же логике — счета вида 16:20, 28:10 явно накопленные очки за матч
# (попытки/конверсии/пенальти), не партии. Бейсбол — то же самое, счета вида 0:1 явно
# накопленные раны за игру. Флорбол — тоже голы, накопленные по 3 периодам (подтверждено:
# period_scores [[1,2],[0,1],[1,1]] суммируются в финальный счёт 2:4).
RESOLVABLE_TOTAL_SPORTS = ("футбол", "хоккей", "баскетбол", "гандбол", "водное поло", "регби", "бейсбол", "флорбол")

# Спорт хранит score_1/score_2 как выигранные сеты/партии (не очки), НО period_scores[i]
# для этих же видов спорта — это подлинный очковый счёт именно этого сета/партии, а не
# счёт партий. Подтверждено на реальных матчах: теннис [[5,7],[6,4],[5,2]] (геймы за
# сет), волейбол [[16,25],[21,25],[19,23]] (очки за партию, до 25), настольный теннис
# [[11,5],[9,11],[11,6]] (очки за партию, до 11) — во всех случаях числа явно не "0 или
# 1 сет", а реальный счёт розыгрыша. Это значит: period-скоупные тотал/фора/инд.тотал
# ставки на такой спорт ("1-й сет — Фора ...") можно грейдить точно так же, как и для
# RESOLVABLE_TOTAL_SPORTS, просто беря period_scores[i] вместо score_1/score_2. А
# ставки на "Основной матч" можно грейдить, просуммировав все элементы period_scores
# (общий счёт по очкам за весь матч) — см. _effective_points_score().
# Counter-Strike (Bo3/Bo5) fits the exact same shape: score_1/score_2 is maps won, and
# period_scores[i] is that map's own round score — confirmed live: [[13,7],[12,11]]
# (round scores per map). Other esports titles aren't included here since they don't
# necessarily share this maps-vs-rounds split (e.g. league/FIFA-style esports already
# reports its own additive score directly as score_1/score_2, no periods needed).
# Snooker fits the same shape too: score_1/score_2 is frames won, period_scores[i] is
# that frame's own point break — confirmed live: [[8, 62]] (a real frame score; snooker
# frames run up to ~140).
PERIODIC_POINT_SPORTS = ("теннис", "настольный теннис", "волейбол", "бадминтон", "counter-strike", "снукер")

# Точные наборы factor_id — источник: CORE_FACTOR_MAP (parser_service.py). 925/1571
# were swapped for a long time (see the comment on CORE_FACTOR_MAP): Fonbet's own
# catalog maps factor 925 to "X2" and 1571 to "12", not the reverse — so it's 925 in
# FACTORS_X2 and 1571 in FACTORS_12 here, matching the corrected label map.
FACTORS_W1 = {921}
FACTORS_X = {922}
FACTORS_W2 = {923}
FACTORS_1X = {924}
FACTORS_12 = {1571}
FACTORS_X2 = {925, 926}
# "Итоговая победа" (hockey/floorball) — who wins the *match*, not just regulation time:
# if the score is level after regulation these sports go to overtime/a shootout to
# decide it. When score_1 != score_2 this is identical to W1/W2 (whoever's ahead won
# outright, OT never being needed). When score_1 == score_2 the match was still decided
# by a shootout/OT goal that (per real data) does NOT get reflected back into score_1/
# score_2 — we have no shootout-result field at all, so that specific case stays
# unresolvable (same "we're honestly missing the data" reasoning as everywhere else in
# this function, not a full exclusion — most matches don't need a shootout). Catalog
# confirms 7035/933/7348/16617/16680/17036 are all the same "team 1" market (Fonbet
# just encodes it differently per league/tournament type) and the mirror set for team 2.
FACTORS_FINAL_WIN1 = {7035, 933, 7348, 16617, 16680, 17036}
FACTORS_FINAL_WIN2 = {7036, 934, 7349, 16618, 16681, 17037}
# Was {930}/{931} only for a long time — every other total line (alternate totals
# offered alongside the main one, shared across sports) was silently unresolvable even
# for football/hockey/basketball/handball/water polo, where it's fully gradable. Pairing
# verified against Fonbet's factorsCatalog "tables" ("Основные ставки | Тотал": each row
# is [param, Б-factor, М-factor]) — see the matching comment in parser_service.py's
# OUTCOME_FAMILY_MAP, which had two wrong entries (1740, 1804) fixed alongside this.
# 1848/1849 was originally excluded outright (see EXCLUDED_FACTOR_IDS history in
# parser_service.py) on the assumption a points-total line was unresolvable for sports
# scored in sets — true for score_1/score_2, but PERIODIC_POINT_SPORTS above means we
# actually can grade it via the period_scores sum, so it's back here instead of excluded.
FACTORS_TOTAL_OVER = {930, 940, 1696, 1727, 1730, 1733, 1736, 1739, 1793, 1796, 1799, 1802, 1805, 1848}
FACTORS_TOTAL_UNDER = {931, 941, 1697, 1728, 1731, 1734, 1737, 1791, 1794, 1797, 1800, 1803, 1806, 1849}
# Individual totals — score_1/score_2 already gives us each side's own goal/point count
# directly, so these are just as gradable as the combined total; they were previously
# always left unresolved (voided) even though we had everything needed to grade them.
# 1854/1857/1860/1873/1880 and their under-side mirror come from CORE_FACTOR_MAP's
# per-factor "Больше"/"Меньше" labels (parser_service.py), verified earlier against a
# real water-polo match — kept as-is rather than trusting the "tables" catalog's row
# pairing for this specific table, which pairs 1854 with 1871 and doesn't seem to
# describe the same grouping (no 1857/1858/1860/1861 row exists there at all). The new
# additions below (974/976, 978/980, 1824-1831, 2203/2204, 1883-1900, 2209/2210) are
# catalog-only IDs with no prior CORE_FACTOR_MAP entry to conflict with, so those are
# trusted straight from the catalog's Б/М table pairing.
FACTORS_ITOTAL1_OVER = {1809, 1812, 1815, 1818, 1821, 974, 1824, 1827, 1830, 2203}
FACTORS_ITOTAL1_UNDER = {1810, 1813, 1816, 1819, 1822, 976, 1825, 1828, 1831, 2204}
FACTORS_ITOTAL2_OVER = {1854, 1857, 1860, 1873, 1880, 978, 1883, 1886, 1893, 1896, 1899, 2209}
FACTORS_ITOTAL2_UNDER = {1855, 1858, 1861, 1871, 1874, 1881, 980, 1884, 1887, 1894, 1897, 1900, 2210}
# "Кол-во сетов" (total number of sets played) and "Фора по сетам" (handicap on sets
# won) — unlike every other total/fora above, these are denominated in SETS, not points,
# even for PERIODIC_POINT_SPORTS: they need the real score_1/score_2 tally (sets won),
# not the period_scores point-sum substitution. Graded via their own branch below using
# the raw score_1/score_2 parameters directly. Verified against real tennis data: 2421
# tracks team 1's line, 2422 team 2's (mirror sign, same match, same param magnitude).
FACTORS_SETS_TOTAL_OVER = {917, 10409}
FACTORS_SETS_TOTAL_UNDER = {918, 10410}
# Catalog's "Фора по сетам" table header is [Фора, "1", Фора, "2"] — column 2's factorId
# is the Ф1 (team 1) value, column 4's is Ф2. Confirmed against a real decisive match
# (team 1 won 1:0 sets): with this assignment, 2422 (Ф1, -1.5) loses and 2421 (Ф2, +1.5)
# wins — the winning favorite laying -1.5 and not covering, the losing side's +1.5
# covering — exactly the shape a real handicap outcome should have. The reverse
# assignment (first tried) produced the losing side laying -1.5 as the "favorite", which
# doesn't happen in practice — that's what gave this away as swapped.
FACTORS_SETS_FORA1 = {2422, 2424, 2427, 2430, 2433, 2436, 3262, 3265, 3244, 3247}
FACTORS_SETS_FORA2 = {2421, 2425, 2428, 2431, 2434, 2437, 3263, 3266, 3245, 3248}
# "Фора по картам" (esports map handicap, same [Фора,"1",Фора,"2"] catalog header) —
# same convention, re-confirmed independently against real Counter-Strike results (e.g.
# team 1 lost 0:1 maps, 3262 "+1.5" as Ф1 won, 3263 "-1.5" as Ф2 lost — the losing side's
# +1.5 covering, the winning side's -1.5 not, same realistic shape as the sets case).
# Handicaps — score_1 + param vs score_2 (or the mirror for side 2) is standard Asian-
# handicap grading; same push/line-not-crossed rule as totals. Was always left
# unresolved even though it's gradable the same way totals are — 4156 bets sitting
# permanently void when this was added.
# Like FACTORS_TOTAL_OVER/UNDER, this only ever had a handful of the real pairs — cross-
# referenced against Fonbet's factorsCatalog "tables" (the "Основные ставки | Фора"
# table, rows shaped [param, Ф1-factor, param, Ф2-factor]) to fill in the rest, including
# 1845/1846 (the "не рассчитана" table-tennis Fora from history) and alt-line pairs
# (937/938, 1672/1675, 1683-1690, 1692/1718, 1723/1724, 16211-16233) never mapped before.
FACTORS_FORA1 = {
    927, 910, 989, 1569, 1677, 1680, 937, 1672, 1683, 1686, 1689, 1692, 1723, 1845,
    16211, 16214, 16217, 16220, 16223, 16226, 16229, 16232,
}
FACTORS_FORA2 = {
    928, 912, 991, 1572, 1678, 1681, 938, 1675, 1684, 1687, 1690, 1718, 1724, 1846,
    16212, 16215, 16218, 16221, 16224, 16227, 16230, 16233,
}
# "Обе забьют" (BTTS) — both sides having a nonzero score is directly readable from
# score_1/score_2, no different from the totals/individual-totals math above.
FACTORS_BTTS_YES = {4241}
FACTORS_BTTS_NO = {4242}


# Requires the trailing word to actually be a period/part-of-match unit — "1-я карта"
# (which card number) and "2-й период" (which period) both start with "<digit>-<letters>
# <word>", but only the second one is a *part of the match* whose own score
# period_scores[N-1] can stand in for. Confirmed this was a real bug: without the
# trailing-word check, "1-я карта"/"2-я карта"/"3-я карта" (yellow-card-number bets,
# nothing to do with periods) parsed as ordinals 1/2/3 and got graded against
# period_scores — 549 card bets had a bogus is_win from this before it was caught.
_PERIOD_ORDINAL_RE = re.compile(
    r"^(\d+)-[а-яё]+\s+(сет|тайм|период|четверть|половина)\b", re.IGNORECASE
)


def _parse_period_ordinal(prefix: str) -> Optional[int]:
    """"1-й тайм" -> 1, "2-й период" -> 2, "3-я четверть" -> 3. Prefixes without a
    leading ordinal, or whose trailing word isn't a recognized period unit ("Овертайм",
    "Следующий гол", "1-я карта", ...), return None — deliberately unresolvable."""
    m = _PERIOD_ORDINAL_RE.match(prefix)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def resolve_outcome(
    factor_id: int,
    label: str,
    param_str: str,
    score_1: int,
    score_2: int,
    market_prefix: str = "",
    sport_path: str = "",
    period_scores: Optional[List[Tuple[int, int]]] = None,
    named_scores: Optional[Dict[str, Tuple[int, int]]] = None,
) -> Tuple[Optional[int], bool]:
    """
    Возвращает (outcome, is_push).

    outcome: 1 (ставка сыграла), 0 (не сыграла) или None (честно рассчитать нельзя —
    либо из-за нехватки данных, либо потому что это возврат).

    is_push: True, когда outcome is None конкретно потому, что линия (тотал/фора/инд.
    тотал) легла ровно на параметр — законный возврат ставки, а не "не рассчитано".
    UI должен показывать это отдельным состоянием, не путая с реально нерасчитанными
    ставками, для которых is_push всегда False.

    outcome is None (is_push=False) возвращается, когда у нас физически нет данных для
    корректного расчёта:
      * маркет — часть матча (тайм/период/четверть/сет/...) без счёта именно за эту
        часть (period_scores не передан, слишком короткий, или префикс не начинается
        с порядкового числительного — "Овертайм", "Следующий гол" и спец-маркеты вроде
        угловых/фолов/карточек: счёта по ним система не собирает вообще);
      * "проход" (переход по серии/сумме матчей — не выводится из счёта одного матча)
        и любые нераспознанные факторы;
      * тотал/индивидуальный тотал/фора/"обе забьют" в виде спорта, где счёт хранится
        не в очках/голах (теннис, волейбол и т.п.);
      * "Итоговая победа" при равном счёте (решено буллитами/овертаймом, которых у нас
        нет) — это НЕ возврат, реальный исход есть, просто мы его не знаем;
      * любая ошибка разбора данных.
    """
    prefix = (market_prefix or "").strip()
    sport = (sport_path or "").lower()
    is_periodic_sport = any(s in sport for s in PERIODIC_POINT_SPORTS)

    if (
        factor_id in FACTORS_SETS_TOTAL_OVER or factor_id in FACTORS_SETS_TOTAL_UNDER
        or factor_id in FACTORS_SETS_FORA1 or factor_id in FACTORS_SETS_FORA2
    ):
        # Checked before eff_score_1/eff_score_2 even get computed below — these markets
        # are sets-denominated and use the raw score_1/score_2 args directly, never
        # needing (or wanting) the period_scores point-sum substitution that "elif
        # is_periodic_sport" further down requires and would otherwise short-circuit on
        # (returning None whenever period_scores is empty, which has nothing to do with
        # whether a sets market can be graded).
        try:
            raw_s1 = int(score_1)
            raw_s2 = int(score_2)
        except Exception:
            return None, False
        if not param_str:
            return None, False
        try:
            param_val = float(str(param_str).replace(",", "."))
        except Exception:
            return None, False
        if factor_id in FACTORS_SETS_TOTAL_OVER or factor_id in FACTORS_SETS_TOTAL_UNDER:
            total_sets = raw_s1 + raw_s2
            if total_sets == param_val:
                return None, True
            return (1 if (total_sets > param_val) == (factor_id in FACTORS_SETS_TOTAL_OVER) else 0), False
        adjusted = (raw_s1 + param_val - raw_s2) if factor_id in FACTORS_SETS_FORA1 else (raw_s2 + param_val - raw_s1)
        if adjusted == 0:
            return None, True
        return (1 if adjusted > 0 else 0), False

    if prefix and prefix != MAIN_MARKET_PREFIX:
        ordinal = _parse_period_ordinal(prefix)
        if ordinal is None or not period_scores or ordinal > len(period_scores) or ordinal < 1:
            return None, False
        eff_score_1, eff_score_2 = period_scores[ordinal - 1]
        # period_scores holds genuine point data whether the sport is additive overall
        # (it's a slice of the match's goals) or periodic-point (it's the set/period's
        # own point score) — either way it's safe for total/handicap/itotal math below,
        # and for "who won this period" too (a period's own point tally is exactly what
        # decides it).
        scores_are_points = True
        # match_s1/match_s2 (below) intentionally match eff here — "2-й сет — П1" means
        # who won set 2, which the set's own point score answers directly.
        match_s1, match_s2 = eff_score_1, eff_score_2
    elif is_periodic_sport:
        # "Основной матч" scope on a sets-scored sport: score_1/score_2 is sets won, not
        # points, but summing every recorded period's own point score gives the true
        # match-wide point total — see the PERIODIC_POINT_SPORTS comment above. This
        # point-sum is for TOTAL/ITOTAL/FORA/BTTS math ONLY, though — who actually *won
        # the match* (W1/X/W2/12/X2/FINAL_WIN) is always decided by sets/maps won, not
        # by total points across them (a player can lose 1-2 in sets while still winning
        # more total games, so match_s1/match_s2 below stays the raw score_1/score_2).
        if not period_scores:
            return None, False
        eff_score_1 = sum(p[0] for p in period_scores)
        eff_score_2 = sum(p[1] for p in period_scores)
        scores_are_points = True
        match_s1, match_s2 = score_1, score_2
    else:
        eff_score_1, eff_score_2 = score_1, score_2
        scores_are_points = any(s in sport for s in RESOLVABLE_TOTAL_SPORTS)
        match_s1, match_s2 = score_1, score_2

    try:
        s1 = int(eff_score_1)
        s2 = int(eff_score_2)
        m1 = int(match_s1)
        m2 = int(match_s2)
    except Exception:
        return None, False

    if factor_id in FACTORS_W1:
        return (1 if m1 > m2 else 0), False
    if factor_id in FACTORS_X:
        return (1 if m1 == m2 else 0), False
    if factor_id in FACTORS_W2:
        return (1 if m2 > m1 else 0), False
    if factor_id in FACTORS_1X:
        return (1 if m1 >= m2 else 0), False
    if factor_id in FACTORS_12:
        return (1 if m1 != m2 else 0), False
    if factor_id in FACTORS_X2:
        return (1 if m2 >= m1 else 0), False

    if factor_id in FACTORS_FINAL_WIN1 or factor_id in FACTORS_FINAL_WIN2:
        if m1 != m2:
            higher_side_1 = m1 > m2
            return (1 if (higher_side_1 if factor_id in FACTORS_FINAL_WIN1 else not higher_side_1) else 0), False
        # Tied after regulation/OT — score_1/score_2 never gets a "bonus" goal for the
        # shootout winner (confirmed by watching a real match's raw feed second-by-second
        # through an entire "серия буллитов" sequence: misc.score1/score2 stayed frozen
        # the whole time). But liveEventInfos.subscores carries a *separate* named entry
        # for it (kindName "серия буллитов", e.g. c1/c2 going 0:0 → 1:0 → ... → 3:2) —
        # captured into named_scores — whose own higher side is the actual match winner.
        shootout = (named_scores or {}).get("серия буллитов")
        if shootout is None or shootout[0] == shootout[1]:
            return None, False  # still in progress, or truly no shootout data captured
        so_higher_1 = shootout[0] > shootout[1]
        return (1 if (so_higher_1 if factor_id in FACTORS_FINAL_WIN1 else not so_higher_1) else 0), False

    if factor_id in FACTORS_TOTAL_OVER or factor_id in FACTORS_TOTAL_UNDER:
        if not scores_are_points:
            return None, False
        if not param_str:
            return None, False
        try:
            param_val = float(str(param_str).replace(",", "."))
        except Exception:
            return None, False
        total = s1 + s2
        if total == param_val:
            return None, True  # возврат (push) — не выигрыш и не проигрыш
        if factor_id in FACTORS_TOTAL_OVER:
            return (1 if total > param_val else 0), False
        return (1 if total < param_val else 0), False

    if (
        factor_id in FACTORS_ITOTAL1_OVER or factor_id in FACTORS_ITOTAL1_UNDER
        or factor_id in FACTORS_ITOTAL2_OVER or factor_id in FACTORS_ITOTAL2_UNDER
    ):
        if not scores_are_points:
            return None, False
        if not param_str:
            return None, False
        try:
            param_val = float(str(param_str).replace(",", "."))
        except Exception:
            return None, False
        side_total = s1 if factor_id in FACTORS_ITOTAL1_OVER or factor_id in FACTORS_ITOTAL1_UNDER else s2
        if side_total == param_val:
            return None, True  # возврат (push)
        if factor_id in FACTORS_ITOTAL1_OVER or factor_id in FACTORS_ITOTAL2_OVER:
            return (1 if side_total > param_val else 0), False
        return (1 if side_total < param_val else 0), False

    if factor_id in FACTORS_FORA1 or factor_id in FACTORS_FORA2:
        if not scores_are_points:
            return None, False
        if not param_str:
            return None, False
        try:
            param_val = float(str(param_str).replace(",", "."))
        except Exception:
            return None, False
        if factor_id in FACTORS_FORA1:
            adjusted = s1 + param_val - s2
        else:
            adjusted = s2 + param_val - s1
        if adjusted == 0:
            return None, True  # возврат (push) — гандикап ровно закрыл разницу
        return (1 if adjusted > 0 else 0), False

    if factor_id in FACTORS_BTTS_YES or factor_id in FACTORS_BTTS_NO:
        # Deliberately stays additive-sport-only (not scores_are_points): "both sides
        # scored" isn't a meaningful question against a summed sets-score point total —
        # BTTS factors don't appear for sets-scored sports on Fonbet anyway.
        if not any(s in sport for s in RESOLVABLE_TOTAL_SPORTS):
            return None, False
        both_scored = s1 > 0 and s2 > 0
        if factor_id in FACTORS_BTTS_YES:
            return (1 if both_scored else 0), False
        return (0 if both_scored else 1), False

    # "Проход" (advancement over a multi-match series/aggregate) и всё незнакомое —
    # не резолвится: исход не выводится из счёта одного этого матча.
    return None, False

def archive_finished_events(cursor, timestamp_str: str):
    cursor.execute("SELECT * FROM events WHERE is_live = 0")
    finished = cursor.fetchall()

    if not finished:
        return

    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    for ev in finished:
        eid = ev["event_id"]
        s1 = ev["score_1"] or 0
        s2 = ev["score_2"] or 0
        sport_path = ev["sport_path"] or ""
        period_scores_json = ev["period_scores_json"] if "period_scores_json" in ev.keys() else None
        try:
            period_scores = [tuple(p) for p in json.loads(period_scores_json or "[]")]
        except Exception:
            period_scores = []
        named_scores_json = ev["named_scores_json"] if "named_scores_json" in ev.keys() else None
        try:
            named_scores = {k: tuple(v) for k, v in json.loads(named_scores_json or "{}").items()}
        except Exception:
            named_scores = {}

        f_cursor.execute("""
            INSERT INTO finished_events (
                event_id, sport_id, sport_path, match_name, team_1, team_2,
                score_1, score_2, score, finished_at, archived_count, period_scores_json,
                named_scores_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
            ON CONFLICT(event_id) DO UPDATE SET
                score_1 = excluded.score_1,
                score_2 = excluded.score_2,
                score = excluded.score,
                finished_at = excluded.finished_at,
                archived_count = finished_events.archived_count + 1,
                period_scores_json = excluded.period_scores_json,
                named_scores_json = excluded.named_scores_json;
        """, (
            eid, ev["sport_id"], sport_path, ev["match_name"],
            ev["team_1"], ev["team_2"], s1, s2, ev["score"], timestamp_str, period_scores_json or "[]",
            named_scores_json or "{}"
        ))

        # Group the full snapshot-by-snapshot odds_history into one row per unique bet
        # (event_id, factor_id, parameter, market_prefix), with honest initial/final coefficients.
        cursor.execute("""
            SELECT factor_id,
                   COALESCE(market_prefix, '') AS market_prefix,
                   COALESCE(parameter, '') AS parameter,
                   MIN(label) AS label,
                   COUNT(*) AS samples_count,
                   MIN(coefficient) AS min_coeff, MAX(coefficient) AS max_coeff,
                   MIN(id) AS first_id, MAX(id) AS last_id
              FROM odds_history
             WHERE event_id = %s
             GROUP BY factor_id, COALESCE(market_prefix, ''), COALESCE(parameter, '')
        """, (eid,))
        groups = cursor.fetchall()

        for g in groups:
            fid = g["factor_id"]
            prefix = g["market_prefix"] or ""
            param = g["parameter"] or ""

            cursor.execute(
                "SELECT coefficient, timestamp FROM odds_history WHERE id = %s", (g["first_id"],)
            )
            first_row = cursor.fetchone()
            cursor.execute(
                "SELECT coefficient, score_at_time FROM odds_history WHERE id = %s", (g["last_id"],)
            )
            last_row = cursor.fetchone()
            cursor.execute(
                """SELECT coefficient, score_at_time FROM odds_history
                    WHERE event_id = %s AND factor_id = %s
                      AND COALESCE(parameter, '') = %s AND COALESCE(market_prefix, '') = %s
                    ORDER BY id ASC""",
                (eid, fid, param, prefix),
            )
            seq_rows = cursor.fetchall()
            odds_seq = [r["coefficient"] for r in seq_rows]

            # Score as it actually stood at each snapshot while the bet was live —
            # used for training instead of the final score (see score_seq_json migration
            # note above). "N:M" strings that fail to parse fall back to 0-0 (pre-kickoff).
            score_diff_seq: List[int] = []
            for r in seq_rows:
                raw = r["score_at_time"] or "0:0"
                try:
                    a, b = str(raw).split(":", 1)
                    score_diff_seq.append(int(a) - int(b))
                except Exception:
                    score_diff_seq.append(0)
            score_diff_at_bet = score_diff_seq[0] if score_diff_seq else 0

            is_win, is_push = resolve_outcome(
                fid, g["label"] or "", param, s1, s2,
                market_prefix=prefix, sport_path=sport_path, period_scores=period_scores,
                named_scores=named_scores,
            )

            # Capture what the model actually predicted for this bet before it's gone —
            # this is the only way to later check "when the model said 75%, did it really
            # win ~75% of the time?" (calibration).
            cursor.execute(
                """SELECT win_probability FROM ai_predictions
                    WHERE event_id = %s AND factor_id = %s
                      AND COALESCE(CAST(parameter AS TEXT), '') = %s
                      AND COALESCE(market_prefix, '') = %s""",
                (eid, fid, param, prefix),
            )
            pred_row = cursor.fetchone()
            predicted_win_probability = pred_row["win_probability"] if pred_row else None

            f_cursor.execute("""
                INSERT INTO finished_bets (
                    event_id, factor_id, market_prefix, label, parameter,
                    initial_coefficient, final_coefficient, min_coefficient, max_coefficient,
                    samples_count, odds_seq_json, score_at_time, is_win, first_seen_at, finished_at,
                    predicted_win_probability, score_seq_json, score_diff_at_bet, trained_count, is_push
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s)
                ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                    final_coefficient = excluded.final_coefficient,
                    min_coefficient = LEAST(finished_bets.min_coefficient, excluded.min_coefficient),
                    max_coefficient = GREATEST(finished_bets.max_coefficient, excluded.max_coefficient),
                    samples_count = excluded.samples_count,
                    odds_seq_json = excluded.odds_seq_json,
                    score_at_time = excluded.score_at_time,
                    is_win = excluded.is_win,
                    is_push = excluded.is_push,
                    finished_at = excluded.finished_at,
                    predicted_win_probability = COALESCE(excluded.predicted_win_probability, finished_bets.predicted_win_probability),
                    score_seq_json = excluded.score_seq_json,
                    score_diff_at_bet = excluded.score_diff_at_bet,
                    trained_count = 0;
            """, (
                eid, fid, prefix, g["label"] or "", param,
                first_row["coefficient"], last_row["coefficient"], g["min_coeff"], g["max_coeff"],
                g["samples_count"], json.dumps(odds_seq, ensure_ascii=False), last_row["score_at_time"],
                is_win, first_row["timestamp"], timestamp_str, predicted_win_probability,
                json.dumps(score_diff_seq, ensure_ascii=False), score_diff_at_bet, int(is_push),
            ))

        cursor.execute("DELETE FROM latest_odds WHERE event_id = %s", (eid,))
        cursor.execute("DELETE FROM odds_history WHERE event_id = %s", (eid,))
        cursor.execute("DELETE FROM events WHERE event_id = %s", (eid,))
        cursor.execute("DELETE FROM ai_predictions WHERE event_id = %s", (eid,))

    f_conn.commit()
    release_connection(f_conn)

def save_parsed_events(
    parsed_events: List[Dict[str, Any]],
    timestamp_str: str,
    present_event_ids: Optional[Iterable[int]] = None,
) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    # "Present" = anything the parser actually saw in this snapshot, including live
    # events that temporarily had zero odds (e.g. a quarter-break line pull) — not
    # just events that made it into parsed_events.
    present_ids = set(present_event_ids) if present_event_ids is not None else set()
    present_ids.update(e["event_id"] for e in parsed_events)

    if not present_ids or len(present_ids) < settings.MIN_SNAPSHOT_EVENTS:
        # Suspiciously small/empty snapshot — almost always a parser/API hiccup,
        # not "every live match ended at once". Don't touch grace-period counters.
        logger.warning(
            f"Snapshot too small ({len(present_ids)} events) — skipping finish-detection this cycle."
        )
    else:
        id_list = list(present_ids)

        cursor.execute("SELECT COUNT(*) AS c FROM events WHERE is_live = 1")
        live_before = cursor.fetchone()["c"] or 0

        cursor.execute(
            "SELECT COUNT(*) AS c FROM events WHERE is_live = 1 AND NOT (event_id = ANY(%s))",
            (id_list,),
        )
        missing_count = cursor.fetchone()["c"] or 0

        # No global "% of live table disappeared" guard here on purpose: it used to
        # freeze ALL grace-period tracking (below) whenever too much of the live table
        # looked missing at once. But that ratio is measured against live_before, which
        # is the live table's own (possibly already backlogged) size — once any backlog
        # built up for any reason, every future cycle looked "suspicious" too, which
        # blocked the very tracking that would clear the backlog. Permanent deadlock.
        # The per-event check below (miss_count >= threshold AND missing_since older
        # than the grace window) already protects against a single bad snapshot
        # wrongly finalizing real live matches, without this failure mode.
        if missing_count:
            logger.info(f"{missing_count}/{live_before} live events missing from this snapshot.")

        # Present events: reset the miss counter.
        cursor.execute(
            """UPDATE events
                   SET miss_count = 0, missing_since = NULL
                 WHERE event_id = ANY(%s)
                   AND (miss_count > 0 OR missing_since IS NOT NULL)""",
            (id_list,),
        )
        # Missing events: increment the counter and record when they first went missing.
        cursor.execute(
            """UPDATE events
                   SET miss_count = COALESCE(miss_count, 0) + 1,
                       missing_since = COALESCE(missing_since, %s)
                 WHERE is_live = 1
                   AND NOT (event_id = ANY(%s))""",
            (timestamp_str, id_list),
        )
        # Finalize only once BOTH thresholds are satisfied (consecutive misses AND
        # a minimum grace window since the event first went missing) — see settings.py
        # for why this is a single short window for every sport, not sport-dependent.
        cursor.execute(
            """UPDATE events
                  SET is_live = 0
                WHERE is_live = 1
                  AND miss_count >= %s
                  AND missing_since IS NOT NULL
                  AND EXTRACT(EPOCH FROM (%s::timestamptz - missing_since::timestamptz)) / 60.0 >= %s""",
            (settings.EVENT_MISS_THRESHOLD, timestamp_str, settings.EVENT_MISS_GRACE_MINUTES),
        )
        if cursor.rowcount:
            logger.info(f"Finalized {cursor.rowcount} events after grace period.")

    # Archive non-live finished events
    archive_finished_events(cursor, timestamp_str)

    # Settle any open live_bets whose event just got archived above — right here, in the
    # same cycle the event finalizes, instead of waiting for ai_service's next inference
    # tick (which used to be the only place settlement happened, see plan notes).
    settle_result = settle_live_bets(timestamp_str)

    for ev in parsed_events:
        eid = ev["event_id"]
        sub_markets_json = json.dumps(ev.get("sub_markets", []), ensure_ascii=False)
        period_scores_json = json.dumps(ev.get("period_scores") or [])
        named_scores_json = json.dumps(
            {k: list(v) for k, v in (ev.get("named_scores") or {}).items()}, ensure_ascii=False
        )

        # Never let period_scores_json/named_scores_json shrink. Fonbet's live feed
        # occasionally omits the "scores"/subscores field on a single poll (seen right
        # around a match finishing/transitioning), which parses to an empty list/dict —
        # blindly overwriting with that would erase periods we'd already captured, and
        # since this can happen on the very last poll before the event goes into its
        # grace period and archives, that loss would be permanent (nothing upstream ever
        # re-derives it). Keep whichever value has more entries — computed in Python
        # (rather than SQL's json_array_length/CASE, which the sqlite version used) since
        # that requires no JSON-array-length function beyond what psycopg2 needs anyway.
        cursor.execute(
            "SELECT period_scores_json, named_scores_json FROM events WHERE event_id = %s", (eid,)
        )
        existing = cursor.fetchone()
        existing_period_json = existing["period_scores_json"] if existing else None
        existing_named_json = existing["named_scores_json"] if existing else None

        try:
            new_period_len = len(json.loads(period_scores_json or "[]"))
        except Exception:
            new_period_len = 0
        try:
            old_period_len = len(json.loads(existing_period_json or "[]"))
        except Exception:
            old_period_len = 0
        period_scores_json_final = (
            period_scores_json if new_period_len >= old_period_len else (existing_period_json or "[]")
        )

        new_named_len = len(named_scores_json or "{}")
        old_named_len = len(existing_named_json or "{}")
        named_scores_json_final = (
            named_scores_json if new_named_len >= old_named_len else (existing_named_json or "{}")
        )

        cursor.execute("""
            INSERT INTO events (
                event_id, sport_id, sport_path, match_name, team_1, team_2,
                score_1, score_2, score, timer, is_live, sub_markets_json,
                total_odds_count, last_updated_at, miss_count, missing_since,
                period_scores_json, named_scores_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, 0, NULL, %s, %s)
            ON CONFLICT(event_id) DO UPDATE SET
                sport_id = excluded.sport_id,
                sport_path = excluded.sport_path,
                match_name = excluded.match_name,
                team_1 = excluded.team_1,
                team_2 = excluded.team_2,
                score_1 = excluded.score_1,
                score_2 = excluded.score_2,
                score = excluded.score,
                timer = excluded.timer,
                is_live = 1,
                sub_markets_json = excluded.sub_markets_json,
                total_odds_count = excluded.total_odds_count,
                last_updated_at = excluded.last_updated_at,
                miss_count = 0,
                missing_since = NULL,
                period_scores_json = excluded.period_scores_json,
                named_scores_json = excluded.named_scores_json;
        """, (
            eid, ev.get("sport_id"), ev.get("sport_path"), ev.get("match_name"),
            ev.get("team_1"), ev.get("team_2"), ev.get("score_1", 0), ev.get("score_2", 0),
            ev.get("score", "0:0"), ev.get("timer", ""), sub_markets_json,
            ev.get("total_odds_count", 0), timestamp_str, period_scores_json_final, named_scores_json_final
        ))

        # Insert odds history & update latest odds
        for odd in ev.get("odds", []):
            fid = odd["factor_id"]
            prefix = odd.get("market_prefix", "")
            param = str(odd.get("parameter", "")) if odd.get("parameter") is not None else ""
            coeff = float(odd.get("coefficient", 0.0))
            label = odd.get("label", "")
            score_str = ev.get("score", "0:0")

            # Save historical record
            cursor.execute("""
                INSERT INTO odds_history (
                    event_id, factor_id, market_prefix, label, parameter, coefficient, score_at_time, timestamp
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (eid, fid, prefix, label, param, coeff, score_str, timestamp_str))

            # Upsert latest odds (preserves initial_coefficient on conflict)
            cursor.execute("""
                INSERT INTO latest_odds (
                    event_id, factor_id, market_prefix, label, parameter, coefficient, initial_coefficient, score_at_time, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                    coefficient = excluded.coefficient,
                    score_at_time = excluded.score_at_time,
                    updated_at = excluded.updated_at;
            """, (eid, fid, prefix, label, param, coeff, coeff, score_str, timestamp_str))

    conn.commit()
    release_connection(conn)

    # Runs after the commit above so it sees this cycle's freshly-written
    # period_scores_json — settles period-scoped bets ("1-й тайм", "2-й период", ...)
    # the moment their own period ends, instead of waiting for the whole match. Wrapped
    # separately: this opens its own connections and occasionally loses a SQLite lock
    # race against the many concurrent API reads — that must not take down the whole
    # scrape cycle (events/odds above are already safely committed either way). A
    # skipped cycle here just means the affected bet settles on the next one instead.
    try:
        period_settle_result = settle_completed_period_bets(timestamp_str)
        settle_result["settled"] += period_settle_result["settled"]
        settle_result["won"] += period_settle_result["won"]
        settle_result["lost"] += period_settle_result["lost"]
        settle_result["void"] += period_settle_result["void"]
        settle_result["messages"] += period_settle_result["messages"]
    except Exception as e:
        logger.error(f"Error settling completed-period bets (will retry next cycle): {e}")

    return settle_result

def get_live_matches(sport_filter: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM events WHERE is_live = 1"
    params = []

    if sport_filter and sport_filter.lower() != "all":
        query += " AND sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    if search:
        query += " AND (match_name ILIKE %s OR team_1 ILIKE %s OR team_2 ILIKE %s)"
        s_param = f"%{search.lower()}%"
        params.extend([s_param, s_param, s_param])

    query += " ORDER BY sport_path ASC, event_id DESC"

    cursor.execute(query, params)
    events = [dict(r) for r in cursor.fetchall()]

    if not events:
        release_connection(conn)
        return []

    live_event_ids = [e["event_id"] for e in events]

    # Batch odds lookups across ALL live events in two queries instead of one query per
    # event (which meant 1000+ correlated-subquery round trips once the live snapshot
    # grew past ~1000 events — that turned this endpoint into a multi-minute hang). The
    # initial-coefficient query uses a window function to grab the first-ever coefficient
    # per (event_id, factor_id, parameter, market_prefix) group in a single pass over
    # odds_history, scoped to just the live events via the join.

    # l.updated_at = e.last_updated_at restricts this to markets actually present in each
    # event's most recent scrape snapshot. Without it, a market the bookmaker has since
    # pulled or replaced (e.g. a handicap line that moved from -1.5 to -2.5, or a
    # 1X double-chance market withdrawn mid-match) keeps its last-known coefficient
    # frozen in latest_odds forever — the event stays is_live=1 (other markets on it
    # keep refreshing), so this would otherwise keep showing users a phantom price.
    # e.last_updated_at = MAX(...) additionally catches an event that's vanished from
    # Fonbet's feed *entirely* (grace period hasn't finalized it yet) — both timestamps
    # freeze together in that case, so comparing them to each other alone never notices;
    # comparing against the latest successful scrape cycle's timestamp does.
    cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
    latest_scrape_ts = cursor.fetchone()["max"]
    cursor.execute("""
        SELECT l.event_id, l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient, l.score_at_time
        FROM latest_odds l
        JOIN events e ON e.event_id = l.event_id
        WHERE l.event_id = ANY(%s)
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = %s
    """, (live_event_ids, latest_scrape_ts))
    odds_rows = [dict(o) for o in cursor.fetchall()]

    cursor.execute("""
        SELECT event_id, factor_id, parameter, market_prefix, coefficient
        FROM (
            SELECT
                event_id, factor_id, parameter, market_prefix, coefficient,
                ROW_NUMBER() OVER (
                    PARTITION BY event_id, factor_id, COALESCE(parameter, ''), COALESCE(market_prefix, '')
                    ORDER BY id ASC
                ) AS rn
            FROM odds_history
            WHERE event_id = ANY(%s)
        ) sub
        WHERE rn = 1
    """, (live_event_ids,))
    initial_map = {
        (r["event_id"], r["factor_id"], r["parameter"] or "", r["market_prefix"] or ""): r["coefficient"]
        for r in cursor.fetchall()
    }

    odds_by_event: Dict[int, List[Dict[str, Any]]] = {}
    for o in odds_rows:
        key = (o["event_id"], o["factor_id"], o["parameter"] or "", o["market_prefix"] or "")
        odds_by_event.setdefault(o["event_id"], []).append({
            "factor_id": o["factor_id"],
            "market_prefix": o["market_prefix"],
            "label": o["label"],
            "parameter": o["parameter"],
            "coefficient": o["coefficient"],
            "initial_coefficient": initial_map.get(key, o["coefficient"]),
            "score_at_time": o["score_at_time"],
        })

    result = []
    for match_dict in events:
        match_dict["sub_markets"] = json.loads(match_dict.get("sub_markets_json") or "[]")
        match_dict["odds"] = odds_by_event.get(match_dict["event_id"], [])
        result.append(match_dict)

    release_connection(conn)
    return result

def get_odds_history(event_id: int, factor_id: int, parameter: Optional[str] = None, market_prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT id, event_id, factor_id, market_prefix, label, parameter, coefficient, score_at_time, timestamp FROM odds_history WHERE event_id = %s AND factor_id = %s"
    params = [event_id, factor_id]

    if parameter is not None:
        query += " AND parameter = %s"
        params.append(parameter)

    if market_prefix is not None:
        query += " AND market_prefix = %s"
        params.append(market_prefix)

    query += " ORDER BY id ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    release_connection(conn)

    return [dict(r) for r in rows]

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

def get_db_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS c FROM events WHERE is_live = 1")
    live_count = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM events")
    total_events = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM odds_history")
    history_count = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM ai_predictions")
    predictions_count = cursor.fetchone()["c"]

    cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
    last_updated = cursor.fetchone()["max"]

    cursor.execute("SELECT pg_database_size(current_database()) AS size")
    total_db_size_bytes = cursor.fetchone()["size"] or 0

    release_connection(conn)

    # Query Dedicated Finished Events DB (autobet_finished.db)
    finished_count = 0
    finished_history_count = 0
    unresolved_bets_count = 0
    # None means "not enough resolved bets yet to compute a real accuracy" — the
    # frontend must show this as "no data", never fall back to a made-up number.
    error_rate_pct = None
    accuracy_pct = None
    try:
        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        f_cursor.execute("SELECT COUNT(*) AS c FROM finished_events")
        finished_count = f_cursor.fetchone()["c"]

        f_cursor.execute("SELECT COUNT(*) AS c FROM finished_bets")
        finished_history_count = f_cursor.fetchone()["c"]

        # Calculate real AI prediction error rate on completed, resolvable bets (is_win = 1 vs 0).
        # Bets we couldn't honestly grade (is_win IS NULL — forecasts on forfeits/part-of-match
        # markets etc.) are excluded rather than counted as losses.
        f_cursor.execute("""
            SELECT COUNT(*) AS total_eval, SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) AS wins
              FROM finished_bets
             WHERE initial_coefficient >= 1.10 AND initial_coefficient <= 2.10
               AND is_win IS NOT NULL
        """)
        row = f_cursor.fetchone()
        if row and row["total_eval"] and row["total_eval"] > 0:
            total_eval = row["total_eval"]
            wins = row["wins"] or 0
            losses = total_eval - wins
            error_rate_pct = round((losses / total_eval) * 100.0, 1)
            accuracy_pct = round(100.0 - error_rate_pct, 1)

        f_cursor.execute("SELECT COUNT(*) AS c FROM finished_bets WHERE is_win IS NULL")
        unresolved_bets_count = f_cursor.fetchone()["c"] or 0

        release_connection(f_conn)
    except Exception as e:
        logger.error(f"Error querying finished db stats: {e}")

    return {
        "live_events_count": live_count,
        "total_events_count": total_events,
        "finished_events_count": finished_count,
        "finished_odds_history_count": finished_history_count,
        "unresolved_bets_count": unresolved_bets_count,
        "total_odds_history_count": history_count,
        "ai_predictions_count": predictions_count,
        "error_rate_pct": error_rate_pct,
        "accuracy_pct": accuracy_pct,
        "last_updated_at": last_updated,
        "db_size_bytes": total_db_size_bytes,
        "db_size_formatted": format_file_size(total_db_size_bytes)
    }

def save_ai_predictions(predictions: List[Dict[str, Any]], timestamp_str: str):
    conn = get_connection()
    cursor = conn.cursor()

    for p in predictions:
        cursor.execute("""
            INSERT INTO ai_predictions (
                event_id, factor_id, market_prefix, parameter,
                win_probability, error_rate, expected_roi,
                lightgbm_score, pytorch_score, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO UPDATE SET
                win_probability = excluded.win_probability,
                error_rate = excluded.error_rate,
                expected_roi = excluded.expected_roi,
                lightgbm_score = excluded.lightgbm_score,
                pytorch_score = excluded.pytorch_score,
                updated_at = excluded.updated_at;
        """, (
            p["event_id"], p["factor_id"], p.get("market_prefix", ""), str(p.get("parameter", "")),
            p["win_probability"], p["error_rate"], p["expected_roi"],
            p.get("lightgbm_score", 0.0), p.get("pytorch_score", 0.0), timestamp_str
        ))

    conn.commit()
    release_connection(conn)

# Pseudo-count for the Bayesian shrinkage used in calibration below. Small buckets (few
# resolved bets so far) stay close to the model's raw self-reported probability; buckets
# with plenty of real results pull hard towards what actually happened historically.
CALIBRATION_PRIOR_STRENGTH = 20

def get_calibration_buckets() -> Dict[int, tuple]:
    """
    Returns {decile: (wins, total)} built from every resolved finished bet where we know
    what the model predicted at bet time — i.e. the model's actual historical track record,
    bucketed by what confidence it claimed. Used to correct the raw model probability into
    one that reflects real-world win rate instead of the model's own (often overconfident)
    self-estimate.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    # FLOOR(...)::INTEGER, not CAST(... AS INTEGER) — Postgres' CAST-to-integer rounds to
    # the nearest integer, while SQLite's truncates towards zero; predicted_win_probability
    # is always >= 0 here so FLOOR reproduces the original truncating bucket assignment.
    f_cursor.execute("""
        SELECT FLOOR(predicted_win_probability / 10)::INTEGER AS bucket,
               COUNT(*) AS total,
               SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) AS wins
          FROM finished_bets
         WHERE is_win IS NOT NULL AND predicted_win_probability IS NOT NULL
         GROUP BY bucket
    """)
    rows = f_cursor.fetchall()
    release_connection(f_conn)
    return {int(r["bucket"]): (r["wins"] or 0, r["total"] or 0) for r in rows}

def calibrate_probability(raw_prob: float, buckets: Dict[int, tuple]) -> float:
    """
    Blends the model's raw probability with the empirical win rate of bets that were
    historically predicted at a similar confidence (Bayesian shrinkage towards real
    outcomes). A bucket with 1000 resolved bets dominates; a bucket with 2 resolved bets
    barely moves the number away from the raw estimate.
    """
    bucket = min(9, max(0, int(raw_prob // 10)))
    wins, total = buckets.get(bucket, (0, 0))
    prior_wins = (raw_prob / 100.0) * CALIBRATION_PRIOR_STRENGTH
    calibrated = (wins + prior_wins) / (total + CALIBRATION_PRIOR_STRENGTH) * 100.0
    return round(min(max(calibrated, 1.0), 99.0), 1)

def get_top_neurobets(
    sport_filter: Optional[str] = None,
    sort_mode: str = "best",
    min_odds: float = 1.1,
    max_odds: float = 2.1,
    limit: int = 50,
    offset: int = 0,
    min_confidence: float = 70.0,
) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    # INNER JOIN ai_predictions (not LEFT JOIN + a 1/coefficient formula fallback) — this
    # list must reflect only what the trained model actually evaluated, the same as real
    # bet placement does (ai_service/app/neuralbet/pipeline.py never uses a heuristic
    # fallback either). A market the model hasn't scored yet just doesn't appear here
    # rather than showing a guess dressed up as a prediction.
    # l.updated_at = e.last_updated_at AND e.last_updated_at = MAX(...) is the same
    # staleness guard used everywhere else (get_live_matches, pipeline.py, bet
    # placement) — without it a market Fonbet has since pulled or replaced could still
    # show its last frozen prediction here.
    query = """
        SELECT
            e.event_id, e.sport_path, e.match_name, e.team_1, e.team_2, e.score, e.timer,
            l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient,
            COALESCE(
                (
                    SELECT h.coefficient
                    FROM odds_history h
                    WHERE h.event_id = l.event_id
                      AND h.factor_id = l.factor_id
                      AND COALESCE(CAST(h.parameter AS TEXT), '') = COALESCE(CAST(l.parameter AS TEXT), '')
                      AND COALESCE(h.market_prefix, '') = COALESCE(l.market_prefix, '')
                    ORDER BY h.id ASC
                    LIMIT 1
                ),
                l.coefficient
            ) AS initial_coefficient,
            p.win_probability AS win_probability,
            p.error_rate AS error_rate,
            p.expected_roi AS expected_roi,
            p.lightgbm_score AS lightgbm_score,
            p.pytorch_score AS pytorch_score
        FROM latest_odds l
        JOIN events e ON l.event_id = e.event_id
        JOIN ai_predictions p ON l.event_id = p.event_id
            AND l.factor_id = p.factor_id
            AND COALESCE(CAST(l.parameter AS TEXT), '') = COALESCE(CAST(p.parameter AS TEXT), '')
            AND COALESCE(l.market_prefix, '') = COALESCE(p.market_prefix, '')
        WHERE e.is_live = 1
          AND l.coefficient >= %s
          AND l.coefficient <= %s
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = (SELECT MAX(last_updated_at) FROM events)
    """
    params = [min_odds, max_odds]

    if sport_filter and sport_filter.lower() != "all":
        query += " AND e.sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    # No ORDER BY / LIMIT here: calibration (below) can reshuffle the ranking relative to
    # the model's raw self-reported numbers, so we sort in Python after calibrating —
    # sorting in SQL first would bias which row "wins" a de-dup group towards raw scores.
    query += " LIMIT 5000"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    release_connection(conn)

    # Correct the model's raw probability using its actual historical track record —
    # see calibrate_probability(). Falls back to the raw number when there's no
    # calibration data yet (buckets empty), so behavior degrades gracefully.
    buckets = get_calibration_buckets()
    candidates = []
    for r in rows:
        d = dict(r)
        raw_prob = d.get("win_probability")
        if raw_prob is None:
            continue
        calibrated = calibrate_probability(raw_prob, buckets)
        d["raw_win_probability"] = raw_prob
        d["win_probability"] = calibrated
        d["error_rate"] = round(100.0 - calibrated, 1)
        d["expected_roi"] = round((calibrated / 100.0) * d["coefficient"] - 1.0, 3) * 100.0
        d["expected_roi"] = round(d["expected_roi"], 1)
        candidates.append(d)

    if sort_mode == "best":
        candidates.sort(key=lambda d: (d["expected_roi"], d["win_probability"]), reverse=True)
    else:
        candidates.sort(key=lambda d: (d["win_probability"], -d["coefficient"]), reverse=True)

    # De-duplicate mutually-exclusive outcomes of the same market (e.g. "Тотал Больше 2.5"
    # and "Тотал Меньше 2.5" on the same event) so the list never recommends betting on
    # both sides of the same market. Rows arrive already sorted best-first, so keeping the
    # first row seen per group keeps the strongest pick and discards the rest.
    seen_groups = set()
    deduped = []
    for d in candidates:
        family = OUTCOME_FAMILY_MAP.get(d["factor_id"])
        param = d.get("parameter")

        if family == "handicap":
            try:
                param_key = str(abs(float(param)))
            except (TypeError, ValueError):
                param_key = str(param)
        elif family in ("total", "itotal1", "itotal2"):
            param_key = str(param)
        elif family in ("1x2", "btts", "proход"):
            param_key = ""
        else:
            family = f"solo_{d['factor_id']}"
            param_key = str(param)

        group_key = (d["event_id"], d["market_prefix"], family, param_key)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        # Only count as an actual "bet" if, based on real historical performance at this
        # confidence level, the outcome is genuinely likely to hit — not just the model's
        # raw (often overconfident) self-estimate scraping past 50%.
        if d["win_probability"] < min_confidence:
            continue

        deduped.append(d)

    total = len(deduped)
    page = deduped[offset: offset + limit] if limit else deduped[offset:]

    return {"items": page, "total": total}

def _void_open_live_bets():
    """
    Voids every currently-open bot bet (refunds the stake, marks it 'void') before the
    live DB is wiped out from under it. Without this, a bet open on an event that's
    about to be deleted — never archived into finished_bets — would sit in live_bets
    forever with its stake stuck in `locked`, since nothing would ever resolve it.
    A voided bet returns the stake untouched, same rule as an ungradable outcome.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("SELECT id, stake FROM live_bets WHERE status = 'open'")
    open_bets = f_cursor.fetchall()
    if not open_bets:
        release_connection(f_conn)
        return 0
    total_stake = sum(b["stake"] or 0.0 for b in open_bets)
    f_cursor.execute(
        "UPDATE live_bets SET status = 'void', payout = stake, settled_at = now() WHERE status = 'open';"
    )
    f_cursor.execute(
        "UPDATE bankroll_accounts SET balance = balance + %s, locked = GREATEST(locked - %s, 0), updated_at = now() WHERE account = 'live';",
        (total_stake, total_stake),
    )
    f_conn.commit()
    release_connection(f_conn)
    return len(open_bets)

def reset_live_database():
    """
    Clears live operational data: events, latest_odds, odds_history, ai_predictions.
    Also voids (refunds) any bot bets still open on that data — see _void_open_live_bets.
    """
    voided = _void_open_live_bets()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events;")
    cursor.execute("DELETE FROM latest_odds;")
    cursor.execute("DELETE FROM odds_history;")
    cursor.execute("DELETE FROM ai_predictions;")
    conn.commit()
    # No explicit VACUUM here (unlike the SQLite version) — VACUUM can't run inside a
    # transaction block in Postgres, and Postgres autovacuum already reclaims this
    # space on its own, so there's nothing to replicate.
    release_connection(conn)
    logger.info(
        f"Successfully reset LIVE database tables (events, latest_odds, odds_history, "
        f"ai_predictions); voided {voided} open bot bet(s)."
    )

def reset_all_databases():
    """
    Clears live operational data as well as archived finished training tables and stored model checkpoints.
    """
    reset_live_database()

    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("DELETE FROM finished_events;")
    f_cursor.execute("DELETE FROM finished_bets;")
    f_cursor.execute("DELETE FROM finished_odds_history;")
    f_cursor.execute("DELETE FROM live_bets;")
    f_cursor.execute("DELETE FROM bankroll_ledger;")
    f_cursor.execute("""
        UPDATE bankroll_accounts SET
            balance = start_balance, peak_balance = start_balance, locked = 0,
            rounds = 0, bets_placed = 0, wins = 0, losses = 0,
            total_staked = 0, total_returned = 0, ruin_count = 0, is_ruined = 0,
            updated_at = now();
    """)
    f_conn.commit()
    # See reset_live_database's comment — no explicit VACUUM under Postgres.
    release_connection(f_conn)

    # Both model checkpoints must go — leaving lightgbm_model.txt behind after a "full
    # reset" would keep serving predictions from a booster trained on data that's now gone.
    model_dir = "/app/data/models"
    for fname in ("pytorch_gru.pt", "lightgbm_model.txt"):
        fpath = os.path.join(model_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass

    logger.info("Successfully reset ALL databases (LIVE & Finished training archive), bankroll accounts, and cleared model checkpoints.")

def get_neurobets_history(
    sport_filter: Optional[str] = None,
    search: Optional[str] = None,
    min_odds: float = 1.1,
    max_odds: float = 2.1,
    outcome_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> Dict[str, Any]:
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    base_query = """
        FROM finished_bets h
        JOIN finished_events e ON h.event_id = e.event_id
        WHERE h.initial_coefficient >= %s AND h.initial_coefficient <= %s
    """
    params = [min_odds, max_odds]

    if sport_filter and sport_filter.lower() != "all":
        base_query += " AND e.sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    if search:
        base_query += " AND (e.match_name ILIKE %s OR e.team_1 ILIKE %s OR e.team_2 ILIKE %s)"
        s = f"%{search.lower()}%"
        params.extend([s, s, s])

    # Summary Statistics — four states: win / loss / push / pending. is_win IS NULL
    # covers both "push" (line landed exactly on the bet — legitimate return, is_push=1)
    # and "pending" (we genuinely can't grade it — is_push=0); they used to be
    # conflated under one "не рассчитано" bucket, which misleadingly looked like a bug
    # for bets that actually resolved correctly as a return. Kept unfiltered by
    # outcome_filter so the counts stay the same totals regardless of which outcome tab
    # is selected — that's what lets them double as filter buttons.
    count_query = f"""
        SELECT COUNT(*) AS total_count,
               SUM(CASE WHEN h.is_win = 1 THEN 1 ELSE 0 END) AS wins_count,
               SUM(CASE WHEN h.is_win = 0 THEN 1 ELSE 0 END) AS losses_count,
               SUM(CASE WHEN h.is_win IS NULL AND COALESCE(h.is_push, 0) = 1 THEN 1 ELSE 0 END) AS push_count,
               SUM(CASE WHEN h.is_win IS NULL AND COALESCE(h.is_push, 0) = 0 THEN 1 ELSE 0 END) AS pending_count
        {base_query}
    """
    f_cursor.execute(count_query, params)
    summary_row = f_cursor.fetchone()

    total_count = summary_row["total_count"] or 0
    wins_count = summary_row["wins_count"] or 0
    losses_count = summary_row["losses_count"] or 0
    push_count = summary_row["push_count"] or 0
    pending_count = summary_row["pending_count"] or 0
    resolved_count = wins_count + losses_count
    win_rate_pct = round((wins_count / resolved_count * 100.0), 1) if resolved_count > 0 else 0.0

    # Fetch History Items — outcome_filter narrows only the list, not the summary above.
    filtered_query = base_query
    if outcome_filter == "win":
        filtered_query += " AND h.is_win = 1"
    elif outcome_filter == "loss":
        filtered_query += " AND h.is_win = 0"
    elif outcome_filter == "push":
        filtered_query += " AND h.is_win IS NULL AND COALESCE(h.is_push, 0) = 1"
    elif outcome_filter == "pending":
        filtered_query += " AND h.is_win IS NULL AND COALESCE(h.is_push, 0) = 0"

    data_query = f"""
        SELECT
            h.id AS id, h.event_id, h.factor_id, h.market_prefix, h.label, h.parameter,
            h.initial_coefficient, h.final_coefficient, h.score_at_time, h.is_win, h.is_push,
            h.first_seen_at AS timestamp, h.finished_at,
            e.sport_path, e.match_name, e.team_1, e.team_2, e.score_1, e.score_2, e.score
        {filtered_query}
        ORDER BY h.finished_at DESC, h.id DESC
        LIMIT %s OFFSET %s
    """
    f_cursor.execute(data_query, params + [limit, offset])
    rows = f_cursor.fetchall()
    release_connection(f_conn)

    history_items = []
    for r in rows:
        item = dict(r)
        coeff = item.get("initial_coefficient") or item.get("final_coefficient") or 1.5
        iw = item.get("is_win")
        adj = 4.0 if iw == 1 else (-4.0 if iw == 0 else 0.0)
        win_prob = round(min(max((1.0 / coeff * 100.0) + adj, 12.0), 95.0), 1)
        item["win_probability"] = win_prob
        history_items.append(item)

    filtered_count = {
        "win": wins_count, "loss": losses_count, "push": push_count, "pending": pending_count,
    }.get(outcome_filter, total_count)

    return {
        "summary": {
            "total_count": total_count,
            "wins_count": wins_count,
            "losses_count": losses_count,
            "push_count": push_count,
            "pending_count": pending_count,
            "resolved_count": resolved_count,
            "win_rate_pct": win_rate_pct,
            "error_rate_pct": round(100.0 - win_rate_pct, 1) if resolved_count > 0 else 0.0,
            # Count matching the active outcome_filter — what infinite-scroll pagination
            # should compare its offset against, since total_count always stays the
            # unfiltered grand total (see the comment above count_query).
            "filtered_count": filtered_count
        },
        "history": history_items
    }


def _sanitize_non_finite(value: Any) -> Any:
    """Replaces inf/-inf/NaN floats with None so FastAPI's strict JSON encoder (which
    rejects them per spec) never 500s the response — old ledger rows can carry these
    from a since-fixed float-overflow bug, and this keeps that history readable."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize_non_finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_non_finite(v) for v in value]
    return value


def get_bankroll_state() -> Dict[str, Any]:
    """
    Reads the neural bettor's bankroll state directly from autobet_finished.db —
    written by ai_service/app/neuralbet/bankroll.py, which shares this same file over
    the ./data volume. See docs in that module for the "training" vs "live" account
    split. Returns both accounts plus each one's recent ledger for a balance curve.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    accounts = {}
    for account in ("training", "live"):
        f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = %s", (account,))
        row = f_cursor.fetchone()
        accounts[account] = dict(row) if row else None

    ledger = {}
    for account in ("training", "live"):
        f_cursor.execute(
            "SELECT * FROM bankroll_ledger WHERE account = %s ORDER BY id DESC LIMIT 200",
            (account,),
        )
        rows = f_cursor.fetchall()
        ledger[account] = [dict(r) for r in rows]

    release_connection(f_conn)
    return _sanitize_non_finite({"accounts": accounts, "ledger": ledger})


def get_live_bets(limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """The bot's actual simulated live bets (open + settled), newest first — enriched
    with how the underlying match/market looks *right now* (current score, timer,
    coefficient), not just what it looked like when the bet was placed."""
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("SELECT COUNT(*) AS c FROM live_bets")
    total = f_cursor.fetchone()["c"]
    f_cursor.execute("SELECT * FROM live_bets ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
    rows = [dict(r) for r in f_cursor.fetchall()]
    release_connection(f_conn)

    if not rows:
        return {"total": total, "items": rows}

    event_ids = list({r["event_id"] for r in rows})

    conn = get_connection()
    cursor = conn.cursor()
    # An event stuck in the grace period after vanishing from Fonbet's feed still has
    # is_live=1 with its score/timer/coefficients frozen — comparing last_updated_at to
    # the latest successful scrape cycle's timestamp (not just to itself) is what
    # actually tells "genuinely live right now" apart from "hasn't been finalized yet".
    cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
    latest_scrape_ts = cursor.fetchone()["max"]
    cursor.execute(
        "SELECT event_id, score, score_1, score_2, timer, is_live, last_updated_at, sport_path FROM events WHERE event_id = ANY(%s)",
        (event_ids,),
    )
    live_info = {r["event_id"]: dict(r) for r in cursor.fetchall()}
    cursor.execute(
        """SELECT l.event_id, l.factor_id, l.parameter, l.market_prefix, l.coefficient
            FROM latest_odds l
            JOIN events e ON e.event_id = l.event_id
            WHERE l.event_id = ANY(%s)
              AND l.updated_at = e.last_updated_at
              AND e.last_updated_at = %s""",
        (event_ids, latest_scrape_ts),
    )
    current_odds = {
        (r["event_id"], r["factor_id"], r["parameter"] or "", r["market_prefix"] or ""): r["coefficient"]
        for r in cursor.fetchall()
    }
    release_connection(conn)

    # Events not in the live DB anymore have already been archived — fall back to their
    # final score/coefficient so the card still shows something meaningful instead of blanks.
    missing_ids = [eid for eid in event_ids if eid not in live_info]
    finished_info: Dict[int, Dict[str, Any]] = {}
    finished_odds: Dict[tuple, float] = {}
    if missing_ids:
        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        f_cursor.execute(
            "SELECT event_id, score, score_1, score_2, sport_path FROM finished_events WHERE event_id = ANY(%s)",
            (missing_ids,),
        )
        finished_info = {r["event_id"]: dict(r) for r in f_cursor.fetchall()}
        f_cursor.execute(
            "SELECT event_id, factor_id, parameter, market_prefix, final_coefficient FROM finished_bets WHERE event_id = ANY(%s)",
            (missing_ids,),
        )
        finished_odds = {
            (r["event_id"], r["factor_id"], r["parameter"] or "", r["market_prefix"] or ""): r["final_coefficient"]
            for r in f_cursor.fetchall()
        }
        release_connection(f_conn)

    for b in rows:
        eid = b["event_id"]
        odds_key = (eid, b["factor_id"], b["parameter"] or "", b["market_prefix"] or "")
        if eid in live_info:
            info = live_info[eid]
            b["current_score"] = info["score"]
            b["current_timer"] = info["timer"]
            b["match_is_live"] = bool(info["is_live"]) and info["last_updated_at"] == latest_scrape_ts
            b["current_coefficient"] = current_odds.get(odds_key)
            b["sport_path"] = info["sport_path"]
        else:
            info = finished_info.get(eid)
            b["current_score"] = info["score"] if info else None
            b["current_timer"] = None
            b["match_is_live"] = False
            b["current_coefficient"] = finished_odds.get(odds_key)
            b["sport_path"] = info["sport_path"] if info else None

    return {"total": total, "items": rows}


def cancel_open_live_bets() -> Dict[str, Any]:
    """Admin action: void every currently-open live bet and refund its stake in full —
    "as if it was never placed," not a win/loss/void settlement outcome. Unlike
    settle_live_bets() this doesn't wait for the underlying event to finish."""
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("SELECT * FROM live_bets WHERE status = 'open'")
    open_bets = f_cursor.fetchall()
    if not open_bets:
        release_connection(f_conn)
        return {"cancelled": 0, "refunded": 0.0, "messages": []}

    messages: List[Dict[str, str]] = []
    total_refunded = 0.0

    for b in open_bets:
        stake = b["stake"]
        f_cursor.execute(
            "UPDATE live_bets SET status = 'cancelled', payout = %s, settled_at = %s WHERE id = %s",
            (stake, _now_iso(), b["id"]),
        )
        f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = 'live'")
        acc = f_cursor.fetchone()
        balance_after = acc["balance"] + stake
        locked_after = max(acc["locked"] - stake, 0.0)
        f_cursor.execute("""
            UPDATE bankroll_accounts SET
                balance = %s, locked = %s, peak_balance = GREATEST(peak_balance, %s), updated_at = %s
            WHERE account = 'live'
        """, (balance_after, locked_after, balance_after, _now_iso()))
        f_cursor.execute("""
            INSERT INTO bankroll_ledger
                (account, round_no, balance_before, balance_after, staked, returned, bets_count, ruined, created_at)
            VALUES ('live', NULL, %s, %s, %s, %s, 1, 0, %s)
        """, (acc["balance"], balance_after, stake, stake, _now_iso()))

        total_refunded += stake
        messages.append({
            "category": "BANKROLL", "level": "WARNING",
            "message": (
                f"🟠 Ставка отменена администратором: "
                f"{_outcome_desc(b['match_name'], b['market_prefix'], b['label'], b['parameter'])} — "
                f"{stake:.1f} ₽ возвращено на баланс."
            ),
        })

    f_conn.commit()
    release_connection(f_conn)
    return {"cancelled": len(open_bets), "refunded": total_refunded, "messages": messages}


# ---------------------------------------------------------------------------
# Live betting execution — backend is the sole writer of live_bets /
# bankroll_accounts / bankroll_ledger. ai_service (the neural net) only reads its
# balance and proposes candidates over HTTP (see /api/internal/*), it never touches
# these tables directly. This is what lets bet placement re-validate market freshness
# against backend's own just-updated live data, and lets settlement fire the moment
# an event is archived (inside save_parsed_events) instead of lagging behind whatever
# ai_service's own inference cycle happens to run next.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

LIVE_START_BALANCE = float(os.getenv("BANKROLL_START_BALANCE", "1000.0"))
# See ai_service/app/neuralbet/bankroll.py's RUIN_THRESHOLD docstring: a balance that's
# technically > 0 but too small to ever clear a real minimum stake again is functionally
# ruined — without this floor, float rounding could strand an account near-zero forever.
LIVE_RUIN_THRESHOLD = 0.01
# Hard cap on concurrently open live bets, enforced here (not just via ai_service's
# per-round MAX_POSITIONS) since positions accumulate across multiple rounds — a round
# staying under its own per-round cap doesn't stop the *total* open count from growing
# past it over several cycles.
MAX_OPEN_LIVE_POSITIONS = int(os.getenv("BANKROLL_MAX_OPEN_POSITIONS", "6"))


def _outcome_desc(match_name: str, market_prefix: str, label: str, parameter: Any) -> str:
    desc = f"{match_name} — {market_prefix} {label}"
    if parameter:
        desc += f" ({parameter})"
    return desc


def reset_live_account(start_balance: Optional[float] = None) -> Dict[str, Any]:
    """Manual reset — the only way the 'live' account can come back from ruin besides
    the automatic reset settle_live_bets() does when balance hits zero."""
    sb = start_balance if start_balance is not None else LIVE_START_BALANCE
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("""
        INSERT INTO bankroll_accounts (account, balance, start_balance, peak_balance, updated_at)
        VALUES ('live', %s, %s, %s, %s)
        ON CONFLICT(account) DO UPDATE SET
            balance = excluded.balance, start_balance = excluded.start_balance,
            peak_balance = excluded.peak_balance, locked = 0, is_ruined = 0,
            updated_at = excluded.updated_at;
    """, (sb, sb, sb, _now_iso()))
    f_conn.commit()
    release_connection(f_conn)
    return get_live_account()


def get_live_account() -> Dict[str, Any]:
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = 'live'")
    row = f_cursor.fetchone()
    release_connection(f_conn)
    if row is None:
        return {
            "account": "live", "balance": LIVE_START_BALANCE, "start_balance": LIVE_START_BALANCE,
            "peak_balance": LIVE_START_BALANCE, "locked": 0.0, "rounds": 0, "bets_placed": 0,
            "wins": 0, "losses": 0, "total_staked": 0.0, "total_returned": 0.0,
            "ruin_count": 0, "is_ruined": 0, "updated_at": None,
        }
    return dict(row)


def _is_market_fresh(cursor, event_id: int, factor_id: int, parameter: str, market_prefix: str) -> bool:
    """A market is only bettable if it was actually present in the *most recent
    successful scrape cycle* — not just "the market's own last update matches its
    event's last update," which sounds right but isn't: if the whole event has quietly
    vanished from Fonbet's live feed (grace period hasn't finalized it yet), both
    l.updated_at and e.last_updated_at are frozen together at the same old timestamp, so
    that comparison trivially "passes" forever. Comparing against MAX(last_updated_at)
    across all events (the timestamp every event/market touched in the latest scrape
    shares) catches that: an event that didn't appear in this cycle's snapshot falls
    behind it immediately, market-level staleness or not."""
    cursor.execute("""
        SELECT 1
        FROM latest_odds l
        JOIN events e ON e.event_id = l.event_id
        WHERE l.event_id = %s AND l.factor_id = %s
          AND COALESCE(l.parameter, '') = COALESCE(%s, '')
          AND COALESCE(l.market_prefix, '') = COALESCE(%s, '')
          AND e.is_live = 1
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = (SELECT MAX(last_updated_at) FROM events)
    """, (event_id, factor_id, parameter, market_prefix))
    row = cursor.fetchone()
    return row is not None


def place_live_bet_candidates(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Each candidate is {event_id, factor_id, market_prefix, parameter, label, match_name,
    coefficient, stake_fraction, win_probability}, pre-sorted by ai_service's own
    expected_roi. Freshness is re-checked here, against backend's own live database,
    right before the money moves — not trusted from whatever ai_service last saw.
    """
    conn = get_connection()
    cursor = conn.cursor()

    account = get_live_account()
    available = account["balance"]

    placed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    messages: List[Dict[str, str]] = []

    if available <= 0:
        release_connection(conn)
        return {
            "placed": placed,
            "skipped": [{"candidate": c, "reason": "insufficient_balance"} for c in candidates],
            "messages": messages,
        }

    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    f_cursor.execute("SELECT COUNT(*) AS c FROM live_bets WHERE status = 'open'")
    open_count = f_cursor.fetchone()["c"]
    slots_left = MAX_OPEN_LIVE_POSITIONS - open_count

    for c in candidates:
        event_id, factor_id = c["event_id"], c["factor_id"]
        parameter = c.get("parameter") or ""
        market_prefix = c.get("market_prefix") or ""

        if slots_left <= 0:
            skipped.append({"candidate": c, "reason": "max_positions_reached"})
            continue

        if not _is_market_fresh(cursor, event_id, factor_id, parameter, market_prefix):
            skipped.append({"candidate": c, "reason": "stale_market"})
            continue

        stake = available * c["stake_fraction"]
        if stake <= 0:
            skipped.append({"candidate": c, "reason": "zero_stake"})
            continue

        f_cursor.execute("""
            INSERT INTO live_bets (
                event_id, factor_id, market_prefix, parameter, label, match_name,
                coefficient, stake, stake_fraction, win_probability, status, placed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s)
            ON CONFLICT(event_id, factor_id, parameter, market_prefix) DO NOTHING;
        """, (
            event_id, factor_id, market_prefix, parameter, c.get("label", ""), c.get("match_name", ""),
            c["coefficient"], stake, c["stake_fraction"], c["win_probability"], _now_iso(),
        ))
        if f_cursor.rowcount == 0:
            skipped.append({"candidate": c, "reason": "already_open"})
            continue

        f_cursor.execute("""
            UPDATE bankroll_accounts SET
                balance = balance - %s, locked = locked + %s, bets_placed = bets_placed + 1,
                total_staked = total_staked + %s, updated_at = %s
            WHERE account = 'live'
        """, (stake, stake, stake, _now_iso()))

        available -= stake  # keep this batch from over-committing the same balance
        slots_left -= 1
        placed.append({**c, "stake": stake})
        messages.append({
            "category": "BANKROLL",
            "level": "INFO",
            "message": (
                f"💰 Открыта ставка: {_outcome_desc(c.get('match_name',''), market_prefix, c.get('label',''), parameter)} "
                f"@ {c['coefficient']:.2f} — {stake:.1f} ₽ ({c['stake_fraction']*100.0:.1f}% банка), "
                f"вероятность {c['win_probability']:.1f}%, потенциальный выигрыш {stake * c['coefficient']:.1f} ₽."
            ),
        })

    f_conn.commit()
    release_connection(f_conn)
    release_connection(conn)
    return {"placed": placed, "skipped": skipped, "messages": messages}


def _apply_bet_settlement(f_cursor, b, is_win: Optional[int]) -> Tuple[str, List[Dict[str, str]]]:
    """
    Shared bookkeeping for resolving one open live_bet: writes its row, updates the live
    bankroll account (incl. the both-balance-and-locked ruin check), and logs the ledger
    entry. Returns (outcome, messages) where outcome is "won"/"lost"/"void". Shared by
    settle_live_bets() (full-match settlement, via finished_bets) and
    settle_completed_period_bets() (early settlement the moment a bet's own period ends,
    without waiting for the whole match) so the two paths can't drift apart.
    """
    stake = b["stake"]
    outcome_desc = _outcome_desc(b["match_name"], b["market_prefix"], b["label"], b["parameter"])
    messages: List[Dict[str, str]] = []

    if is_win is None:
        status, payout, outcome = "void", stake, "void"
        messages.append({
            "category": "BANKROLL", "level": "INFO",
            "message": f"⚪ Ставка аннулирована (исход не рассчитан): {outcome_desc} — {stake:.1f} ₽ возвращено на баланс.",
        })
    elif is_win:
        status, payout, outcome = "won", stake * b["coefficient"], "win"
        messages.append({
            "category": "BANKROLL", "level": "INFO",
            "message": (
                f"🟢 Ставка ВЫИГРАЛА: {outcome_desc} @ {b['coefficient']:.2f} — "
                f"поставлено {stake:.1f} ₽ → пришло {payout:.1f} ₽ (прибыль +{payout - stake:.1f} ₽)."
            ),
        })
    else:
        status, payout, outcome = "lost", 0.0, "loss"
        messages.append({
            "category": "BANKROLL", "level": "WARNING",
            "message": f"🔴 Ставка ПРОИГРАЛА: {outcome_desc} @ {b['coefficient']:.2f} — поставлено {stake:.1f} ₽ → сгорело {stake:.1f} ₽.",
        })

    f_cursor.execute(
        "UPDATE live_bets SET status = %s, payout = %s, settled_at = %s WHERE id = %s",
        (status, payout, _now_iso(), b["id"]),
    )

    f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = 'live'")
    acc = f_cursor.fetchone()
    balance_before = acc["balance"]
    balance_after = balance_before + payout
    locked_after = max(acc["locked"] - stake, 0.0)
    # Ruin requires BOTH spendable balance AND locked (stake sitting in other still-open
    # bets) to be exhausted — checking balance alone used to reset the account to
    # start_balance while real money was still locked in other pending bets (confirmed:
    # a loss that dropped balance to 0 while ~10 other bets were still open, together
    # worth well over 100 ₽, triggered a "bankruptcy" that wasn't one — those bets could
    # still have paid out). Only truly no money anywhere counts as ruined.
    ruined = balance_after <= LIVE_RUIN_THRESHOLD and locked_after <= LIVE_RUIN_THRESHOLD
    ruin_count = acc["ruin_count"]
    is_ruined = 0
    if ruined:
        ruin_count += 1
        balance_after = acc["start_balance"]
        locked_after = 0.0
        is_ruined = 1
    peak = max(acc["peak_balance"], balance_after)

    f_cursor.execute("""
        UPDATE bankroll_accounts SET
            balance = %s, locked = %s, peak_balance = %s, total_returned = total_returned + %s,
            wins = wins + %s, losses = losses + %s, ruin_count = %s, is_ruined = %s, updated_at = %s
        WHERE account = 'live'
    """, (
        balance_after, locked_after, peak, payout,
        int(outcome == "win"), int(outcome == "loss"),
        ruin_count, is_ruined, _now_iso(),
    ))
    f_cursor.execute("""
        INSERT INTO bankroll_ledger
            (account, round_no, balance_before, balance_after, staked, returned, bets_count, ruined, created_at)
        VALUES ('live', NULL, %s, %s, %s, %s, 1, %s, %s)
    """, (balance_before, balance_after, stake, payout, int(ruined), _now_iso()))

    if is_ruined:
        messages.append({
            "category": "BANKROLL", "level": "WARNING",
            "message": (
                f"🏳️ БОТ ОБАНКРОТИЛСЯ (банкротство №{ruin_count}): баланс дошёл до 0, "
                f"автоматически сброшен на {acc['start_balance']:.1f} ₽."
            ),
        })

    return outcome, messages


def _cycle_summary_message(won: int, lost: int, void: int, f_cursor) -> Dict[str, str]:
    f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = 'live'")
    live_acc = f_cursor.fetchone()
    settled = won + lost + void
    return {
        "category": "BANKROLL",
        "level": "WARNING" if lost > won else "INFO",
        "message": (
            f"📊 Итог за цикл: {settled} ставок рассчитано ({won} выигрыш / {lost} проигрыш / {void} аннулировано). "
            f"Баланс: {live_acc['balance']:.1f} ₽ (пик {live_acc['peak_balance']:.1f} ₽, банкротств {live_acc['ruin_count']})."
        ),
    }


def settle_live_bets(timestamp_str: str) -> Dict[str, Any]:
    """
    Resolves any 'open' live_bets whose underlying event has since been archived (i.e.
    finished_bets now has an outcome row for it). Called from save_parsed_events() right
    after archive_finished_events(), so a bet settles in the same scrape cycle its event
    finalizes in — not whenever ai_service's own inference cycle next happens to run.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("SELECT * FROM live_bets WHERE status = 'open'")
    open_bets = f_cursor.fetchall()
    if not open_bets:
        release_connection(f_conn)
        return {"settled": 0, "won": 0, "lost": 0, "void": 0, "messages": []}

    won = lost = void = 0
    messages: List[Dict[str, str]] = []

    for b in open_bets:
        f_cursor.execute("""
            SELECT is_win FROM finished_bets
            WHERE event_id = %s AND factor_id = %s AND COALESCE(parameter,'') = COALESCE(%s,'')
              AND COALESCE(market_prefix,'') = COALESCE(%s,'')
        """, (b["event_id"], b["factor_id"], b["parameter"], b["market_prefix"]))
        row = f_cursor.fetchone()
        if row is None:
            continue  # event hasn't finished (archived) yet

        outcome, msgs = _apply_bet_settlement(f_cursor, b, row["is_win"])
        messages.extend(msgs)
        if outcome == "won":
            won += 1
        elif outcome == "lost":
            lost += 1
        else:
            void += 1

    settled = won + lost + void
    if settled:
        messages.append(_cycle_summary_message(won, lost, void, f_cursor))

    f_conn.commit()
    release_connection(f_conn)
    return {"settled": settled, "won": won, "lost": lost, "void": void, "messages": messages}


def settle_completed_period_bets(timestamp_str: str) -> Dict[str, Any]:
    """
    Settles open live_bets on period-scoped markets (e.g. "1-й тайм", "2-й период") the
    moment their own period ends — without waiting for the whole match to finish.

    Safe because a period is only ever treated as "over" once period_scores has an entry
    for period N+1 — proof the next period has actually started, not just Fonbet
    pre-populating a 0:0 placeholder for it (confirmed: the raw feed shows a 2nd-half
    entry the instant halftime begins, before a single second-half event has happened).
    "An entry exists for period N" alone doesn't mean period N is finished.

    Deliberately does not handle the *last* period of a match — its end is the match's
    end, and the existing short grace period already settles that quickly via
    settle_live_bets() once the event archives.
    """
    conn = get_connection()
    cursor = conn.cursor()
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    f_cursor.execute("SELECT * FROM live_bets WHERE status = 'open'")
    open_bets = f_cursor.fetchall()
    won = lost = void = 0
    messages: List[Dict[str, str]] = []

    for b in open_bets:
        prefix = (b["market_prefix"] or "").strip()
        if not prefix or prefix == MAIN_MARKET_PREFIX:
            continue  # main-match bets settle via the full-archival path instead
        ordinal = _parse_period_ordinal(prefix)
        if ordinal is None:
            continue

        cursor.execute(
            "SELECT score_1, score_2, sport_path, period_scores_json, is_live FROM events WHERE event_id = %s",
            (b["event_id"],),
        )
        ev = cursor.fetchone()
        if ev is None or not ev["is_live"]:
            continue  # event already finished/archived — settle_live_bets handles it

        try:
            period_scores = [tuple(p) for p in json.loads(ev["period_scores_json"] or "[]")]
        except Exception:
            period_scores = []
        if len(period_scores) <= ordinal:
            continue  # next period hasn't started yet — this one isn't confirmed over

        is_win, _is_push = resolve_outcome(
            b["factor_id"], b["label"] or "", b["parameter"], ev["score_1"], ev["score_2"],
            market_prefix=prefix, sport_path=ev["sport_path"] or "", period_scores=period_scores,
        )
        if is_win is None:
            continue  # ungradable or a push (wrong sport type, exact line, etc.) — leave
            # open, full-match path (and its own is_push bookkeeping) will void it anyway

        outcome, msgs = _apply_bet_settlement(f_cursor, b, is_win)
        messages.extend(msgs)
        if outcome == "won":
            won += 1
        elif outcome == "lost":
            lost += 1
        else:
            void += 1

    settled = won + lost + void
    if settled:
        messages.append(_cycle_summary_message(won, lost, void, f_cursor))

    f_conn.commit()
    release_connection(f_conn)
    release_connection(conn)
    return {"settled": settled, "won": won, "lost": lost, "void": void, "messages": messages}



