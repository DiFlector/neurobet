"""Admin enabled_sports ceiling (no torch / DB)."""
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
    ALLOWED_SPORTS,
    ALLOWED_MARKET_FAMILIES,
    ALLOWED_FACTOR_IDS,
    normalize_enabled_sports,
    set_enabled_sports,
    get_enabled_sports,
    normalize_enabled_markets,
    set_enabled_markets,
    get_enabled_markets,
    in_live_stake_sport,
    in_live_stake_market,
    universe_sql_params,
    live_universe_sql_params,
    factor_ids_for_markets,
    write_brier_stake_sports,
    clear_brier_stake_sports,
    normalize_backtest_mode,
    backtest_file_prefix,
    is_backtest_result_file,
    backtest_updates_live_gate,
)
import neurobet_filters as filters  # noqa: E402


class EnabledSportsTests(unittest.TestCase):
    def tearDown(self) -> None:
        filters._enabled_sports_cache = (-1.0, None)
        filters._enabled_markets_cache = (-1.0, None)

    def test_missing_means_all_allowed(self):
        self.assertEqual(normalize_enabled_sports(None), frozenset(ALLOWED_SPORTS))
        self.assertEqual(normalize_enabled_sports(["garbage", "xyz"]), frozenset())
        self.assertEqual(
            normalize_enabled_sports(["Футбол", "хоккей", "теннис"]),
            frozenset({"футбол", "теннис"}),
        )

    def test_empty_list_stays_empty(self):
        self.assertEqual(normalize_enabled_sports([], missing_means_all=False), frozenset())

    def test_default_from_missing_file_is_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = filters.AI_SETTINGS_PATH
            filters.AI_SETTINGS_PATH = os.path.join(tmp, "missing.json")
            filters._enabled_sports_cache = (-1.0, None)
            try:
                self.assertEqual(get_enabled_sports(), frozenset(ALLOWED_SPORTS))
            finally:
                filters.AI_SETTINGS_PATH = old
                filters._enabled_sports_cache = (-1.0, None)

    def test_file_empty_list_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ai_settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled_sports": []}, f)
            old = filters.AI_SETTINGS_PATH
            filters.AI_SETTINGS_PATH = path
            filters._enabled_sports_cache = (-1.0, None)
            try:
                self.assertEqual(get_enabled_sports(), frozenset())
            finally:
                filters.AI_SETTINGS_PATH = old
                filters._enabled_sports_cache = (-1.0, None)

    def test_admin_env_brier_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            brier_path = os.path.join(tmp, "brier.json")
            old_brier = filters.LIVE_BRIER_SPORTS_PATH
            old_gate = filters.BRIER_SPORT_GATE
            old_env = filters.LIVE_STAKE_SPORTS
            old_settings = filters.AI_SETTINGS_PATH
            filters.LIVE_BRIER_SPORTS_PATH = brier_path
            filters.BRIER_SPORT_GATE = True
            filters.LIVE_STAKE_SPORTS = frozenset({"футбол", "баскетбол", "теннис"})
            filters._brier_sports_cache = (-1.0, None)
            filters.AI_SETTINGS_PATH = os.path.join(tmp, "none.json")
            filters._enabled_sports_cache = (-1.0, None)
            try:
                write_brier_stake_sports(["футбол", "баскетбол"], source="test")
                set_enabled_sports(["футбол", "теннис", "хоккей"])
                self.assertTrue(in_live_stake_sport("Футбол / РФПЛ"))
                self.assertFalse(in_live_stake_sport("Баскетбол / NBA"))
                self.assertFalse(in_live_stake_sport("Теннис / ATP"))
                # Basketball is in env ∩ Brier but toggled off in admin.
                self.assertTrue(
                    in_live_stake_sport("Баскетбол / NBA", apply_admin=False),
                )
            finally:
                clear_brier_stake_sports()
                filters.LIVE_BRIER_SPORTS_PATH = old_brier
                filters.BRIER_SPORT_GATE = old_gate
                filters.LIVE_STAKE_SPORTS = old_env
                filters.AI_SETTINGS_PATH = old_settings
                filters._brier_sports_cache = (-1.0, None)
                filters._enabled_sports_cache = (-1.0, None)

    def test_universe_sql_params_sports_arg(self):
        sports, factors = universe_sql_params()
        self.assertEqual(set(sports), set(ALLOWED_SPORTS))
        self.assertTrue(factors)
        live, _ = universe_sql_params(["футбол", "мусор"])
        self.assertEqual(live, ["футбол"])
        empty, _ = universe_sql_params([])
        self.assertEqual(empty, [filters._ENABLED_SPORT_SENTINEL])


