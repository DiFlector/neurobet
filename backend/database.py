import json
import math
import os
import re
import logging
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Iterable, Tuple, Callable
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from settings import settings

# Docker copies neurobet_filters onto /app; locally it's under repo/shared.
_shared = Path(__file__).resolve().parent.parent / "shared"
if (_shared / "neurobet_filters").is_dir() and str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

from neurobet_filters import (  # noqa: E402
    universe_sql,
    universe_sql_params,
    bet_band_sql,
    MIN_MARKET_SUPPORT,
    is_fast_format_sport_path,
    in_live_stake_sport,
    in_live_stake_market,
    outcome_will_win,
    outcome_will_win_sql,
    passes_live_gates,
)
from neurobet_features import (  # noqa: E402
    pack_timer_entry,
    parse_period_ordinal as _parse_period_ordinal,
    parse_score_diff,
    parse_score_pair,
    parse_score_sum,
    parse_timer,
    parse_ts_epoch as _parse_ts_epoch,
    overround_at_latest,
)

logger = logging.getLogger("database")

_pg_pool = psycopg2.pool.ThreadedConnectionPool(2, 40, dsn=settings.DATABASE_URL)

import threading

class _SafeConn:
    """Обёртка, которая автоматически возвращает соединение в пул при сборке мусора,
    если release_connection не был вызван (защита от утечки при исключениях)."""
    __slots__ = ("_conn", "_released", "_pool", "_tid")

    def __init__(self, conn, pg_pool):
        self._conn = conn
        self._pool = pg_pool
        self._released = False
        self._tid = threading.current_thread().ident

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def _release(self):
        if not self._released:
            self._released = True
            try:
                self._conn.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(self._conn)
            except Exception:
                pass

    def __del__(self):
        self._release()

def get_connection():
    conn = _pg_pool.getconn()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO live, public")
    return _SafeConn(conn, _pg_pool)

def get_finished_connection():
    conn = _pg_pool.getconn()
    conn.cursor_factory = RealDictCursor
    with conn.cursor() as cur:
        cur.execute("SET search_path TO finished, public")
    return _SafeConn(conn, _pg_pool)

def release_connection(conn):
    if isinstance(conn, _SafeConn):
        conn._release()
        return
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        _pg_pool.putconn(conn)
    except Exception:
        pass

from contextlib import contextmanager

DASHBOARD_STATEMENT_TIMEOUT_MS = int(os.getenv("NEUROBET_DASHBOARD_STATEMENT_TIMEOUT_MS", "8000"))


@contextmanager
def db_connection(schema="live"):
    """Контекстный менеджер для безопасного получения/возврата соединения из пула."""
    if schema == "finished":
        conn = get_finished_connection()
    else:
        conn = get_connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


@contextmanager
def dashboard_db(schema: str = "live", timeout_ms: Optional[int] = None):
    """Homepage/API reads: abort if training/backtest IO keeps the query queued.

    Uses SET LOCAL so the timeout cannot leak onto scrape/archive connections
    reused from the same pool.
    """
    ms = DASHBOARD_STATEMENT_TIMEOUT_MS if timeout_ms is None else int(timeout_ms)
    conn = get_finished_connection() if schema == "finished" else get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(max(ms, 0)),))
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        release_connection(conn)

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
MAIN_MATCH_WINNER_FACTORS = (
    FACTORS_W1 | FACTORS_X | FACTORS_W2 | FACTORS_1X | FACTORS_12 | FACTORS_X2
    | FACTORS_FINAL_WIN1 | FACTORS_FINAL_WIN2
)
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


# Fonbet results/v2 event.status — see parser_service.RESULT_STATUS_*.
_RESULT_STATUS_IN_PLAY = 1
_RESULT_STATUS_FINISHED = 2


def _minutes_since(start_raw: Any, timestamp_str: str) -> Optional[float]:
    a = _parse_ts_epoch(start_raw)
    b = _parse_ts_epoch(timestamp_str)
    if a is None or b is None:
        return None
    return max(b - a, 0.0) / 60.0


def _period_looks_finished(sport_path: str, s1: int, s2: int) -> bool:
    """True when this period's point score looks like a completed set/map.

    Incomplete last-set snapshots (8:10 in table tennis, still needing 11 and a
    2-point lead) must not be treated as finals. Sports we don't have a rule for
    return True so we don't strip data we can't judge.
    """
    sport = (sport_path or "").lower()
    try:
        a, b = int(s1), int(s2)
    except Exception:
        return False
    hi, lo = max(a, b), min(a, b)
    if "настольный теннис" in sport:
        return hi >= 11 and hi - lo >= 2
    if "бадминтон" in sport:
        return hi >= 21 and hi - lo >= 2
    if "волейбол" in sport:
        return hi >= 15 and hi - lo >= 2
    if "counter-strike" in sport:
        return hi >= 13
    if "теннис" in sport:
        return (hi >= 6 and hi - lo >= 2) or hi >= 7
    return True


