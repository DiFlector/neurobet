"""
Probability calibration, ported from backend/database.py so ai_service can bet on the
same number the frontend shows instead of its own raw, uncalibrated one — before this,
the bot placed live bets on the ensemble's raw win_probability while the "Нейроставки"
UI filtered/sorted on a calibrated version computed separately in backend, so the two
could silently disagree about which outcomes actually looked good. Calibration now
happens once, here, before a prediction is stored or acted on; backend just reads what
was saved.
"""
from typing import Any, Dict, Tuple

from app.core.database import get_finished_connection, release_connection

# How strongly the empirical bucket win-rate pulls the raw model probability towards
# itself — a bucket with many resolved bets dominates; one with only a couple barely
# moves the raw estimate. Kept identical to backend's old CALIBRATION_PRIOR_STRENGTH so
# behavior doesn't shift just from moving the code.
CALIBRATION_PRIOR_STRENGTH = 20.0


def get_calibration_buckets() -> Dict[int, Tuple[int, int]]:
    """
    Returns {decile: (wins, total)} built from every resolved finished bet where we know
    what the model predicted at bet time — i.e. the model's actual historical track
    record, bucketed by what confidence it claimed. Used to correct the raw model
    probability into one that reflects real-world win rate instead of the model's own
    (often overconfident) self-estimate.
    """
    f_conn = get_finished_connection()
    f_cursor = f_conn.cursor()
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


def calibrate_probability(raw_prob: float, buckets: Dict[int, Tuple[int, int]]) -> float:
    """
    Blends the model's raw probability with the empirical win rate of bets that were
    historically predicted at a similar confidence (Bayesian shrinkage towards real
    outcomes).
    """
    bucket = min(9, max(0, int(raw_prob // 10)))
    wins, total = buckets.get(bucket, (0, 0))
    prior_wins = (raw_prob / 100.0) * CALIBRATION_PRIOR_STRENGTH
    calibrated = (wins + prior_wins) / (total + CALIBRATION_PRIOR_STRENGTH) * 100.0
    return round(min(max(calibrated, 1.0), 99.0), 1)