class EnabledMarketsTests(unittest.TestCase):
    def tearDown(self) -> None:
        filters._enabled_markets_cache = (-1.0, None)
        filters._enabled_sports_cache = (-1.0, None)

    def test_normalize_missing_and_aliases(self):
        self.assertEqual(normalize_enabled_markets(None), frozenset(ALLOWED_MARKET_FAMILIES))
        self.assertEqual(normalize_enabled_markets(["garbage"]), frozenset())
        self.assertEqual(
            normalize_enabled_markets(["П1", "under", "draw"]),
            frozenset({"w1", "total_under", "draw"}),
        )

    def test_empty_list_stays_empty(self):
        self.assertEqual(normalize_enabled_markets([], missing_means_all=False), frozenset())

    def test_file_empty_list_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ai_settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled_markets": []}, f)
            old = filters.AI_SETTINGS_PATH
            filters.AI_SETTINGS_PATH = path
            filters._enabled_markets_cache = (-1.0, None)
            try:
                self.assertEqual(get_enabled_markets(), frozenset())
            finally:
                filters.AI_SETTINGS_PATH = old
                filters._enabled_markets_cache = (-1.0, None)

    def test_missing_key_means_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ai_settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"enabled_sports": ["футбол"]}, f)
            old = filters.AI_SETTINGS_PATH
            filters.AI_SETTINGS_PATH = path
            filters._enabled_markets_cache = (-1.0, None)
            try:
                self.assertEqual(get_enabled_markets(), frozenset(ALLOWED_MARKET_FAMILIES))
            finally:
                filters.AI_SETTINGS_PATH = old
                filters._enabled_markets_cache = (-1.0, None)

    def test_vocab_market_label_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = filters.AI_SETTINGS_PATH
            old_env = filters.LIVE_STAKE_MARKETS
            filters.AI_SETTINGS_PATH = os.path.join(tmp, "none.json")
            filters.LIVE_STAKE_MARKETS = None
            filters._enabled_markets_cache = (-1.0, None)
            try:
                set_enabled_markets(["total_over", "w1"])
                self.assertTrue(in_live_stake_market(market_label="total_over"))
                self.assertTrue(in_live_stake_market(market_label="total_over"))
                self.assertFalse(in_live_stake_market(market_label="total_under"))
                self.assertFalse(in_live_stake_market(market_label="total_under"))
                self.assertTrue(in_live_stake_market(factor_id=921))
                self.assertFalse(in_live_stake_market(factor_id=923))
            finally:
                filters.AI_SETTINGS_PATH = old
                filters.LIVE_STAKE_MARKETS = old_env
                filters._enabled_markets_cache = (-1.0, None)

    def test_admin_blocks_stake_and_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = filters.AI_SETTINGS_PATH
            old_env = filters.LIVE_STAKE_MARKETS
            filters.AI_SETTINGS_PATH = os.path.join(tmp, "none.json")
            filters.LIVE_STAKE_MARKETS = None
            filters._enabled_markets_cache = (-1.0, None)
            try:
                set_enabled_markets(["w1", "draw"])
                self.assertTrue(in_live_stake_market(factor_id=921))
                self.assertFalse(in_live_stake_market(factor_id=923))
                self.assertTrue(in_live_stake_market(factor_id=923, apply_admin=False))
                ids = set(factor_ids_for_markets(["w1", "draw"]))
                self.assertIn(921, ids)
                self.assertIn(922, ids)
                self.assertNotIn(923, ids)
                _, factors = universe_sql_params(None, ["total_under"])
                self.assertTrue(factors)
                self.assertTrue(all(f in ALLOWED_FACTOR_IDS or f == filters._ENABLED_FACTOR_SENTINEL for f in factors))
                self.assertNotIn(921, factors)
                _, empty = universe_sql_params(None, [])
                self.assertEqual(empty, [filters._ENABLED_FACTOR_SENTINEL])
            finally:
                filters.AI_SETTINGS_PATH = old
                filters.LIVE_STAKE_MARKETS = old_env
                filters._enabled_markets_cache = (-1.0, None)

    def test_live_universe_intersects_sports_and_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = filters.AI_SETTINGS_PATH
            filters.AI_SETTINGS_PATH = os.path.join(tmp, "none.json")
            filters._enabled_sports_cache = (-1.0, None)
            filters._enabled_markets_cache = (-1.0, None)
            try:
                set_enabled_sports(["футбол"])
                set_enabled_markets(["w1"])
                sports, factors = live_universe_sql_params()
                self.assertEqual(sports, ["футбол"])
                self.assertEqual(set(factors), {921})
            finally:
                filters.AI_SETTINGS_PATH = old
                filters._enabled_sports_cache = (-1.0, None)
                filters._enabled_markets_cache = (-1.0, None)


class BacktestModeFileTests(unittest.TestCase):
    def test_live_files_exclude_full_prefix(self):
        self.assertEqual(normalize_backtest_mode("FULL"), "full")
        self.assertEqual(normalize_backtest_mode(None), "live")
        self.assertTrue(is_backtest_result_file("backtest_2026.json", "live"))
        self.assertFalse(is_backtest_result_file("backtest_full_2026.json", "live"))
        self.assertTrue(is_backtest_result_file("backtest_full_2026.json", "full"))
        self.assertFalse(is_backtest_result_file("backtest_2026.json", "full"))
        self.assertEqual(backtest_file_prefix("full"), "backtest_full_")
        self.assertEqual(backtest_file_prefix("live"), "backtest_")

    def test_only_live_updates_gate(self):
        self.assertTrue(backtest_updates_live_gate("live"))
        self.assertFalse(backtest_updates_live_gate("full"))
        self.assertFalse(backtest_updates_live_gate("FULL"))


if __name__ == "__main__":
    unittest.main()
