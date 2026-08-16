"""
Probability calibration, ported from backend/database.py so ai_service can bet on the
same number the frontend shows instead of its own raw, uncalibrated one — before this,
the bot placed live bets on the ensemble's raw win_probability while the "Нейроставки"
UI filtered/sorted on a calibrated version computed separately in backend, so the two
could silently disagree about which outcomes actually looked good. Calibration now
happens once, here, before a prediction is stored or acted on; backend just reads what
was saved.
"""
from typing import Any, Dict, Optional, Tuple

from app.core.database import get_finished_connection, release_connection

# How strongly the empirical bucket win-rate pulls the raw model probability towards
# itself — a bucket with many resolved bets dominates; one with only a couple barely
# moves the raw estimate.
CALIBRATION_PRIOR_STRENGTH = 20.0

# Only the most-recently-resolved bets feed calibration, not the entire history — the
# model gets retrained online every cycle, so a bucket's empirical win rate should
# reflect how the *current* weights calibrate, not an average blurred across many
# earlier, materially different versions of the model. Bounded window also keeps this
# query's cost flat as the archive grows past 150k+ rows instead of scanning all of it
# every cycle.
CALIBRATION_RECENCY_WINDOW = 20000
# Weight halves every this many bets further back — a bet from 5000 resolved bets ago
# counts half as much as one from today, one from 10000 ago a quarter as much, etc.
CALIBRATION_HALF_LIFE = 5000.0

# A sport-specific bucket needs at least this much *effective* (recency-weighted)
# resolved-bet weight before it's trusted over the pooled cross-sport bucket —
# otherwise a thinly-traded sport (e.g. crikет: a handful of resolved bets — see
# /neurobet/stats) would calibrate off what's statistically just noise.
MIN_SPORT_BUCKET_WEIGHT = 30.0

# Thresholds for the two coefficient-aware bucket levels (finer than MIN_SPORT_BUCKET_WEIGHT
# since they're a 6x finer split of the same data — see coeff_bucket_index). The
# production stats export showed the calibration error is not flat across probability
# deciles: it's driven by *coefficient* (ROI -3% at coeff 1-1.5 vs -44% at coeff 10+,
# same probability deciles mixed across both). A (sport, decile) bucket blurs a real 35%
# on a 2.5 coefficient together with a dressed-up 35% on a 17 coefficient; splitting by
# coefficient too lets each get corrected on its own.
MIN_SPORT_COEFF_BUCKET_WEIGHT = 15.0
MIN_GLOBAL_COEFF_BUCKET_WEIGHT = 20.0

BucketKey = Tuple[Optional[str], int]
CoeffBucketKey = Tuple[Optional[str], int, int]

# Mirrors the coefficient bands the /neurobet/stats ROI-by-coefficient table already
# groups by, so a calibration bucket lines up with the same slice a human reading that
# page would recognize. Index 5 ("10.0+") is open-ended.
COEFF_BUCKET_EDGES = [1.5, 2.0, 3.0, 5.0, 10.0]


def coeff_bucket_index(coeff: Optional[float]) -> int:
    c = float(coeff) if coeff else 1.0
    for i, edge in enumerate(COEFF_BUCKET_EDGES):
        if c < edge:
            return i
    return len(COEFF_BUCKET_EDGES)


def get_calibration_buckets() -> Dict[CoeffBucketKey, Tuple[float, float]]:
    """
    Returns {(sport, decile, coeff_bucket): (weighted_wins, weighted_total)} — plus
    coarser fallback entries at (None, decile, coeff_bucket), (sport, decile, None) and
    (None, decile, None) — built from the CALIBRATION_RECENCY_WINDOW most-recently-
    resolved bets, each weighted by recency (see CALIBRATION_HALF_LIFE). Three
    refinements over a flat all-time count:
      - per-sport buckets: the model's over/underconfidence at a given raw probability
        isn't the same across sports (the /neurobet/stats guess-rate breakdown shows
        football and table tennis calibrating very differently).
      - per-coefficient buckets: nor is it flat across coefficient at a fixed
        probability decile (see COEFF_BUCKET_EDGES's docstring) — a bucket mixing a
        genuine 35% at coeff 2.5 with an inflated 35% at coeff 17 can't correct either
        one properly.
      - recency-weighted, not flat-counted: a bucket reflects how the model calibrates
        *now*, not an average smeared across many retrained versions of it.
    calibrate_probability walks from the finest (sport, decile, coeff_bucket) bucket
    down to the coarsest pooled (None, decile, None) one as each level's weight proves
    too thin to trust.
    `sport` is the top-level segment of sport_path — the same split
    get_bet_type_stats/the frontend use to group by sport.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("""
        SELECT TRIM(SPLIT_PART(e.sport_path, '/', 1)) AS sport,
               h.predicted_win_probability, h.is_win, h.final_coefficient
          FROM finished_bets h
          JOIN finished_events e ON h.event_id = e.event_id
         WHERE h.is_win IS NOT NULL AND h.predicted_win_probability IS NOT NULL
         ORDER BY h.finished_at DESC
         LIMIT %s
    """, (CALIBRATION_RECENCY_WINDOW,))
    rows = f_cursor.fetchall()
    release_connection(f_conn)

    buckets: Dict[CoeffBucketKey, list] = {}
    for rank, r in enumerate(rows):
        weight = 0.5 ** (rank / CALIBRATION_HALF_LIFE)
        decile = min(9, max(0, int((r["predicted_win_probability"] or 0) // 10)))
        win_weight = weight if r["is_win"] == 1 else 0.0
        sport = r["sport"] or "Другое"
        cb = coeff_bucket_index(r["final_coefficient"])

        for key in ((sport, decile, cb), (None, decile, cb), (sport, decile, None), (None, decile, None)):
            b = buckets.setdefault(key, [0.0, 0.0])
            b[0] += win_weight
            b[1] += weight

    return {k: (v[0], v[1]) for k, v in buckets.items()}


def calibrate_probability(
    raw_prob: float,
    buckets: Dict[CoeffBucketKey, Tuple[float, float]],
    sport: Optional[str] = None,
    coeff: Optional[float] = None,
) -> float:
    """
    Blends the model's raw probability with the empirical (recency-weighted) win rate
    of bets historically predicted at a similar confidence — for the same sport and the
    same coefficient band when there's enough history to trust either, falling back
    (sport+coeff -> coeff-only -> sport-only -> global) as each level proves too thin —
    Bayesian shrinkage towards real, per-sport-per-coefficient outcomes.
    """
    decile = min(9, max(0, int(raw_prob // 10)))
    cb = coeff_bucket_index(coeff) if coeff is not None else None
    s = sport or "Другое"

    wins, total = (0.0, 0.0)
    if cb is not None:
        wins, total = buckets.get((s, decile, cb), (0.0, 0.0))
        if total < MIN_SPORT_COEFF_BUCKET_WEIGHT:
            wins, total = buckets.get((None, decile, cb), (0.0, 0.0))
    if total < MIN_GLOBAL_COEFF_BUCKET_WEIGHT:
        wins, total = buckets.get((s, decile, None), (0.0, 0.0))
    if total < MIN_SPORT_BUCKET_WEIGHT:
        wins, total = buckets.get((None, decile, None), (0.0, 0.0))

    prior_wins = (raw_prob / 100.0) * CALIBRATION_PRIOR_STRENGTH
    calibrated = (wins + prior_wins) / (total + CALIBRATION_PRIOR_STRENGTH) * 100.0
    return round(min(max(calibrated, 1.0), 99.0), 1)
