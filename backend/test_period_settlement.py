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

    def test_volleyball_prelisted_second_set_does_not_finish_first(self):
        periods = [(10, 12), (0, 0)]
        sport = "Волейбол. Россия"
        self.assertEqual(_live_started_period_count(periods, sport), 1)
        self.assertFalse(
            _period_ready_for_settlement(True, 1, periods, sport),
            "1st set must stay open while Fonbet pre-lists 2nd set at 0:0 at 10:12",
        )

    def test_volleyball_tied_set_not_finished(self):
        periods = [(19, 19), (0, 0)]
        sport = "Волейбол"
        self.assertFalse(_period_ready_for_settlement(True, 1, periods, sport))

    def test_volleyball_mid_set_15plus_does_not_finish_first(self):
        # Live Brazil U19 / NCAA: 1st set 17:19 or 16:20 with 2nd pre-listed 0:0.
        # Old hi>=15 rule treated these as finished and settled П1/П2.
        sport = "Волейбол. Бразилия. До 19 лет. Паулиста"
        for periods in ([(17, 19), (0, 0)], [(16, 20), (0, 0)]):
            self.assertEqual(_live_started_period_count(periods, sport), 1)
            self.assertFalse(
                _period_ready_for_settlement(True, 1, periods, sport),
                f"{periods[0]} must stay open until 25 with a 2-point lead",
            )

    def test_volleyball_deuce_runs_past_25(self):
        sport = "Волейбол"
        for score in ((25, 24), (26, 25), (28, 27)):
            periods = [score, (0, 0)]
            self.assertEqual(_live_started_period_count(periods, sport), 1)
            self.assertFalse(
                _period_ready_for_settlement(True, 1, periods, sport),
                f"{score} is still deuce, set can go to 27+",
            )
        for score in ((26, 24), (27, 25), (30, 28)):
            periods = [score, (0, 0)]
            self.assertEqual(_live_started_period_count(periods, sport), 2)
            self.assertTrue(
                _period_ready_for_settlement(True, 1, periods, sport),
                f"{score} already has 25+ and a 2-point lead",
            )

    def test_volleyball_settles_after_real_second_set_start(self):
        periods = [(25, 20), (0, 0)]
        sport = "Волейбол"
        self.assertEqual(_live_started_period_count(periods, sport), 2)
        self.assertTrue(_period_ready_for_settlement(True, 1, periods, sport))

    def test_volleyball_fifth_set_to_15_settles_when_match_ends(self):
        periods = [(25, 20), (25, 18), (20, 25), (18, 25), (15, 13)]
        sport = "Волейбол. США. Женщины. NCAA"
        self.assertFalse(
            _period_ready_for_settlement(True, 5, periods, sport),
            "last set must wait while the match is still live",
        )
        self.assertTrue(
            _period_ready_for_settlement(
                False, 5, periods, sport, match_db_live=False,
            )
        )
        self.assertFalse(
            _period_ready_for_settlement(
                False, 5,
                [(25, 20), (25, 18), (20, 25), (18, 25), (16, 15)],
                sport, match_db_live=False,
            ),
            "5th set 16:15 is still deuce",
        )
        self.assertTrue(
            _period_ready_for_settlement(
                False, 5,
                [(25, 20), (25, 18), (20, 25), (18, 25), (17, 15)],
                sport, match_db_live=False,
            )
        )

    def test_beach_volleyball_21_not_15(self):
        sport = "Пляжный волейбол"
        still_playing = [(16, 18), (0, 0)]
        self.assertEqual(_live_started_period_count(still_playing, sport), 1)
        self.assertFalse(_period_ready_for_settlement(True, 1, still_playing, sport))
        finished = [(21, 18), (0, 0)]
        self.assertEqual(_live_started_period_count(finished, sport), 2)
        self.assertTrue(_period_ready_for_settlement(True, 1, finished, sport))

    def test_named_zero_halves_do_not_beat_real_first_half(self):
        stored = [(1, 1)]
        named = {"1-й тайм": (0, 0), "2-й тайм": (0, 0)}
        best = _best_period_scores(stored, None, named, "Футбол")
        self.assertEqual(best, [(1, 1)])


if __name__ == "__main__":
    unittest.main()
