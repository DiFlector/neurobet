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

BucketKey = Tuple[Optional[str], int]


def get_calibration_buckets() -> Dict[BucketKey, Tuple[float, float]]:
    """
    Returns {(sport, decile): (weighted_wins, weighted_total)} — plus a
    {(None, decile): ...} entry pooling every sport together as a fallback — built from
    the CALIBRATION_RECENCY_WINDOW most-recently-resolved bets, each weighted by
    recency (see CALIBRATION_HALF_LIFE). Two refinements over a flat all-time count:
      - per-sport buckets: the model's over/underconfidence at a given raw probability
        isn't the same across sports (the /neurobet/stats guess-rate breakdown shows
        football and table tennis calibrating very differently) — calibrate_probability
        falls back to the pooled bucket when a sport's own bucket is too thin to trust.
      - recency-weighted, not flat-counted: a bucket reflects how the model calibrates
        *now*, not an average smeared across many retrained versions of it.
    `sport` is the top-level segment of sport_path — the same split
    get_bet_type_stats/the frontend use to group by sport.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
    f_cursor.execute("""
        SELECT TRIM(SPLIT_PART(e.sport_path, '/', 1)) AS sport,
               h.predicted_win_probability, h.is_win
          FROM finished_bets h
          JOIN finished_events e ON h.event_id = e.event_id
         WHERE h.is_win IS NOT NULL AND h.predicted_win_probability IS NOT NULL
         ORDER BY h.finished_at DESC
         LIMIT %s
    """, (CALIBRATION_RECENCY_WINDOW,))
    rows = f_cursor.fetchall()
    release_connection(f_conn)

    sport_buckets: Dict[BucketKey, list] = {}
    global_buckets: Dict[int, list] = {}
    for rank, r in enumerate(rows):
        weight = 0.5 ** (rank / CALIBRATION_HALF_LIFE)
        bucket_idx = min(9, max(0, int((r["predicted_win_probability"] or 0) // 10)))
        win_weight = weight if r["is_win"] == 1 else 0.0
        sport = r["sport"] or "Другое"

        sb = sport_buckets.setdefault((sport, bucket_idx), [0.0, 0.0])
        sb[0] += win_weight
        sb[1] += weight

        gb = global_buckets.setdefault(bucket_idx, [0.0, 0.0])
        gb[0] += win_weight
        gb[1] += weight

    buckets: Dict[BucketKey, Tuple[float, float]] = {k: (v[0], v[1]) for k, v in sport_buckets.items()}
    buckets.update({(None, b): (v[0], v[1]) for b, v in global_buckets.items()})
    return buckets


def calibrate_probability(
    raw_prob: float, buckets: Dict[BucketKey, Tuple[float, float]], sport: Optional[str] = None,
) -> float:
    """
    Blends the model's raw probability with the empirical (recency-weighted) win rate
    of bets historically predicted at a similar confidence, for the same sport when
    there's enough sport-specific history to trust — Bayesian shrinkage towards real,
    per-sport outcomes.
    """
    bucket_idx = min(9, max(0, int(raw_prob // 10)))
    wins, total = buckets.get((sport or "Другое", bucket_idx), (0.0, 0.0))
    if total < MIN_SPORT_BUCKET_WEIGHT:
        wins, total = buckets.get((None, bucket_idx), (0.0, 0.0))
    prior_wins = (raw_prob / 100.0) * CALIBRATION_PRIOR_STRENGTH
    calibrated = (wins + prior_wins) / (total + CALIBRATION_PRIOR_STRENGTH) * 100.0
    return round(min(max(calibrated, 1.0), 99.0), 1)
