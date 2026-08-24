"""Brier-vs-market live sport allowlist (no torch / DB)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SHARED = os.path.join(_REPO, "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from neurobet_filters import (  # noqa: E402
    select_brier_stake_sports,
    write_brier_stake_sports,
    brier_stake_sports_override,
    in_live_stake_sport,
    clear_brier_stake_sports,
)
import neurobet_filters as filters  # noqa: E402


def _row(
    sport: str,
    brier: float,
    market: float,
    evaluated: int = 5000,
    *,
    bets: int = 80,
    roi_pct_lo: float | None = 10.0,
) -> dict:
    row = {
        "sport": sport,
        "evaluated": evaluated,
        "brier": brier,
        "market_brier": market,
        "bets": bets,
    }
    if roi_pct_lo is not None:
        row["roi_pct_lo"] = roi_pct_lo
    return row


class BrierSportGateTests(unittest.TestCase):
    def test_select_requires_clear_margin_and_positive_ci(self):
        selected, detail = select_brier_stake_sports(
            [
                _row("Футбол", 0.1582, 0.1797, bets=185, roi_pct_lo=43.2),
                _row("Настольный теннис", 0.2020, 0.2033, bets=0, roi_pct_lo=None),
                _row("Баскетбол", 0.2028, 0.2385, bets=68, roi_pct_lo=-6.8),
                _row("Теннис", 0.1746, 0.1740, bets=0, roi_pct_lo=None),
                _row("Волейбол", 0.1988, 0.1990, bets=0, roi_pct_lo=None),
            ],
            min_evaluated=2000,
            margin=0.005,
        )
        self.assertEqual(selected, ["футбол"])
        enabled = {d["sport"]: d["enabled"] for d in detail}
        self.assertTrue(enabled["футбол"])
        self.assertFalse(enabled["баскетбол"])
        self.assertFalse(enabled["настольный теннис"])
        self.assertFalse(enabled["теннис"])
        self.assertFalse(enabled["волейбол"])

    def test_select_skips_thin_sports(self):
        selected, _ = select_brier_stake_sports(
            [_row("Теннис", 0.10, 0.20, evaluated=100, bets=80, roi_pct_lo=20.0)],
            min_evaluated=2000,
            margin=0.005,
        )
        self.assertEqual(selected, [])

    def test_select_skips_thin_bets_even_with_brier(self):
        selected, _ = select_brier_stake_sports(
            [_row("Футбол", 0.15, 0.18, bets=10, roi_pct_lo=25.0)],
            min_evaluated=2000,
            margin=0.005,
            min_bets=40,
        )
        self.assertEqual(selected, [])

    def test_require_roi_lo_can_be_disabled(self):
        selected, _ = select_brier_stake_sports(
            [_row("Баскетбол", 0.2028, 0.2385, bets=68, roi_pct_lo=-6.8)],
            min_evaluated=2000,
            margin=0.005,
            require_roi_lo=False,
        )
        self.assertEqual(selected, ["баскетбол"])

    def test_nested_probability_shape(self):
        selected, _ = select_brier_stake_sports(
            [{
                "sport": "футбол",
                "evaluated": 8000,
                "probability": {
                    "current": {"brier": 0.18},
                    "market_raw": {"brier": 0.20},
                },
                "stake_policy": {"current": {"bets": 104, "roi_pct_lo": 13.9}},
            }],
            min_evaluated=2000,
            margin=0.005,
        )
        self.assertEqual(selected, ["футбол"])

    def test_file_roundtrip_filters_live_sport(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "live_stake_brier_sports.json")
            old_path = filters.LIVE_BRIER_SPORTS_PATH
            old_gate = filters.BRIER_SPORT_GATE
            filters.LIVE_BRIER_SPORTS_PATH = path
            filters.BRIER_SPORT_GATE = True
            filters._brier_sports_cache = (-1.0, None)
            try:
                write_brier_stake_sports(["футбол", "баскетбол"], source="test")
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                self.assertEqual(payload["sports"], ["баскетбол", "футбол"])
                self.assertEqual(
                    brier_stake_sports_override(),
                    frozenset({"футбол", "баскетбол"}),
                )
                self.assertTrue(in_live_stake_sport("Футбол / РФПЛ", apply_admin=False))
                self.assertTrue(in_live_stake_sport("Баскетбол / NBA", apply_admin=False))
                self.assertFalse(in_live_stake_sport("Теннис / ATP", apply_admin=False))
                self.assertFalse(in_live_stake_sport("Настольный теннис / Лига Про", apply_admin=False))
            finally:
                clear_brier_stake_sports()
                filters.LIVE_BRIER_SPORTS_PATH = old_path
                filters.BRIER_SPORT_GATE = old_gate
                filters._brier_sports_cache = (-1.0, None)

    def test_empty_allowlist_does_not_freeze_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "live_stake_brier_sports.json")
            old_path = filters.LIVE_BRIER_SPORTS_PATH
            old_gate = filters.BRIER_SPORT_GATE
            filters.LIVE_BRIER_SPORTS_PATH = path
            filters.BRIER_SPORT_GATE = True
            filters._brier_sports_cache = (-1.0, None)
            try:
                write_brier_stake_sports([], source="test")
                self.assertIsNone(brier_stake_sports_override())
                self.assertTrue(in_live_stake_sport("Футбол / РФПЛ", apply_admin=False))
            finally:
                clear_brier_stake_sports()
                filters.LIVE_BRIER_SPORTS_PATH = old_path
                filters.BRIER_SPORT_GATE = old_gate
                filters._brier_sports_cache = (-1.0, None)

    def test_zero_bet_backtest_does_not_overwrite_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "live_stake_brier_sports.json")
            old_path = filters.LIVE_BRIER_SPORTS_PATH
            old_gate = filters.BRIER_SPORT_GATE
            filters.LIVE_BRIER_SPORTS_PATH = path
            filters.BRIER_SPORT_GATE = True
            filters._brier_sports_cache = (-1.0, None)
            try:
                write_brier_stake_sports(["футбол"], source="prior")
                out = filters.update_brier_stake_sports_from_backtest({
                    "walk_forward": {"bets": 0},
                    "walk_forward_by_sport": [
                        _row("Футбол", 0.15, 0.18, bets=0, roi_pct_lo=None),
                    ],
                })
                self.assertTrue(out.get("skipped"))
                self.assertEqual(
                    brier_stake_sports_override(),
                    frozenset({"футбол"}),
                )
            finally:
                clear_brier_stake_sports()
                filters.LIVE_BRIER_SPORTS_PATH = old_path
                filters.BRIER_SPORT_GATE = old_gate
                filters._brier_sports_cache = (-1.0, None)


if __name__ == "__main__":
    unittest.main()
