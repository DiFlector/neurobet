"""Regression: football 1H must not settle on a pre-listed 2nd-half [0,0]."""
import unittest

from database import (
    _best_period_scores,
    _live_started_period_count,
    _period_ready_for_settlement,
)


class PeriodSettlementReadyTests(unittest.TestCase):
    def test_football_trailing_zero_half_not_started_while_live(self):
        periods = [(1, 1), (0, 0)]
        sport = "Футбол. Европа"
        self.assertEqual(_live_started_period_count(periods, sport), 1)
        self.assertFalse(
            _period_ready_for_settlement(True, 1, periods, sport),
            "1H must stay open while Fonbet only pre-lists 2H as 0:0",
        )

    def test_stale_scrape_does_not_settle_while_db_still_live(self):
        periods = [(0, 1), (0, 0)]
        sport = "Футбол"
        # feed_active=False but events.is_live=1 (missed one scrape / injury time)
        self.assertFalse(
            _period_ready_for_settlement(
                False, 1, periods, sport, match_db_live=True,
            )
        )

    def test_football_settles_after_second_half_goal(self):
        periods = [(1, 1), (0, 1)]
        sport = "Футбол"
        self.assertTrue(_period_ready_for_settlement(True, 1, periods, sport))

    def test_football_settles_when_match_leaves_board(self):
        periods = [(1, 1), (0, 0)]
        sport = "Футбол"
        self.assertTrue(
            _period_ready_for_settlement(
                False, 1, periods, sport, match_db_live=False,
            )
        )

    def test_tennis_zero_zero_next_set_counts_as_started(self):
        periods = [(6, 4), (0, 0)]
        sport = "Теннис. ATP"
        self.assertEqual(_live_started_period_count(periods, sport), 2)
        self.assertTrue(_period_ready_for_settlement(True, 1, periods, sport))

    def test_named_zero_halves_do_not_beat_real_first_half(self):
        stored = [(1, 1)]
        named = {"1-й тайм": (0, 0), "2-й тайм": (0, 0)}
        best = _best_period_scores(stored, None, named, "Футбол")
        self.assertEqual(best, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