def _drop_incomplete_last_period(sport_path: str, period_scores: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not period_scores:
        return period_scores
    last = period_scores[-1]
    if _period_looks_finished(sport_path, last[0], last[1]):
        return period_scores
    return period_scores[:-1]


def archive_finished_events(
    cursor, timestamp_str: str, official_results: Optional[Dict[int, Dict[str, Any]]] = None,
):
    cursor.execute("SELECT * FROM events WHERE is_live = 0")
    finished = cursor.fetchall()

    if not finished:
        return

    official_results = official_results or {}
    wait_min = float(settings.EVENT_MISS_GRACE_MINUTES + settings.EVENT_RESULTS_WAIT_MINUTES)
    ready: List[Any] = []
    for raw in finished:
        ev = dict(raw)
        eid = ev["event_id"]
        sport_path = ev["sport_path"] or ""
        official = official_results.get(int(eid))
        age = _minutes_since(ev.get("missing_since"), timestamp_str)
        timed_out = age is None or age >= wait_min

        if official and int(official.get("status") or 0) == _RESULT_STATUS_FINISHED:
            s1 = int(official.get("score_1") or 0)
            s2 = int(official.get("score_2") or 0)
            periods = [tuple(p) for p in (official.get("period_scores") or [])]
            ev["score_1"] = s1
            ev["score_2"] = s2
            ev["score"] = f"{s1}:{s2}"
            if periods:
                ev["period_scores_json"] = json.dumps([list(p) for p in periods], ensure_ascii=False)
            cursor.execute(
                """UPDATE events SET score_1 = %s, score_2 = %s, score = %s, period_scores_json = %s
                    WHERE event_id = %s AND is_live = 0""",
                (s1, s2, ev["score"], ev.get("period_scores_json") or "[]", eid),
            )
            logger.info(
                f"Official results for {eid}: {s1}:{s2} periods={periods or 'kept-live'}"
            )
            ready.append(ev)
            continue

        if not timed_out:
            why = "waiting for Fonbet results"
            if official and int(official.get("status") or 0) == _RESULT_STATUS_IN_PLAY:
                why = "Fonbet results still in-play"
            logger.info(f"Delaying archive of {eid}: {why}.")
            continue

        # Timed out (or results never listed this event as finished). Don't grade
        # a frozen last set — drop it so period markets void instead of settling
        # on 8:10 when the set was actually 8:11. Timer often has the real set
        # list when period_scores_json is empty ("за 3 место" Liga Pro).
        try:
            periods = [tuple(p) for p in json.loads(ev["period_scores_json"] or "[]")]
        except Exception:
            periods = []
        try:
            named = {k: tuple(v) for k, v in json.loads(ev.get("named_scores_json") or "{}").items()}
        except Exception:
            named = {}
        periods = _best_period_scores(periods, ev.get("timer"), named, sport_path)
        stripped = _drop_incomplete_last_period(sport_path, periods)
        ev["period_scores_json"] = json.dumps([list(p) for p in stripped], ensure_ascii=False)
        if stripped != periods:
            logger.warning(
                f"Archiving {eid} without a finished official result; "
                f"dropped incomplete last period {periods[-1]} → {stripped}"
            )
        else:
            logger.warning(f"Archiving {eid} without a finished official result after wait.")
        ready.append(ev)

    if not ready:
        return

    logger.info(f"Archiving {len(ready)} finished event(s) into the training archive...")
    archive_started = time.time()
    f_conn = get_finished_connection()
    try:
        f_cursor = f_conn.cursor()

        for ev in ready:
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

            cursor.execute("""
                SELECT id, factor_id,
                       COALESCE(market_prefix, '') AS market_prefix,
                       COALESCE(parameter, '') AS parameter,
                       COALESCE(label, '') AS label,
                       coefficient, score_at_time, timestamp, timer_at_time
                  FROM odds_history
                 WHERE event_id = %s
                 ORDER BY id ASC
            """, (eid,))
            hist_rows = cursor.fetchall()

            latest_by_market: Dict[Tuple[int, str, str], float] = {}
            overround_by_id: Dict[Any, Optional[float]] = {}
            grouped: Dict[Tuple[Any, str, str], List[Any]] = {}
            for r in hist_rows:
                fid = r["factor_id"]
                prefix = r["market_prefix"] or ""
                param = r["parameter"] or ""
                coeff = r["coefficient"]
                if coeff and float(coeff) > 1.0:
                    latest_by_market[(int(fid), param, prefix)] = float(coeff)
                overround_by_id[r["id"]] = overround_at_latest(
                    latest_by_market, int(fid), param, prefix,
                )
                grouped.setdefault((fid, prefix, param), []).append(r)

            cursor.execute(
                """SELECT factor_id, COALESCE(CAST(parameter AS TEXT), '') AS parameter,
                          COALESCE(market_prefix, '') AS market_prefix,
                          win_probability, predicted_win
                     FROM ai_predictions WHERE event_id = %s""",
                (eid,),
            )
            pred_by_key = {
                (p["factor_id"], p["parameter"] or "", p["market_prefix"] or ""): p
                for p in cursor.fetchall()
            }

            for (fid, prefix, param), seq_rows in grouped.items():
                first_row = seq_rows[0]
                last_row = seq_rows[-1]
                odds_seq = [r["coefficient"] for r in seq_rows]
                ts_seq = [_parse_ts_epoch(r["timestamp"]) for r in seq_rows]
                timer_seq = []
                for r in seq_rows:
                    parsed = parse_timer(r["timer_at_time"])
                    timer_seq.append(pack_timer_entry(
                        parsed.match_time_seconds, parsed.set_point_diff,
                    ))
                overround_seq = [overround_by_id[r["id"]] for r in seq_rows]
                score_diff_seq = [parse_score_diff(r["score_at_time"]) for r in seq_rows]
                score_sum_seq = [parse_score_sum(r["score_at_time"]) for r in seq_rows]
                score_diff_at_bet = score_diff_seq[0] if score_diff_seq else 0
                label = next((r["label"] for r in seq_rows if r["label"]), "") or ""

                if is_fast_format_sport_path(sport_path):
                    # Frozen mid-sim score is not a final. Grading Under 126.5 as a win
                    # on 4:4 while Fonbet's coupon was 69:59 (Dallas–NY 2K, 2026-08-18)
                    # poisons both the live bankroll and finished_bets training labels.
                    is_win, is_push = None, False
                else:
                    is_win, is_push = resolve_outcome(
                        fid, label, param, s1, s2,
                        market_prefix=prefix, sport_path=sport_path, period_scores=period_scores,
                        named_scores=named_scores,
                    )

                pred_row = pred_by_key.get((fid, param, prefix))
                predicted_win_probability = pred_row["win_probability"] if pred_row else None
                predicted_win = pred_row["predicted_win"] if pred_row else None
                overround_close = overround_seq[-1] if overround_seq else None

                f_cursor.execute("""
                    INSERT INTO finished_bets (
                        event_id, factor_id, market_prefix, label, parameter,
                        initial_coefficient, final_coefficient, min_coefficient, max_coefficient,
                        samples_count, odds_seq_json, score_at_time, is_win, first_seen_at, finished_at,
                        predicted_win_probability, score_seq_json, score_sum_seq_json, score_diff_at_bet,
                        trained_count, is_push,
                        predicted_win, overround_close, ts_seq_json, timer_seq_json, overround_seq_json
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s, %s
                    )
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
                        predicted_win = COALESCE(excluded.predicted_win, finished_bets.predicted_win),
                        score_seq_json = excluded.score_seq_json,
                        score_sum_seq_json = excluded.score_sum_seq_json,
                        score_diff_at_bet = excluded.score_diff_at_bet,
                        overround_close = COALESCE(excluded.overround_close, finished_bets.overround_close),
                        ts_seq_json = excluded.ts_seq_json,
                        timer_seq_json = excluded.timer_seq_json,
                        overround_seq_json = excluded.overround_seq_json,
                        trained_count = 0;
                """, (
                    eid, fid, prefix, label, param,
                    first_row["coefficient"], last_row["coefficient"],
                    min(odds_seq) if odds_seq else None,
                    max(odds_seq) if odds_seq else None,
                    len(seq_rows), json.dumps(odds_seq, ensure_ascii=False), last_row["score_at_time"],
                    is_win, first_row["timestamp"], timestamp_str, predicted_win_probability,
                    json.dumps(score_diff_seq, ensure_ascii=False),
                    json.dumps(score_sum_seq, ensure_ascii=False), score_diff_at_bet,
                    0, int(is_push),
                    predicted_win, overround_close, json.dumps(ts_seq), json.dumps(timer_seq),
                    json.dumps(overround_seq),
                ))

            cursor.execute("DELETE FROM latest_odds WHERE event_id = %s", (eid,))
            cursor.execute("DELETE FROM odds_history WHERE event_id = %s", (eid,))
            cursor.execute("DELETE FROM events WHERE event_id = %s", (eid,))
            cursor.execute("DELETE FROM ai_predictions WHERE event_id = %s", (eid,))

        f_conn.commit()
        logger.info(
            f"Archived {len(ready)} event(s) in {time.time() - archive_started:.1f}s"
        )
    except Exception:
        try:
            f_conn.rollback()
        except Exception:
            pass
        raise
    finally:
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

        # Grace-period counters track disappearance from the *catalog* (present_ids),
        # not "had zero odds this poll". A line recalc pulls markets for a minute while
        # the event stays in seen_live_ids — that must not finalize the match or settle
        # open bot bets on an interim score.
        upserted_ids = [int(e["event_id"]) for e in parsed_events] or [-1]
        cursor.execute(
            """UPDATE events
                   SET miss_count = 0, missing_since = NULL
                 WHERE event_id = ANY(%s)
                   AND (miss_count > 0 OR missing_since IS NOT NULL)""",
            (id_list,),
        )
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

        # Catalog-present, zero-odds stuck entries (finished match still listed, or
        # Liga Pro "за 3 место"). Use a *long* no-upsert window — not EVENT_MISS_GRACE
        # (1 min), which falsely finalized live football during a line recalc.
        cursor.execute(
            """UPDATE events
                  SET is_live = 0,
                      missing_since = COALESCE(missing_since, last_updated_at)
                WHERE is_live = 1
                  AND event_id = ANY(%s)
                  AND NOT (event_id = ANY(%s))
                  AND last_updated_at IS DISTINCT FROM %s
                  AND EXTRACT(EPOCH FROM (%s::timestamptz - last_updated_at::timestamptz)) / 60.0 >= %s""",
            (
                id_list,
                upserted_ids,
                timestamp_str,
                timestamp_str,
                settings.EVENT_CATALOG_NO_ODDS_FINISH_MINUTES,
            ),
        )
        if cursor.rowcount:
            logger.info(
                f"Finalized {cursor.rowcount} catalog-present event(s) with no odds "
                f"for {settings.EVENT_CATALOG_NO_ODDS_FINISH_MINUTES}+ min."
            )

    # Live snapshot is committed first. Archiving finished events (copying odds
    # trajectories into finished_bets) can take minutes on a large batch and used
    # to sit *before* this commit — so last_updated_at froze and the AI trigger
    # never fired for the whole archive. archive_and_settle() runs after the
    # caller has already kicked inference.

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
            timer_str = ev.get("timer", "")

            # Save historical record
            cursor.execute("""
                INSERT INTO odds_history (
                    event_id, factor_id, market_prefix, label, parameter, coefficient, score_at_time, timestamp, timer_at_time
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (eid, fid, prefix, label, param, coeff, score_str, timestamp_str, timer_str))

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
    return {"events_saved": len(parsed_events)}


def archive_and_settle(
    timestamp_str: str,
    official_results: Optional[Dict[int, Dict[str, Any]]] = None,
    results_fetcher: Optional[Callable[[str], Dict[int, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Copy finalized (is_live=0) events into the training archive and settle
    open live_bets. Runs *after* save_parsed_events has already committed the
    live snapshot so a slow archive cannot freeze last_updated_at or delay
    the AI trigger. Safe to retry: archived events are deleted from live, so
    a second call in the same cycle is a no-op once the first commit lands.

    official_results overlays Fonbet's results-page finals (set scores included)
    before grading — the live feed often drops the event one point too early.
    If omitted, results_fetcher(timestamp_str) is called only when at least one
    event is waiting to archive.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if official_results is None and results_fetcher is not None:
            cursor.execute("SELECT 1 FROM events WHERE is_live = 0 LIMIT 1")
            if cursor.fetchone():
                try:
                    official_results = results_fetcher(timestamp_str)
                except Exception as e:
                    logger.warning(f"Fonbet results fetch skipped: {e}")
                    official_results = None
        archive_finished_events(cursor, timestamp_str, official_results=official_results)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        # Archive bugs used to abort the whole function, so open live_bets never
        # settled even when the live row was already is_live=0 and gradable.
        logger.exception("Error archiving finished events (will retry next cycle)")
    finally:
        release_connection(conn)

    settle_result = settle_live_bets(timestamp_str)

    # Period-scoped bets ("1-й тайм", "2-й период", ...) settle once period_scores
    # has the next period — not waiting for the whole match. Wrapped separately:
    # this opens its own connections and must not take down the scrape cycle.
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


DB_STATS_CACHE_SECONDS = float(os.getenv("NEUROBET_DB_STATS_CACHE_SECONDS", "30"))
_db_stats_core_cache: Dict[str, Any] = {"loaded_at": 0.0, "data": None}
_db_stats_refresh_lock = threading.Lock()
_db_stats_refreshing = False


def _approx_pg_row_count(cursor, schema: str, table: str) -> int:
    """Fast row estimate from pg_class — exact COUNT(*) on million-row tables is too slow for polling."""
    cursor.execute(
        """
        SELECT COALESCE(GREATEST(c.reltuples, 0), 0)::bigint AS c
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
        """,
        (schema, table),
    )
    row = cursor.fetchone()
    est = int(row["c"] or 0) if row else 0
    if est > 0:
        return est
    cursor.execute(f"SELECT COUNT(*) AS c FROM {schema}.{table}")
    return int(cursor.fetchone()["c"] or 0)


def _compute_db_stats_core() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS c FROM events WHERE is_live = 1")
    live_count = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM events")
    total_events = cursor.fetchone()["c"]

    history_count = _approx_pg_row_count(cursor, "live", "odds_history")

    cursor.execute("SELECT COUNT(*) AS c FROM ai_predictions")
    predictions_count = cursor.fetchone()["c"]

    cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
    last_updated = cursor.fetchone()["max"]

    cursor.execute("SELECT pg_database_size(current_database()) AS size")
    total_db_size_bytes = cursor.fetchone()["size"] or 0

    release_connection(conn)

    finished_count = 0
    finished_history_count = 0
    unresolved_bets_count = 0
    try:
        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        finished_count = _approx_pg_row_count(f_cursor, "finished", "finished_events")
        finished_history_count = _approx_pg_row_count(f_cursor, "finished", "finished_bets")
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
        "last_updated_at": last_updated,
        "db_size_bytes": total_db_size_bytes,
        "db_size_formatted": format_file_size(total_db_size_bytes),
    }


def _refresh_db_stats_core_cache() -> None:
    global _db_stats_refreshing, _db_stats_core_cache
    try:
        _db_stats_core_cache = {
            "loaded_at": time.time(),
            "data": _compute_db_stats_core(),
        }
    except Exception as e:
        logger.error(f"DB stats background refresh failed: {e}")
    finally:
        with _db_stats_refresh_lock:
            _db_stats_refreshing = False


def get_db_stats(*, include_guess_rate: bool = True) -> Dict[str, Any]:
    global _db_stats_refreshing
    now = time.time()
    loaded_at = float(_db_stats_core_cache.get("loaded_at") or 0.0)
    core = _db_stats_core_cache.get("data")
    age = now - loaded_at if loaded_at > 0 else float("inf")

    if core is not None and age < DB_STATS_CACHE_SECONDS:
        result = dict(core)
    elif core is not None and loaded_at > 0:
        result = dict(core)
        with _db_stats_refresh_lock:
            if not _db_stats_refreshing:
                _db_stats_refreshing = True
                threading.Thread(
                    target=_refresh_db_stats_core_cache,
                    daemon=True,
                    name="db-stats-refresh",
                ).start()
    else:
        _db_stats_core_cache["data"] = _compute_db_stats_core()
        _db_stats_core_cache["loaded_at"] = now
        result = dict(_db_stats_core_cache["data"])

    if include_guess_rate:
        guess_rate_pct, miss_rate_pct = get_headline_guess_rate()
        result["guess_rate_pct"] = guess_rate_pct
        result["miss_rate_pct"] = miss_rate_pct
    else:
        result["guess_rate_pct"] = None
        result["miss_rate_pct"] = None

    return result


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

# Calibration (Bayesian shrinkage of the raw model probability towards the model's real
# historical win rate at that confidence level) now happens once, in ai_service, before a
# prediction is even saved — see ai_service/app/neuralbet/calibration.py. win_probability
# read from ai_predictions/finished_bets below is already calibrated; recalibrating it a
# second time here used to let this page's numbers drift from what the bot actually acted
# on (it always bet on the raw score) — this file no longer computes calibration at all.

# Same live gates as ai_service (coeff band, min EV, min market support,
# NEURALBET_LIVE_STAKE_SPORTS / NEURALBET_LIVE_STAKE_MARKETS) — sourced from
# shared/neurobet_filters so «Активные LIVE Прогнозы» and «Ставки нейросети»
# match what the bot would actually risk money on. Refresh interval stays local:
# it's a cache TTL, not a filter.
NEUROBET_MARKET_SUPPORT_REFRESH_SECONDS = float(os.getenv("NEURALBET_MARKET_SUPPORT_REFRESH_SECONDS", "300"))
_neurobet_market_support: Dict[Tuple[str, int, str], int] = {}
_neurobet_market_support_loaded_at = 0.0
_neurobet_market_support_refresh_lock = threading.Lock()
_neurobet_market_support_refreshing = False

# Recency weighting for get_db_stats()'s guess_rate_pct (the "Точность модели" ring on
# the main page): a bet from GUESS_RATE_HALF_LIFE_HOURS ago counts half as much as one
# settled just now, one from twice that long ago a quarter as much, and so on — a
# continuous exponential decay, not a hard cutoff, so the number never jumps when a bet
# happens to cross some fixed day boundary. Was previously an unweighted all-time
# average, which meant the ring barely moved even after a real model improvement — a
# handful of better predictions today drowns in a few hundred thousand historical ones.
# 24h half-life means "mostly today's results, but still smoothed by a bit of
# yesterday's" instead of a sharp day-over-day swing. The SQL query is still bounded to
# GUESS_RATE_LOOKBACK_DAYS (not the whole archive) purely so it doesn't have to scan
# every row ever recorded — bets older than that already carry a practically-zero weight
# (0.5^(7*24/24) is far below floating-point-meaningful) so excluding them changes
# nothing about the result, only the query cost.
GUESS_RATE_HALF_LIFE_HOURS = float(os.getenv("NEUROBET_GUESS_RATE_HALF_LIFE_HOURS", "24.0"))
GUESS_RATE_LOOKBACK_DAYS = int(os.getenv("NEUROBET_GUESS_RATE_LOOKBACK_DAYS", "7"))

MOSCOW_TZ = timezone(timedelta(hours=3))


def _now_moscow_naive() -> datetime:
    """Moscow wall-clock time with no tzinfo — matches the format finished_at is stored
    in (a TEXT "YYYY-MM-DD HH:MM:SS" written from Moscow-local now(), see
    backend/main.py's now_moscow/now_str), so subtracting a parsed finished_at from this
    gives the correct elapsed wall-clock time without any timezone-offset ambiguity."""
    return datetime.now(MOSCOW_TZ).replace(tzinfo=None)


def _parse_finished_at_naive(raw: Any) -> Optional[datetime]:
    """Parse finished_at into Moscow-naive datetime for recency weighting."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        s = str(raw).strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(MOSCOW_TZ).replace(tzinfo=None)
    return dt


HEADLINE_GUESS_RATE_CACHE_SECONDS = float(
    os.getenv("NEUROBET_HEADLINE_GUESS_RATE_CACHE_SECONDS", "300")
)
_headline_guess_rate_cache: Dict[str, Any] = {
    "loaded_at": 0.0,
    "guess_rate_pct": None,
    "miss_rate_pct": None,
}
_headline_guess_rate_refresh_lock = threading.Lock()
_headline_guess_rate_refreshing = False
_HEADLINE_GUESS_RATE_CACHE_PATH = os.path.join(
    os.getenv("MODEL_DIR", "/app/data/models"), "headline_guess_rate.json"
)


def _load_headline_cache_from_disk() -> None:
    global _headline_guess_rate_cache
    try:
        with open(_HEADLINE_GUESS_RATE_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("loaded_at"), (int, float)) and data.get("guess_rate_pct") is not None:
            _headline_guess_rate_cache = data
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Could not load headline guess-rate cache: {e}")


def _save_headline_cache_to_disk() -> None:
    try:
        os.makedirs(os.path.dirname(_HEADLINE_GUESS_RATE_CACHE_PATH), exist_ok=True)
        with open(_HEADLINE_GUESS_RATE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_headline_guess_rate_cache, f)
    except Exception as e:
        logger.warning(f"Could not persist headline guess-rate cache: {e}")


_load_headline_cache_from_disk()


def _compute_headline_guess_rate() -> Tuple[Optional[float], Optional[float]]:
    """Recency-weighted guess rate for the main-page «Точность модели» ring."""
    try:
        now_naive = _now_moscow_naive()
        now_str = now_naive.strftime("%Y-%m-%d %H:%M:%S")
        recency_cutoff = (now_naive - timedelta(days=GUESS_RATE_LOOKBACK_DAYS)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        band_sql, band_params = bet_band_sql(
            "h.final_coefficient",
            "((h.predicted_win_probability / 100.0) * h.final_coefficient - 1.0) * 100.0",
            p_expr="h.predicted_win_probability",
        )
        sports, factors = universe_sql_params()

        support = _get_market_support()
        allowed_sports: List[str] = []
        allowed_factors: List[int] = []
        allowed_labels: List[str] = []
        if support:
            for (sport, factor_id, label), cnt in support.items():
                if cnt >= MIN_MARKET_SUPPORT:
                    allowed_sports.append(sport)
                    allowed_factors.append(factor_id)
                    allowed_labels.append(label or "")

        support_join = ""
        support_params: List[Any] = []
        if allowed_sports:
            support_join = """
              INNER JOIN (
                SELECT * FROM unnest(%s::text[], %s::int[], %s::text[])
                  AS sup(sport, factor_id, label)
              ) sup ON sup.sport = TRIM(SPLIT_PART(f.sport_path, '/', 1))
                   AND sup.factor_id = h.factor_id
                   AND sup.label = COALESCE(h.label, '')
            """
            support_params = [allowed_sports, allowed_factors, allowed_labels]

        f_conn = get_finished_connection()
        f_cursor = f_conn.cursor()
        f_cursor.execute(
            f"""
            SELECT
                COALESCE(SUM(w), 0) AS weighted_total,
                COALESCE(SUM(CASE WHEN {outcome_will_win_sql(
                    "predicted_win", "final_coefficient",
                    factor_expr="factor_id",
                    score1_expr="score_1",
                    score2_expr="score_2",
                    p_expr="predicted_win_probability",
                )} = is_win THEN w ELSE 0 END), 0) AS weighted_correct
              FROM (
                SELECT h.predicted_win, h.is_win, h.final_coefficient,
                       h.factor_id, f.score_1, f.score_2, h.predicted_win_probability,
                       POWER(0.5, GREATEST(
                           EXTRACT(EPOCH FROM (%s::timestamp - h.finished_at::timestamp)) / 3600.0,
                           0
                       ) / %s) AS w
                  FROM finished_bets h
                  JOIN finished_events f ON h.event_id = f.event_id
                  {support_join}
                 WHERE h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL
                   AND h.predicted_win_probability IS NOT NULL
                   AND h.final_coefficient IS NOT NULL AND h.final_coefficient > 1.0
                   {universe_sql("f", "h")}
                   {band_sql}
                   AND h.finished_at >= %s
              ) weighted
            """,
            (now_str, GUESS_RATE_HALF_LIFE_HOURS, *support_params, sports, factors, *band_params, recency_cutoff),
        )
        row = f_cursor.fetchone()
        release_connection(f_conn)

        weighted_total = float(row["weighted_total"] or 0.0)
        weighted_correct = float(row["weighted_correct"] or 0.0)
        if weighted_total <= 0:
            return None, None

        guess_rate_pct = round(weighted_correct / weighted_total * 100.0, 1)
        return guess_rate_pct, round(100.0 - guess_rate_pct, 1)
    except Exception as e:
        logger.error(f"Error computing headline guess rate: {e}")
        return None, None


def _refresh_headline_guess_rate_cache() -> None:
    global _headline_guess_rate_refreshing, _headline_guess_rate_cache
    try:
        guess_rate_pct, miss_rate_pct = _compute_headline_guess_rate()
        _headline_guess_rate_cache = {
            "loaded_at": time.time(),
            "guess_rate_pct": guess_rate_pct,
            "miss_rate_pct": miss_rate_pct,
        }
        _save_headline_cache_to_disk()
    except Exception as e:
        logger.error(f"Headline guess-rate background refresh failed: {e}")
    finally:
        with _headline_guess_rate_refresh_lock:
            _headline_guess_rate_refreshing = False


def get_headline_guess_rate() -> Tuple[Optional[float], Optional[float]]:
    """Cached headline guess/miss % — shared by /api/stats and /api/neurobets/headline-accuracy."""
    global _headline_guess_rate_refreshing, _headline_guess_rate_cache
    now = time.time()
    loaded_at = float(_headline_guess_rate_cache.get("loaded_at") or 0.0)
    guess_rate_pct = _headline_guess_rate_cache.get("guess_rate_pct")
    miss_rate_pct = _headline_guess_rate_cache.get("miss_rate_pct")
    age = now - loaded_at if loaded_at > 0 else float("inf")

    if age < HEADLINE_GUESS_RATE_CACHE_SECONDS:
        return guess_rate_pct, miss_rate_pct

    # Stale but usable — return immediately, recompute in the background.
    if loaded_at > 0 and guess_rate_pct is not None:
        with _headline_guess_rate_refresh_lock:
            if not _headline_guess_rate_refreshing:
                _headline_guess_rate_refreshing = True
                threading.Thread(
                    target=_refresh_headline_guess_rate_cache,
                    daemon=True,
                    name="headline-guess-rate-refresh",
                ).start()
        return guess_rate_pct, miss_rate_pct

    guess_rate_pct, miss_rate_pct = _compute_headline_guess_rate()
    _headline_guess_rate_cache = {
        "loaded_at": now,
        "guess_rate_pct": guess_rate_pct,
        "miss_rate_pct": miss_rate_pct,
    }
    _save_headline_cache_to_disk()
    return guess_rate_pct, miss_rate_pct


def warm_neurobet_caches() -> None:
    """Pre-warm DB counters, market-support, and headline guess rate for instant first paint."""
    def _run() -> None:
        global _headline_guess_rate_cache, _db_stats_core_cache
        try:
            _db_stats_core_cache = {
                "loaded_at": time.time(),
                "data": _compute_db_stats_core(),
            }
            _refresh_market_support_cache()
            guess_rate_pct, miss_rate_pct = _compute_headline_guess_rate()
            _headline_guess_rate_cache = {
                "loaded_at": time.time(),
                "guess_rate_pct": guess_rate_pct,
                "miss_rate_pct": miss_rate_pct,
            }
            _save_headline_cache_to_disk()
            get_neurobets_history_summary()
        except Exception as e:
            logger.warning(f"NeuroBet cache pre-warm failed: {e}")

    threading.Thread(target=_run, daemon=True, name="neurobet-cache-warm").start()


def warm_headline_guess_rate_cache() -> None:
    """Backward-compatible alias for startup hook."""
    warm_neurobet_caches()


def _compute_market_support() -> Dict[Tuple[str, int, str], int]:
    f_conn = get_finished_connection()
    try:
        f_cursor = f_conn.cursor()
        f_cursor.execute("""
            SELECT TRIM(SPLIT_PART(f.sport_path, '/', 1)) AS sport, h.factor_id, h.label, COUNT(*) AS c
              FROM finished_bets h
              JOIN finished_events f ON h.event_id = f.event_id
             WHERE h.is_win IS NOT NULL
             GROUP BY 1, 2, 3
        """)
        return {(r["sport"] or "", r["factor_id"], r["label"] or ""): r["c"] for r in f_cursor.fetchall()}
    finally:
        release_connection(f_conn)


def _refresh_market_support_cache() -> None:
    global _neurobet_market_support, _neurobet_market_support_loaded_at, _neurobet_market_support_refreshing
    try:
        _neurobet_market_support = _compute_market_support()
        _neurobet_market_support_loaded_at = time.time()
    except Exception as e:
        logger.error(f"Error refreshing neurobet market support counts: {e}")
    finally:
        with _neurobet_market_support_refresh_lock:
            _neurobet_market_support_refreshing = False


def _get_market_support() -> Dict[Tuple[str, int, str], int]:
    """
    {(top_level_sport, factor_id, label): resolved_count} over the whole finished-bets
    archive, cached for NEUROBET_MARKET_SUPPORT_REFRESH_SECONDS — mirrors ai_service's
    own _refresh_market_support exactly (same grouping, same cache shape) so both
    services agree on which markets count as "thin." Cached rather than queried live on
    every request: get_top_neurobets is polled every ~10s by the frontend, and this
    aggregates over the full finished_bets table (450k+ rows and growing). On query
    failure the stale cache (or an empty dict = fail-open, matching ai_service) is kept
    rather than raising — a DB hiccup here shouldn't take the whole prediction list down.
    """
    global _neurobet_market_support, _neurobet_market_support_loaded_at, _neurobet_market_support_refreshing
    now = time.time()
    age = now - _neurobet_market_support_loaded_at if _neurobet_market_support_loaded_at else float("inf")
    if _neurobet_market_support and age < NEUROBET_MARKET_SUPPORT_REFRESH_SECONDS:
        return _neurobet_market_support
    with _neurobet_market_support_refresh_lock:
        if not _neurobet_market_support_refreshing:
            _neurobet_market_support_refreshing = True
            threading.Thread(
                target=_refresh_market_support_cache,
                daemon=True,
                name="market-support-refresh",
            ).start()
    return _neurobet_market_support


_LIVE_SCORE_KEYS = ("score_1", "score_2", "period_scores_json", "named_scores_json")


def _live_pick_currently_winning(row: Dict[str, Any]) -> bool:
    """True if this selection is already in the money on the current score.

    ``predicted_win = 0`` means skip / no +EV, not "will lose". A П1 at 1:0
    in minute 84 is winning on the scoreboard and must not appear in the
    loss list just because 1.02 odds leave no edge.
    """
    try:
        s1 = row.get("score_1")
        s2 = row.get("score_2")
        if s1 is None or s2 is None:
            s1, s2 = parse_score_pair(row.get("score"))
        else:
            s1, s2 = int(s1), int(s2)
    except (TypeError, ValueError):
        s1, s2 = parse_score_pair(row.get("score"))
    is_win, _is_push = resolve_outcome(
        row.get("factor_id"),
        row.get("label") or "",
        row.get("parameter") or "",
        s1,
        s2,
        market_prefix=row.get("market_prefix") or "",
        sport_path=row.get("sport_path") or "",
        period_scores=_parse_period_scores_json(row.get("period_scores_json")),
        named_scores=_parse_named_scores_json(row.get("named_scores_json")),
    )
    return is_win == 1


def get_top_neurobets(
    sport_filter: Optional[str] = None,
    sort_mode: str = "best",
    limit: int = 50,
    offset: int = 0,
    verdict: str = "win",
    search: Optional[str] = None,
) -> Dict[str, Any]:
    # INNER JOIN ai_predictions (not LEFT JOIN + a 1/coefficient formula fallback) — this
    # list must reflect only what the trained model actually evaluated, the same as real
    # bet placement does (ai_service/app/neuralbet/pipeline.py never uses a heuristic
    # fallback either). A market the model hasn't scored yet just doesn't appear here
    # rather than showing a guess dressed up as a prediction.
    # verdict: "win" is the bot's real betting pool (predicted_win=1, +EV). "loss" is
    # skip / no +EV (predicted_win=0) — NOT "the model thinks this will lose", and
    # selections already winning on the current score are dropped so a П1 at 1:0
    # does not show up as a "loss". "all" is both. Unscored markets (predicted_win
    # IS NULL) are excluded in every case.
    # This list used to be everything above a fixed min_confidence% cutoff; now the model
    # decides bet/no-bet itself, so this is a verdict list, not a ranked-by-EV top-N.
    # win_probability is already calibrated (ai_service calibrates before saving — see
    # ai_service/app/neuralbet/calibration.py) so it's read here as-is, no second pass.
    # l.updated_at = e.last_updated_at AND e.last_updated_at = MAX(...) is the same
    # staleness guard used everywhere else (get_live_matches, pipeline.py, bet
    # placement) — without it a market Fonbet has since pulled or replaced could still
    # show its last frozen prediction here.
    if verdict == "loss":
        verdict_clause = "AND p.predicted_win = 0"
    elif verdict == "all":
        verdict_clause = "AND p.predicted_win IS NOT NULL"
    else:
        verdict_clause = "AND p.predicted_win = 1"

    query = f"""
        SELECT
            e.event_id, e.sport_path, e.match_name, e.team_1, e.team_2, e.score, e.timer,
            e.score_1, e.score_2, e.period_scores_json, e.named_scores_json,
            l.factor_id, l.market_prefix, l.label, l.parameter, l.coefficient,
            COALESCE(l.initial_coefficient, l.coefficient) AS initial_coefficient,
            p.win_probability AS win_probability,
            p.error_rate AS error_rate,
            p.expected_roi AS expected_roi,
            p.lightgbm_score AS lightgbm_score,
            p.pytorch_score AS pytorch_score,
            p.predicted_win AS predicted_win,
            p.decision_confidence AS decision_confidence
        FROM latest_odds l
        JOIN events e ON l.event_id = e.event_id
        JOIN ai_predictions p ON l.event_id = p.event_id
            AND l.factor_id = p.factor_id
            AND COALESCE(CAST(l.parameter AS TEXT), '') = COALESCE(CAST(p.parameter AS TEXT), '')
            AND COALESCE(l.market_prefix, '') = COALESCE(p.market_prefix, '')
        WHERE e.is_live = 1
          {verdict_clause}
          AND l.updated_at = e.last_updated_at
          AND e.last_updated_at = (SELECT MAX(last_updated_at) FROM events)
          {universe_sql("e", "l")}
    """
    sports, factors = universe_sql_params()
    params = [sports, factors]

    # Coefficient cap + minimum-EV floor — only for the "win" list (the bot's real
    # betting pool): "loss"/"all" exist to show what the network rejects or the full
    # unfiltered board, and forcing the same caps there would just hide information a
    # human might want to see, not match anything the bot actually risks money on.
    if verdict == "win":
        band_sql, band_params = bet_band_sql(
            "l.coefficient", "p.expected_roi", p_expr="p.win_probability",
        )
        query += band_sql
        params.extend(band_params)

    if sport_filter and sport_filter.lower() != "all":
        query += " AND e.sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    # Free-text search across match/teams/bet-type — every whitespace-separated word must
    # appear somewhere in the combined haystack (order-independent AND), so "Команда Фора 1"
    # or "команда - команда п1" both work regardless of which field(s) actually matched.
    if search:
        words = [w for w in re.split(r"\s+", search.strip()) if re.search(r"\w", w)]
        if words:
            query += """ AND (
                COALESCE(e.match_name, '') || ' ' || COALESCE(e.team_1, '') || ' ' || COALESCE(e.team_2, '') || ' ' ||
                COALESCE(l.label, '') || ' ' || COALESCE(CAST(l.parameter AS TEXT), '') || ' ' || COALESCE(l.market_prefix, '')
            ) ILIKE ALL(%s)"""
            params.append([f"%{w}%" for w in words])

    # No ORDER BY / LIMIT here — de-dup below needs to see everything at once to keep the
    # strongest pick per mutually-exclusive market group; sorted in Python instead.
    query += " LIMIT 5000"

    with dashboard_db("live") as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

    candidates = [dict(r) for r in rows if r.get("win_probability") is not None]

    # Thin-market filter — applies regardless of verdict (unlike the coeff/EV gates
    # above): a market with only a handful of resolved outcomes ever isn't reliably
    # calibrated whichever way the verdict points, so showing it as either a confident
    # "win" or "loss" pick would be showing noise dressed up as a signal. Fails open
    # (empty cache = no filtering) rather than hiding everything on a cache miss.
    support = _get_market_support()
    if support:
        candidates = [
            c for c in candidates
            if support.get(
                ((c.get("sport_path") or "").split("/")[0].strip(), c["factor_id"], c.get("label") or ""), 0,
            ) >= MIN_MARKET_SUPPORT
        ]

    for c in candidates:
        c["will_win"] = outcome_will_win(
            c.get("predicted_win"),
            c.get("coefficient"),
            factor_id=c.get("factor_id"),
            score_1=c.get("score_1"),
            score_2=c.get("score_2"),
            win_probability=c.get("win_probability"),
        )

    # Skip-list (verdict=loss) is will_win=0, not "no +EV". Drop 1X2 leaders
    # and shorts; keep currently-winning totals out of the loss list too.
    if verdict == "loss":
        candidates = [
            c for c in candidates
            if c.get("will_win") == 0 and not _live_pick_currently_winning(c)
        ]

    # Stake pool (verdict=win): will_win=1 AND live gates (band / EV / sport / market).
    if verdict == "win":
        candidates = [
            c for c in candidates
            if c.get("will_win") == 1
            and in_live_stake_sport(c.get("sport_path"))
            and in_live_stake_market(factor_id=c.get("factor_id"))
            and passes_live_gates(
                float(c.get("coefficient") or 0),
                float(c.get("expected_roi") or 0),
                sport_path=c.get("sport_path"),
                factor_id=c.get("factor_id"),
                win_probability=c.get("win_probability"),
                will_win=c.get("will_win"),
            )
        ]

    if sort_mode == "best":
        candidates.sort(key=lambda d: (d["expected_roi"], d["win_probability"]), reverse=True)
    else:
        candidates.sort(key=lambda d: (d["win_probability"], -d["coefficient"]), reverse=True)

    # De-duplicate to at most one pick per event — not just per market — so the "win"
    # list (the bot's real betting pool) never recommends two bets on the same match,
    # even ones that aren't strictly mutually exclusive (e.g. "П1 wins" and "team 2's
    # individual total over 2.5" are both individually plausible but pull against each
    # other; see place_live_bet_candidates' occupied_events for the full reasoning —
    # this list should show exactly what the bot would actually do). Rows arrive
    # already sorted best-first, so keeping the first row seen per event keeps the
    # strongest pick and discards the rest. Only applied to "win": "loss"/"all" exist to
    # browse everything the network has an opinion on, and collapsing those to one row
    # per match would just hide information no money is ever at risk on anyway.
    if verdict == "win":
        seen_events = set()
        deduped = []
        for d in candidates:
            if d["event_id"] in seen_events:
                continue
            seen_events.add(d["event_id"])
            deduped.append(d)
    else:
        deduped = candidates

    total = len(deduped)
    page = deduped[offset: offset + limit] if limit else deduped[offset:]
    for item in page:
        w = item.get("will_win")
        item["will_win"] = None if w is None else int(w)
        for k in _LIVE_SCORE_KEYS:
            item.pop(k, None)

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

def _live_pool_sql(event_alias: str, bet_alias: str, coeff_expr: str) -> Tuple[str, list]:
    """Universe + coefficient band + min-EV — the same live-pool SQL get_db_stats and
    get_top_neurobets(verdict=win) already apply, so /stats never surfaces sports,
    markets, or odds the bot would not stake."""
    band_sql, band_params = bet_band_sql(
        coeff_expr,
        f"(({bet_alias}.predicted_win_probability / 100.0) * {coeff_expr} - 1.0) * 100.0",
        p_expr=f"{bet_alias}.predicted_win_probability",
    )
    sports, factors = universe_sql_params()
    sql = (
        f" AND {bet_alias}.predicted_win_probability IS NOT NULL"
        f" AND {coeff_expr} IS NOT NULL AND {coeff_expr} > 1.0"
        + universe_sql(event_alias, bet_alias)
        + band_sql
    )
    return sql, [sports, factors, *band_params]


def _filter_by_market_support(rows: list, sport_key: str = "sport") -> list:
    support = _get_market_support()
    if not support:
        return rows
    return [
        r for r in rows
        if support.get((r[sport_key] or "", r["factor_id"], r["label"] or ""), 0) >= MIN_MARKET_SUPPORT
    ]


def get_bet_type_stats() -> Dict[str, Any]:
    """
    Guess-rate breakdown by sport and bet type — same "guessed" definition as
    get_neurobets_history/get_db_stats (outcome_will_win vs is_win on resolved,
    model-scored bets: coeff below MIN_BET_COEFF is a win call, not a skip/loss),
    just grouped instead of listed individually.

    Restricted to the live betting pool (universe + coeff band + min EV + market
    support): /stats and this API must not show sports, markets, or odds the bot
    no longer stakes.

    Grouped by (top-level sport, factor_id, label) — not by parameter separately:
    `label` already bakes the parameter in (resolve_factor_label() in
    backend/parser_service.py always appends it, e.g. "Тотал Больше (2.5)"), so
    factor_id + label alone already distinguishes lines the way a human reads
    Fonbet's own market names. Top-level sport is split out of sport_path's
    " / "-joined breadcrumb the same way the frontend does
    (sport_path.split("/")[0] in neurobets/page.tsx) so e.g. "П1" in football and "П1"
    in basketball are never lumped into one bar.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    pool_sql, pool_params = _live_pool_sql("e", "h", "h.final_coefficient")
    f_cursor.execute(f"""
        SELECT TRIM(SPLIT_PART(e.sport_path, '/', 1)) AS sport,
               h.factor_id, h.label,
               COUNT(*) AS judged,
               SUM(CASE WHEN {outcome_will_win_sql()} = h.is_win THEN 1 ELSE 0 END) AS correct
          FROM finished_bets h
          JOIN finished_events e ON h.event_id = e.event_id
         WHERE h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL
           {pool_sql}
         GROUP BY 1, 2, 3
    """, pool_params)
    rows = _filter_by_market_support(f_cursor.fetchall())
    release_connection(f_conn)

    sports: Dict[str, Dict[str, Any]] = {}
    overall_judged = 0
    overall_correct = 0

    for r in rows:
        sport = r["sport"] or "Другое"
        judged = r["judged"] or 0
        correct = r["correct"] or 0
        incorrect = judged - correct
        overall_judged += judged
        overall_correct += correct

        bucket = sports.setdefault(sport, {
            "sport": sport, "judged": 0, "correct": 0, "incorrect": 0, "bet_types": [],
        })
        bucket["judged"] += judged
        bucket["correct"] += correct
        bucket["incorrect"] += incorrect
        bucket["bet_types"].append({
            "factor_id": r["factor_id"],
            "label": r["label"] or f"Исход {r['factor_id']}",
            "judged": judged,
            "correct": correct,
            "incorrect": incorrect,
            "guess_rate_pct": round(correct / judged * 100.0, 1) if judged > 0 else 0.0,
        })

    sport_list = []
    for bucket in sports.values():
        bucket["bet_types"].sort(key=lambda b: b["judged"], reverse=True)
        bucket["guess_rate_pct"] = (
            round(bucket["correct"] / bucket["judged"] * 100.0, 1) if bucket["judged"] > 0 else 0.0
        )
        sport_list.append(bucket)
    sport_list.sort(key=lambda s: s["judged"], reverse=True)

    return {
        "overall": {
            "judged": overall_judged,
            "correct": overall_correct,
            "incorrect": overall_judged - overall_correct,
            # None (not 0.0) when there's nothing judged yet — same "never fabricate a
            # number" rule as get_db_stats (backend/database.py's guess_rate_pct).
            "guess_rate_pct": round(overall_correct / overall_judged * 100.0, 1) if overall_judged > 0 else None,
        },
        "sports": sport_list,
    }


# Bucketed by final_coefficient (its value at the point the bet was cut off/settled) —
# a bookmaker's margin and a model's real edge both behave very differently at short
# odds vs long ones, so one blended number hides more than it shows.
ROI_COEFF_BUCKETS = [
    (1.0, 1.5, "1.0–1.5"),
    (1.5, 2.0, "1.5–2.0"),
    (2.0, 3.0, "2.0–3.0"),
    (3.0, 5.0, "3.0–5.0"),
    (5.0, 10.0, "5.0–10.0"),
    (10.0, None, "10.0+"),
]


def get_roi_stats() -> Dict[str, Any]:
    """
    Flat-stake ROI and Brier-score calibration, bucketed by coefficient — the numbers
    that actually answer "is this model profitable," not just "how often is it right."
    Restricted to the same live pool as /stats-by-type (universe + coeff band + min EV
    + market support). Empty coefficient buckets are omitted so 1.0–1.5 / 2.0+ no
    longer appear once those bands are outside the stake rules.

    A model that only ever calls favorites at ~1.2 can hit an 80%+ guess-rate while
    still losing money on every single bet once the bookmaker's margin is netted out —
    guess-rate alone can't tell those two situations apart, ROI can.

    - roi_pct: flat 1-unit-per-bet return on every bet the model's own verdict
      (predicted_win) said to place in this bucket — (returns - stakes) / stakes * 100.
      Positive means betting every one of the model's "will win" verdicts at these odds,
      flat stakes, no compounding, would have made money. This is the ROI the model's
      own decisions would have produced — not what Kelly staking actually risked.
    - brier: mean squared error of the model's own calibrated win_probability against
      the real 0/1 outcome, over every judged bet in the bucket (not just predicted-win
      ones) — lower is better calibrated, 0 is a perfect forecaster.
    - brier_baseline: the same score using the bookmaker's own implied probability
      (1 / coefficient) as the "prediction" instead of the model's — the number to beat.
      If the model's Brier isn't below this, it isn't adding anything the raw odds
      didn't already say for free.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    pool_sql, pool_params = _live_pool_sql("e", "h", "h.final_coefficient")
    f_cursor.execute(f"""
        SELECT TRIM(SPLIT_PART(e.sport_path, '/', 1)) AS sport,
               h.factor_id, h.label,
               h.final_coefficient, h.is_win, h.predicted_win, h.predicted_win_probability
          FROM finished_bets h
          JOIN finished_events e ON h.event_id = e.event_id
         WHERE h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL
           {pool_sql}
    """, pool_params)
    rows = _filter_by_market_support(f_cursor.fetchall())
    release_connection(f_conn)

    def bucket_name(coeff: float) -> Optional[str]:
        for lo, hi, name in ROI_COEFF_BUCKETS:
            if coeff >= lo and (hi is None or coeff < hi):
                return name
        return None

    acc: Dict[str, Dict[str, Any]] = {
        name: {"judged": 0, "staked": 0.0, "returned": 0.0, "bets_placed": 0, "brier_sum": 0.0, "baseline_sum": 0.0, "brier_n": 0}
        for _, _, name in ROI_COEFF_BUCKETS
    }
    overall = {"judged": 0, "staked": 0.0, "returned": 0.0, "bets_placed": 0}

    for r in rows:
        coeff = r["final_coefficient"]
        name = bucket_name(coeff)
        if name is None:
            continue
        b = acc[name]
        b["judged"] += 1
        overall["judged"] += 1
        is_win = r["is_win"] == 1

        if r["predicted_win"] == 1:
            b["bets_placed"] += 1
            b["staked"] += 1.0
            overall["bets_placed"] += 1
            overall["staked"] += 1.0
            if is_win:
                b["returned"] += coeff
                overall["returned"] += coeff

        if r["predicted_win_probability"] is not None:
            outcome = 1.0 if is_win else 0.0
            p = r["predicted_win_probability"] / 100.0
            b["brier_sum"] += (p - outcome) ** 2
            b["baseline_sum"] += (1.0 / coeff - outcome) ** 2
            b["brier_n"] += 1

    buckets = []
    for _, _, name in ROI_COEFF_BUCKETS:
        b = acc[name]
        if b["judged"] == 0:
            continue
        buckets.append({
            "range": name,
            "judged": b["judged"],
            "bets_placed": b["bets_placed"],
            "roi_pct": round((b["returned"] - b["staked"]) / b["staked"] * 100.0, 1) if b["staked"] > 0 else None,
            "brier": round(b["brier_sum"] / b["brier_n"], 4) if b["brier_n"] > 0 else None,
            "brier_baseline": round(b["baseline_sum"] / b["brier_n"], 4) if b["brier_n"] > 0 else None,
        })

    return {
        "buckets": buckets,
        "overall": {
            "judged": overall["judged"],
            "bets_placed": overall["bets_placed"],
            "roi_pct": round((overall["returned"] - overall["staked"]) / overall["staked"] * 100.0, 1) if overall["staked"] > 0 else None,
        },
    }

def get_neurobets_history(
    sport_filter: Optional[str] = None,
    search: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    *,
    include_summary: bool = True,
) -> Dict[str, Any]:
    base_query = f"""
        FROM finished_bets h
        JOIN finished_events e ON h.event_id = e.event_id
        WHERE 1=1
        {universe_sql("e", "h")}
    """
    uni_sports, uni_factors = universe_sql_params()
    params: List[Any] = [uni_sports, uni_factors]

    if sport_filter and sport_filter.lower() != "all":
        base_query += " AND e.sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    if search:
        base_query += " AND (e.match_name ILIKE %s OR e.team_1 ILIKE %s OR e.team_2 ILIKE %s)"
        s = f"%{search.lower()}%"
        params.extend([s, s, s])

    summary = (
        get_neurobets_history_summary(sport_filter, search, outcome_filter)
        if include_summary
        else None
    )

    filtered_query = base_query
    if outcome_filter == "correct":
        filtered_query += (
            " AND h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL"
            f" AND {outcome_will_win_sql()} IS NOT NULL"
            f" AND {outcome_will_win_sql()} = h.is_win"
        )
    elif outcome_filter == "incorrect":
        filtered_query += (
            " AND h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL"
            f" AND {outcome_will_win_sql()} IS NOT NULL"
            f" AND {outcome_will_win_sql()} <> h.is_win"
        )
    elif outcome_filter == "push":
        filtered_query += " AND h.is_win IS NULL AND COALESCE(h.is_push, 0) = 1"
    elif outcome_filter == "pending":
        filtered_query += (
            " AND (h.is_win IS NULL AND COALESCE(h.is_push, 0) = 0"
            " OR (h.is_win IS NOT NULL AND h.predicted_win IS NULL)"
            f" OR (h.is_win IS NOT NULL AND {outcome_will_win_sql()} IS NULL))"
        )

    history_items: List[Dict[str, Any]] = []
    if limit > 0:
        # id is IDENTITY-on-archive, so newest id ≈ most recently finished. The unique
        # idx_finished_bets_id turns this LIMIT into an index scan instead of sorting
        # the whole 450k-row archive — critical while training/backtest saturate disk.
        data_query = f"""
            SELECT
                h.id AS id, h.event_id, h.factor_id, h.market_prefix, h.label, h.parameter,
                h.initial_coefficient, h.final_coefficient, h.score_at_time, h.is_win, h.is_push,
                h.predicted_win, h.predicted_win_probability,
                {outcome_will_win_sql()} AS will_win,
                h.first_seen_at AS timestamp, h.finished_at,
                e.sport_path, e.match_name, e.team_1, e.team_2, e.score_1, e.score_2, e.score
            {filtered_query}
            ORDER BY h.id DESC
            LIMIT %s OFFSET %s
        """
        with dashboard_db("finished") as f_conn:
            f_cursor = f_conn.cursor()
            f_cursor.execute(data_query, params + [limit, offset])
            history_items = [dict(r) for r in f_cursor.fetchall()]

    return {
        "summary": summary,
        "history": history_items,
    }


HISTORY_SUMMARY_CACHE_SECONDS = float(os.getenv("NEUROBET_HISTORY_SUMMARY_CACHE_SECONDS", "90"))
_history_summary_cache: Dict[str, Dict[str, Any]] = {}
_history_summary_refresh_lock = threading.Lock()
_history_summary_refreshing: set[str] = set()


def _history_summary_cache_key(sport_filter: Optional[str], search: Optional[str]) -> str:
    return f"{(sport_filter or '').strip().lower()}|{(search or '').strip().lower()}"


def _empty_history_summary() -> Dict[str, Any]:
    return {
        "total_count": 0,
        "correct_count": 0,
        "incorrect_count": 0,
        "push_count": 0,
        "pending_count": 0,
        "judged_count": 0,
        "guess_rate_pct": 0.0,
        "miss_rate_pct": 0.0,
        "filtered_count": 0,
    }


def _history_summary_from_row(summary_row: Any, outcome_filter: Optional[str]) -> Dict[str, Any]:
    total_count = summary_row["total_count"] or 0
    correct_count = summary_row["correct_count"] or 0
    incorrect_count = summary_row["incorrect_count"] or 0
    push_count = summary_row["push_count"] or 0
    pending_count = summary_row["pending_count"] or 0
    judged_count = correct_count + incorrect_count
    guess_rate_pct = round((correct_count / judged_count * 100.0), 1) if judged_count > 0 else 0.0
    filtered_count = {
        "correct": correct_count,
        "incorrect": incorrect_count,
        "push": push_count,
        "pending": pending_count,
    }.get(outcome_filter or "", total_count)
    return {
        "total_count": total_count,
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "push_count": push_count,
        "pending_count": pending_count,
        "judged_count": judged_count,
        "guess_rate_pct": guess_rate_pct,
        "miss_rate_pct": round(100.0 - guess_rate_pct, 1) if judged_count > 0 else 0.0,
        "filtered_count": filtered_count,
    }


def _compute_history_summary(
    sport_filter: Optional[str],
    search: Optional[str],
    outcome_filter: Optional[str],
) -> Dict[str, Any]:
    base_query = f"""
        FROM finished_bets h
        JOIN finished_events e ON h.event_id = e.event_id
        WHERE 1=1
        {universe_sql("e", "h")}
    """
    uni_sports, uni_factors = universe_sql_params()
    params: List[Any] = [uni_sports, uni_factors]

    if sport_filter and sport_filter.lower() != "all":
        base_query += " AND e.sport_path ILIKE %s"
        params.append(f"%{sport_filter.lower()}%")

    if search:
        base_query += " AND (e.match_name ILIKE %s OR e.team_1 ILIKE %s OR e.team_2 ILIKE %s)"
        s = f"%{search.lower()}%"
        params.extend([s, s, s])

    with dashboard_db("finished", timeout_ms=15000) as f_conn:
        f_cursor = f_conn.cursor()
        count_query = f"""
            SELECT COUNT(*) AS total_count,
                   SUM(CASE WHEN h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL
                                 AND {outcome_will_win_sql()} IS NOT NULL
                                 AND {outcome_will_win_sql()} = h.is_win THEN 1 ELSE 0 END) AS correct_count,
                   SUM(CASE WHEN h.is_win IS NOT NULL AND h.predicted_win IS NOT NULL
                                 AND {outcome_will_win_sql()} IS NOT NULL
                                 AND {outcome_will_win_sql()} <> h.is_win THEN 1 ELSE 0 END) AS incorrect_count,
                   SUM(CASE WHEN h.is_win IS NULL AND COALESCE(h.is_push, 0) = 1 THEN 1 ELSE 0 END) AS push_count,
                   SUM(CASE WHEN h.is_win IS NULL AND COALESCE(h.is_push, 0) = 0
                            OR (h.is_win IS NOT NULL AND h.predicted_win IS NULL)
                            OR (h.is_win IS NOT NULL AND {outcome_will_win_sql()} IS NULL) THEN 1 ELSE 0 END) AS pending_count
            {base_query}
        """
        f_cursor.execute(count_query, params)
        summary_row = f_cursor.fetchone()
    return _history_summary_from_row(summary_row, outcome_filter)


def _refresh_history_summary_cache(cache_key: str, sport_filter: Optional[str], search: Optional[str]) -> None:
    try:
        base_summary = _compute_history_summary(sport_filter, search, outcome_filter=None)
        _history_summary_cache[cache_key] = {
            "loaded_at": time.time(),
            "base": base_summary,
        }
    except Exception as e:
        logger.error(f"History summary background refresh failed ({cache_key}): {e}")
    finally:
        with _history_summary_refresh_lock:
            _history_summary_refreshing.discard(cache_key)


def get_neurobets_history_summary(
    sport_filter: Optional[str] = None,
    search: Optional[str] = None,
    outcome_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Cached aggregate counts for history tab badges — no row fetch."""
    cache_key = _history_summary_cache_key(sport_filter, search)
    now = time.time()
    entry = _history_summary_cache.get(cache_key)
    age = now - float(entry["loaded_at"]) if entry else float("inf")

    if entry is None or age >= HISTORY_SUMMARY_CACHE_SECONDS:
        if entry is not None and entry.get("base"):
            with _history_summary_refresh_lock:
                if cache_key not in _history_summary_refreshing:
                    _history_summary_refreshing.add(cache_key)
                    threading.Thread(
                        target=_refresh_history_summary_cache,
                        args=(cache_key, sport_filter, search),
                        daemon=True,
                        name=f"history-summary-{cache_key[:24]}",
                    ).start()
            base = entry["base"]
        else:
            base = _compute_history_summary(sport_filter, search, outcome_filter=None)
            _history_summary_cache[cache_key] = {"loaded_at": now, "base": base}
    else:
        base = entry["base"]

    if not outcome_filter or outcome_filter == "all":
        return dict(base)
    filtered = dict(base)
    filtered["filtered_count"] = {
        "correct": base["correct_count"],
        "incorrect": base["incorrect_count"],
        "push": base["push_count"],
        "pending": base["pending_count"],
    }.get(outcome_filter, base["total_count"])
    return filtered


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


def get_bankroll_state(*, include_ledger: bool = True) -> Dict[str, Any]:
    """
    Reads the neural bettor's bankroll state directly from autobet_finished.db —
    written by ai_service/app/neuralbet/bankroll.py, which shares this same file over
    the ./data volume. See docs in that module for the "training" vs "live" account
    split. Returns both accounts plus each one's recent ledger for a balance curve.
    The homepage only needs accounts; skip the ledger scan there.
    """
    with dashboard_db("finished") as f_conn:
        f_cursor = f_conn.cursor()

        accounts = {}
        for account in ("training", "live"):
            f_cursor.execute("SELECT * FROM bankroll_accounts WHERE account = %s", (account,))
            row = f_cursor.fetchone()
            accounts[account] = dict(row) if row else None

        ledger: Dict[str, list] = {"training": [], "live": []}
        if include_ledger:
            for account in ("training", "live"):
                f_cursor.execute(
                    "SELECT * FROM bankroll_ledger WHERE account = %s ORDER BY id DESC LIMIT 200",
                    (account,),
                )
                ledger[account] = [dict(r) for r in f_cursor.fetchall()]

    return _sanitize_non_finite({"accounts": accounts, "ledger": ledger})


def get_live_bets(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    """The bot's actual simulated live bets, newest first.

    `status`:
      - ``open`` — currently staked positions (enriched with live score/timer/odds)
      - ``settled`` — won/lost/void/cancelled (no live enrichment)
      - omitted — mixed page, same as before

    Already-placed bets are never filtered by the current Brier/sport stake
    whitelist: that gate decides *new* stakes, not whether history is visible.
    """
    status_norm = (status or "").strip().lower() or None
    where_params: List[Any] = []
    if status_norm == "open":
        where_sql = "WHERE status = 'open'"
        order_sql = "ORDER BY id DESC"
    elif status_norm == "settled":
        where_sql = "WHERE status <> 'open'"
        order_sql = "ORDER BY id DESC"
    elif status_norm in ("won", "lost", "void", "cancelled"):
        where_sql = "WHERE status = %s"
        where_params = [status_norm]
        order_sql = "ORDER BY id DESC"
    else:
        where_sql = ""
        order_sql = "ORDER BY id DESC"

    with dashboard_db("finished") as f_conn:
        f_cursor = f_conn.cursor()
        f_cursor.execute(f"SELECT COUNT(*) AS c FROM live_bets {where_sql}", where_params)
        total = f_cursor.fetchone()["c"]
        f_cursor.execute(
            f"SELECT * FROM live_bets {where_sql} {order_sql} LIMIT %s OFFSET %s",
            where_params + [limit, offset],
        )
        rows = [dict(r) for r in f_cursor.fetchall()]

    if not rows:
        return {"total": total, "items": rows}

    event_ids = list({r["event_id"] for r in rows})
    open_ids = list({r["event_id"] for r in rows if r.get("status") == "open"})

    live_info: Dict[int, Dict[str, Any]] = {}
    current_odds: Dict[tuple, float] = {}
    current_preds: Dict[tuple, Dict[str, Any]] = {}
    latest_scrape_ts = None
    if open_ids:
        with dashboard_db("live") as conn:
            cursor = conn.cursor()
            # An event stuck in the grace period after vanishing from Fonbet's feed still has
            # is_live=1 with its score/timer/coefficients frozen — comparing last_updated_at to
            # the latest successful scrape cycle's timestamp (not just to itself) is what
            # actually tells "genuinely live right now" apart from "hasn't been finalized yet".
            cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
            latest_scrape_ts = cursor.fetchone()["max"]
            cursor.execute(
                "SELECT event_id, score, score_1, score_2, timer, is_live, last_updated_at, sport_path FROM events WHERE event_id = ANY(%s)",
                (open_ids,),
            )
            live_info = {r["event_id"]: dict(r) for r in cursor.fetchall()}
            cursor.execute(
                """SELECT l.event_id, l.factor_id, l.parameter, l.market_prefix, l.coefficient
                    FROM latest_odds l
                    JOIN events e ON e.event_id = l.event_id
                    WHERE l.event_id = ANY(%s)
                      AND l.updated_at = e.last_updated_at
                      AND e.last_updated_at = %s""",
                (open_ids, latest_scrape_ts),
            )
            current_odds = {
                (r["event_id"], r["factor_id"], r["parameter"] or "", r["market_prefix"] or ""): r["coefficient"]
                for r in cursor.fetchall()
            }
            cursor.execute(
                """SELECT event_id, factor_id,
                          COALESCE(parameter, '') AS parameter,
                          COALESCE(market_prefix, '') AS market_prefix,
                          predicted_win, expected_roi
                     FROM ai_predictions
                    WHERE event_id = ANY(%s)""",
                (open_ids,),
            )
            current_preds = {
                (r["event_id"], r["factor_id"], r["parameter"] or "", r["market_prefix"] or ""): dict(r)
                for r in cursor.fetchall()
            }

    missing_ids = [eid for eid in event_ids if eid not in live_info]
    finished_info: Dict[int, Dict[str, Any]] = {}
    if missing_ids:
        with dashboard_db("finished") as f_conn:
            f_cursor = f_conn.cursor()
            f_cursor.execute(
                "SELECT event_id, score, score_1, score_2, sport_path FROM finished_events WHERE event_id = ANY(%s)",
                (missing_ids,),
            )
            finished_info = {r["event_id"]: dict(r) for r in f_cursor.fetchall()}

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
            pred = current_preds.get(odds_key)
            b["current_predicted_win"] = None if pred is None else pred.get("predicted_win")
            b["current_expected_roi"] = None if pred is None else pred.get("expected_roi")
        else:
            info = finished_info.get(eid)
            b["current_score"] = info["score"] if info else None
            b["current_timer"] = None
            b["match_is_live"] = False
            b["current_coefficient"] = None
            b["sport_path"] = info["sport_path"] if info else None
            b["current_predicted_win"] = None
            b["current_expected_roi"] = None

    return {"total": total, "items": _sanitize_non_finite(rows)}


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
                f"Bet cancelled by admin: "
                f"{_outcome_desc(b['match_name'], b['market_prefix'], b['label'], b['parameter'])} — "
                f"{stake:.1f} ₽ refunded to balance."
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
# Mirrors ai_service/app/neuralbet/bankroll.py's MAX_BALANCE — upper bound on balance/
# peak_balance so unbounded compounding across settlements can't drift the DOUBLE
# PRECISION balance toward float overflow (inf/NaN, which the NOT NULL column rejects).
LIVE_MAX_BALANCE = float(os.getenv("BANKROLL_MAX_BALANCE", "1000000000000.0"))
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
    sb = min(start_balance if start_balance is not None else LIVE_START_BALANCE, LIVE_MAX_BALANCE)
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

    # At most one open bot bet per event, full stop — not just per market. Two markets
    # on the same match are routinely correlated even when they aren't strictly
    # mutually exclusive (e.g. "П1 wins" and "team 2's individual total over 2.5" pull
    # against each other: a match trending toward a P1 win usually means team 2 isn't
    # scoring much), and modeling *how* correlated any given pair of markets is would
    # need per-sport, per-scoreline statistics no simpler rule can safely approximate.
    # One position per match sidesteps needing that model at all. Seeded from
    # already-open bets, then grown as this batch places its own, so two candidates on
    # the same event within one batch are also caught (keeping only the first — batches
    # arrive pre-sorted by expected_roi, so that's the strongest pick).
    f_cursor.execute("SELECT DISTINCT event_id FROM live_bets WHERE status = 'open'")
    occupied_events = {r["event_id"] for r in f_cursor.fetchall()}

    for c in candidates:
        event_id, factor_id = c["event_id"], c["factor_id"]
        parameter = c.get("parameter") or ""
        market_prefix = c.get("market_prefix") or ""

        # Defense in depth: ai_service already filters via live_gate_skip_reason, but
        # refuse moneyline (etc.) here too if NEURALBET_LIVE_STAKE_MARKETS is totals-only.
        if not in_live_stake_market(factor_id=factor_id):
            skipped.append({"candidate": c, "reason": "market_outside_live_list"})
            continue

        if slots_left <= 0:
            skipped.append({"candidate": c, "reason": "max_positions_reached"})
            continue

        if not _is_market_fresh(cursor, event_id, factor_id, parameter, market_prefix):
            skipped.append({"candidate": c, "reason": "stale_market"})
            continue

        if event_id in occupied_events:
            skipped.append({"candidate": c, "reason": "event_already_has_open_bet"})
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

        occupied_events.add(event_id)

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
                f"Bet opened: {_outcome_desc(c.get('match_name',''), market_prefix, c.get('label',''), parameter)} "
                f"@ {c['coefficient']:.2f} — {stake:.1f} ₽ ({c['stake_fraction']*100.0:.1f}% of bank), "
                f"win probability {c['win_probability']:.1f}%, potential payout {stake * c['coefficient']:.1f} ₽."
            ),
        })

    f_conn.commit()
    release_connection(f_conn)
    release_connection(conn)
    return {"placed": placed, "skipped": skipped, "messages": messages}


def _apply_bet_settlement(
    f_cursor, b, is_win: Optional[int], is_push: bool = False,
) -> Tuple[str, List[Dict[str, str]]]:
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
        if is_push:
            reason = "push: line landed exactly"
        else:
            reason = "outcome not resolved"
        messages.append({
            "category": "BANKROLL", "level": "INFO",
            "message": f"Bet voided ({reason}): {outcome_desc} — {stake:.1f} ₽ refunded to balance.",
        })
    elif is_win:
        status, payout, outcome = "won", stake * b["coefficient"], "win"
        messages.append({
            "category": "BANKROLL", "level": "INFO",
            "message": (
                f"Bet WON: {outcome_desc} @ {b['coefficient']:.2f} — "
                f"staked {stake:.1f} ₽ → returned {payout:.1f} ₽ (profit +{payout - stake:.1f} ₽)."
            ),
        })
    else:
        status, payout, outcome = "lost", 0.0, "loss"
        messages.append({
            "category": "BANKROLL", "level": "WARNING",
            "message": f"Bet LOST: {outcome_desc} @ {b['coefficient']:.2f} — staked {stake:.1f} ₽ → lost {stake:.1f} ₽.",
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
    balance_after = min(balance_after, LIVE_MAX_BALANCE)
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
                f"BOT RUINED (ruin #{ruin_count}): balance hit 0, "
                f"auto-reset to {acc['start_balance']:.1f} ₽."
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
            f"Cycle summary: {settled} bets settled ({won} won / {lost} lost / {void} void). "
            f"Balance: {live_acc['balance']:.1f} ₽ (peak {live_acc['peak_balance']:.1f} ₽, ruins {live_acc['ruin_count']})."
        ),
    }


_TIMER_SET_RE = re.compile(r"(\d+)\s*\*?\s*[-:]\s*(\d+)\s*\*?")
_NAMED_PERIOD_SCORE_RE = re.compile(
    r"^(\d+)-[а-яё]+\s+(сет|тайм|период|четверть|половина)$",
    re.IGNORECASE,
)


def _period_scores_from_timer(timer: Optional[str]) -> List[Tuple[int, int]]:
    """Fonbet often parks set scores in `timer` ('(11-9 11-5 10*-6) за 3 место')."""
    if not timer:
        return []
    return [(int(a), int(b)) for a, b in _TIMER_SET_RE.findall(str(timer))]


def _period_scores_from_named(
    named: Optional[Dict[str, Tuple[int, int]]],
) -> List[Tuple[int, int]]:
    by_ord: Dict[int, Tuple[int, int]] = {}
    for key, val in (named or {}).items():
        m = _NAMED_PERIOD_SCORE_RE.match(str(key).strip())
        if not m:
            continue
        try:
            by_ord[int(m.group(1))] = (int(val[0]), int(val[1]))
        except Exception:
            continue
    if not by_ord:
        return []
    max_o = max(by_ord)
    if any(i not in by_ord for i in range(1, max_o + 1)):
        return []
    return [by_ord[i] for i in range(1, max_o + 1)]


def _period_scores_quality(sport_path: str, periods: List[Tuple[int, int]]) -> Tuple[int, int]:
    finished = sum(1 for a, b in periods if _period_looks_finished(sport_path, a, b))
    return (finished, len(periods))


def _best_period_scores(
    stored: List[Tuple[int, int]],
    timer: Optional[str],
    named: Optional[Dict[str, Tuple[int, int]]] = None,
    sport_path: str = "",
) -> List[Tuple[int, int]]:
    """Prefer the source with the most completed sets, not merely the longest list."""
    candidates = [
        stored or [],
        _period_scores_from_timer(timer),
        _period_scores_from_named(named),
    ]
    return max(candidates, key=lambda ps: _period_scores_quality(sport_path, ps))


def _merge_period_scores(
    stored: List[Tuple[int, int]],
    timer: Optional[str],
    named: Optional[Dict[str, Tuple[int, int]]] = None,
    sport_path: str = "",
) -> List[Tuple[int, int]]:
    return _best_period_scores(stored, timer, named, sport_path)


def _stale_past_results_wait(last_updated: Any, as_of: Any) -> bool:
    """True once the live snapshot has been frozen longer than the official-results wait."""
    wait = float(settings.EVENT_MISS_GRACE_MINUTES + settings.EVENT_RESULTS_WAIT_MINUTES)
    age = _minutes_since(last_updated, as_of)
    return age is None or age >= wait


def _stale_past_grace_only(start_raw: Any, as_of: Any) -> bool:
    """Shorter staleness check for “frozen last set” voiding.

    When the match is already off-feed, waiting full EVENT_RESULTS_WAIT_MINUTES
    can keep period/set bets stuck for hours if the event is intermittently
    re-seen. For these “incomplete last set” cases we prefer resolving quickly.
    """
    wait = float(settings.EVENT_MISS_GRACE_MINUTES)
    age = _minutes_since(start_raw, as_of)
    return age is None or age >= wait


def _parse_period_scores_json(raw: Any) -> List[Tuple[int, int]]:
    try:
        return [tuple(p) for p in json.loads(raw or "[]")]
    except Exception:
        return []


def _parse_named_scores_json(raw: Any) -> Dict[str, Tuple[int, int]]:
    try:
        return {k: tuple(v) for k, v in json.loads(raw or "{}").items()}
    except Exception:
        return {}


def _latest_live_scrape_ts(cursor) -> Optional[str]:
    cursor.execute("SELECT MAX(last_updated_at) AS max FROM events")
    row = cursor.fetchone()
    return row["max"] if row else None


def _feed_active(is_live: bool, last_updated_at: Any, latest_scrape_ts: Any) -> bool:
    """Same definition the UI uses for match_is_live — stale last_updated_at is finished."""
    return bool(is_live) and last_updated_at is not None and last_updated_at == latest_scrape_ts


def _is_main_match_winner_bet(factor_id: int, market_prefix: str) -> bool:
    prefix = (market_prefix or "").strip()
    if prefix and prefix != MAIN_MARKET_PREFIX:
        return False
    return int(factor_id) in MAIN_MATCH_WINNER_FACTORS


def _load_event_grading_state(event_id: int) -> Optional[Dict[str, Any]]:
    """Match scores for grading open bets when finished_bets row is not ready yet."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        latest_scrape_ts = _latest_live_scrape_ts(cursor)
        cursor.execute(
            """SELECT score_1, score_2, sport_path, period_scores_json, named_scores_json,
                      is_live, last_updated_at, missing_since, timer
                 FROM events WHERE event_id = %s""",
            (event_id,),
        )
        row = cursor.fetchone()
        if row:
            stored = _parse_period_scores_json(row["period_scores_json"])
            named = _parse_named_scores_json(row["named_scores_json"])
            sport_path = row["sport_path"] or ""
            db_is_live = bool(row["is_live"])
            return {
                "score_1": int(row["score_1"] or 0),
                "score_2": int(row["score_2"] or 0),
                "sport_path": sport_path,
                "period_scores": _best_period_scores(
                    stored, row.get("timer"), named, sport_path,
                ),
                "named_scores": named,
                "feed_active": _feed_active(
                    db_is_live, row.get("last_updated_at"), latest_scrape_ts,
                ),
                "finalized": not db_is_live,
                "missing_since": row.get("missing_since"),
                "last_updated_at": row.get("last_updated_at"),
            }
    finally:
        release_connection(conn)

    f_conn = get_finished_connection()
    try:
        f_cursor = f_conn.cursor()
        f_cursor.execute(
            """SELECT score_1, score_2, sport_path, period_scores_json, named_scores_json
                 FROM finished_events WHERE event_id = %s""",
            (event_id,),
        )
        row = f_cursor.fetchone()
        if row:
            sport_path = row["sport_path"] or ""
            named = _parse_named_scores_json(row["named_scores_json"])
            return {
                "score_1": int(row["score_1"] or 0),
                "score_2": int(row["score_2"] or 0),
                "sport_path": sport_path,
                "period_scores": _best_period_scores(
                    _parse_period_scores_json(row["period_scores_json"]),
                    None,
                    named,
                    sport_path,
                ),
                "named_scores": named,
                "feed_active": False,
                "finalized": True,
                "last_updated_at": None,
            }
    finally:
        release_connection(f_conn)
    return None


def _grade_live_bet(b: Dict[str, Any], state: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    sport_path = state["sport_path"]
    if is_fast_format_sport_path(sport_path):
        return None, False
    return resolve_outcome(
        b["factor_id"],
        b["label"] or "",
        b["parameter"] or "",
        state["score_1"],
        state["score_2"],
        market_prefix=b["market_prefix"] or "",
        sport_path=sport_path,
        period_scores=state["period_scores"],
        named_scores=state["named_scores"],
    )


def _period_ready_for_settlement(
    is_live: bool,
    ordinal: int,
    period_scores: List[Tuple[int, int]],
    sport_path: str = "",
) -> bool:
    """Period N is gradable once period N+1 starts (live), or once the match is off-feed.

    Incomplete last-set snapshots (10-6 in table tennis) are not treated as finals.
    """
    n = len(period_scores)
    if n < ordinal:
        return False
    target = period_scores[ordinal - 1]
    later_period_started = n > ordinal
    if is_live:
        return later_period_started
    if later_period_started:
        return True
    return _period_looks_finished(sport_path, target[0], target[1])


def settle_live_bets(timestamp_str: str) -> Dict[str, Any]:
    """
    Resolves any 'open' live_bets whose underlying event has since been archived (i.e.
    finished_bets now has an outcome row for it). Called from archive_and_settle()
    right after archive_finished_events(), so a bet settles in the same scrape cycle
    its event finalizes in — not whenever ai_service's own inference cycle next happens
    to run.
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
            SELECT is_win, is_push FROM finished_bets
            WHERE event_id = %s AND factor_id = %s AND COALESCE(parameter,'') = COALESCE(%s,'')
              AND COALESCE(market_prefix,'') = COALESCE(%s,'')
        """, (b["event_id"], b["factor_id"], b["parameter"], b["market_prefix"]))
        row = f_cursor.fetchone()
        if row is not None:
            outcome, msgs = _apply_bet_settlement(
                f_cursor, b, row["is_win"], bool(row["is_push"]),
            )
            messages.extend(msgs)
            if outcome == "win":
                won += 1
            elif outcome == "loss":
                lost += 1
            else:
                void += 1
            continue

        # Event finalized (is_live=0) but not archived yet — grade from live DB scores
        # instead of waiting for finished_bets (can lag minutes behind results API).
        state = _load_event_grading_state(b["event_id"])
        if state is None or not state.get("finalized"):
            continue

        prefix = (b["market_prefix"] or "").strip()
        ordinal = _parse_period_ordinal(prefix) if prefix and prefix != MAIN_MARKET_PREFIX else None
        start_ts = state.get("missing_since") or state.get("last_updated_at")
        # Main-match bets need the official-results wait — interim scores (1:1 on П1)
        # must not settle as a loss during a line recalc / premature finalize.
        if ordinal is None and not _stale_past_results_wait(start_ts, timestamp_str):
            continue

        period_ready = True
        if ordinal is not None:
            period_ready = _period_ready_for_settlement(
                False, ordinal, state["period_scores"], state["sport_path"],
            )
        if not period_ready:
            # Frozen last set on a dead feed (Liga Pro «за 3 место» at 10*-6 for hours).
            # After the official-results wait, void rather than grade 10-6 as a final
            # or leave the stake locked forever.
            if not _stale_past_grace_only(start_ts, timestamp_str):
                continue
            is_win, is_push = None, False
        else:
            is_win, is_push = _grade_live_bet(b, state)
            if is_win is None and not is_push:
                continue
        outcome, msgs = _apply_bet_settlement(f_cursor, b, is_win, is_push)
        messages.extend(msgs)
        if outcome == "win":
            won += 1
        elif outcome == "loss":
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

    Deliberately does not settle the *last* period while the match is still live — its end
    is the match's end, and the existing short grace period settles that quickly once
    is_live=0 (via this function for finalized events, or settle_live_bets after archive).

    Also runs for is_live=0 events still waiting in the live DB for archival — previously
    skipped, which left period/set bets stuck at «матч завершён» for minutes.
    """
    conn = get_connection()
    cursor = conn.cursor()
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()

    f_cursor.execute("SELECT * FROM live_bets WHERE status = 'open'")
    open_bets = f_cursor.fetchall()
    won = lost = void = 0
    messages: List[Dict[str, str]] = []
    latest_scrape_ts = _latest_live_scrape_ts(cursor)

    for b in open_bets:
        prefix = (b["market_prefix"] or "").strip()
        if not prefix or prefix == MAIN_MARKET_PREFIX:
            continue  # main-match bets settle via the full-archival path instead
        ordinal = _parse_period_ordinal(prefix)
        if ordinal is None:
            continue

        cursor.execute(
            """SELECT score_1, score_2, sport_path, period_scores_json, named_scores_json,
                      is_live, last_updated_at, missing_since, timer
                 FROM events WHERE event_id = %s""",
            (b["event_id"],),
        )
        ev = cursor.fetchone()
        if ev is None:
            continue  # archived — settle_live_bets uses finished_bets / finished_events
        sport_path = ev["sport_path"] or ""
        if is_fast_format_sport_path(sport_path):
            continue  # ungradable compressed sim — full-archive path voids it

        if latest_scrape_ts is None:
            latest_scrape_ts = _latest_live_scrape_ts(cursor)
        feed_active = _feed_active(bool(ev["is_live"]), ev.get("last_updated_at"), latest_scrape_ts)
        named = _parse_named_scores_json(ev.get("named_scores_json"))
        period_scores = _best_period_scores(
            _parse_period_scores_json(ev["period_scores_json"]),
            ev.get("timer"),
            named,
            sport_path,
        )
        if not _period_ready_for_settlement(
            feed_active, ordinal, period_scores, sport_path,
        ):
            start_ts = ev.get("missing_since") or ev.get("last_updated_at")
            if feed_active or not _stale_past_grace_only(start_ts, timestamp_str):
                continue
            is_win, is_push = None, False
        else:
            is_win, is_push = resolve_outcome(
                b["factor_id"], b["label"] or "", b["parameter"], ev["score_1"], ev["score_2"],
                market_prefix=prefix, sport_path=sport_path, period_scores=period_scores,
                named_scores=named,
            )
            if is_win is None and not is_push:
                continue

        outcome, msgs = _apply_bet_settlement(f_cursor, b, is_win, is_push)
        messages.extend(msgs)
        if outcome == "win":
            won += 1
        elif outcome == "loss":
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



