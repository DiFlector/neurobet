"""Market shrink, sibling EV after shrink, reliability buckets (no torch / DB)."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SHARED = os.path.join(_REPO, "shared")
_AI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from neurobet_filters import shrink_p_toward_market  # noqa: E402
from neurobet_features.sibling import apply_sibling_coherence  # noqa: E402
import neurobet_features.sibling as sibling_mod  # noqa: E402
import neurobet_filters as filters  # noqa: E402


def _load_review():
    """Load review.py without importing app.neuralbet (avoids torch)."""
    os.environ.setdefault("DATABASE_URL", "postgresql://x:x@localhost/x")
    if "app" not in sys.modules:
        app = types.ModuleType("app")
        app.__path__ = [os.path.join(_AI_ROOT, "app")]
        sys.modules["app"] = app
    if "app.config" not in sys.modules:
        cfg = types.ModuleType("app.config")
        cfg.MODEL_DIR = "/tmp"
        sys.modules["app.config"] = cfg
    if "app.neuralbet" not in sys.modules:
        nb = types.ModuleType("app.neuralbet")
        nb.__path__ = [os.path.join(_AI_ROOT, "app", "neuralbet")]
        sys.modules["app.neuralbet"] = nb

    qg_path = os.path.join(_AI_ROOT, "app", "neuralbet", "quality_gate.py")
    if "app.neuralbet.quality_gate" not in sys.modules:
        spec = importlib.util.spec_from_file_location("app.neuralbet.quality_gate", qg_path)
        assert spec and spec.loader
        qg = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = qg
        spec.loader.exec_module(qg)

    review_path = os.path.join(_AI_ROOT, "app", "neuralbet", "review.py")
    spec = importlib.util.spec_from_file_location("app.neuralbet.review", review_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class ShrinkTests(unittest.TestCase):
    def test_mix_toward_one_over_coeff(self):
        p = 0.70
        coeff = 1.73
        market = 1.0 / coeff
        mixed = shrink_p_toward_market(p, coeff, shrink=0.25)
        expected = 0.75 * p + 0.25 * market
        self.assertAlmostEqual(mixed, expected, places=6)

    def test_zero_shrink_is_identity(self):
        self.assertAlmostEqual(shrink_p_toward_market(0.70, 1.73, shrink=0.0), 0.70)

    def test_full_shrink_is_market(self):
        self.assertAlmostEqual(
            shrink_p_toward_market(0.70, 1.73, shrink=1.0),
            1.0 / 1.73,
            places=6,
        )


class SiblingShrinkTests(unittest.TestCase):
    def setUp(self):
        self._old = sibling_mod.MARKET_SHRINK
        sibling_mod.MARKET_SHRINK = 0.25
        filters.MARKET_SHRINK = 0.25

    def tearDown(self):
        sibling_mod.MARKET_SHRINK = self._old
        filters.MARKET_SHRINK = self._old

    def test_borderline_ev_drops_below_min_edge(self):
        # 61.6% at 1.73 is ~6.6% EV; after 0.25 shrink (~60.7%) EV falls under 5%.
        row = {
            "event_id": 1,
            "factor_id": 921,
            "parameter": "",
            "market_prefix": "",
            "calibrated_p": 0.616,
            "coeff": 1.73,
        }
        apply_sibling_coherence([row], min_edge_pct=5.0)
        self.assertAlmostEqual(row["calibrated_p"], 0.6065, places=3)
        self.assertLess(row["expected_roi"], 5.0)
        self.assertEqual(row["predicted_win"], 0)

    def test_two_way_totals_renorm_after_shrink(self):
        over = {
            "event_id": 7,
            "factor_id": 930,
            "parameter": "2.5",
            "market_prefix": "",
            "calibrated_p": 0.62,
            "coeff": 1.80,
        }
        under = {
            "event_id": 7,
            "factor_id": 931,
            "parameter": "2.5",
            "market_prefix": "",
            "calibrated_p": 0.38,
            "coeff": 1.90,
        }
        apply_sibling_coherence([over, under], min_edge_pct=5.0)
        total = over["calibrated_p"] + under["calibrated_p"]
        self.assertAlmostEqual(total, 1.0, places=6)


class ReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = _load_review()

    def test_bucket_gap_and_overconfident_flag(self):
        records = [
            {"current_prob": 62.0, "is_win": 1 if i < 36 else 0}
            for i in range(90)
        ]
        bins = self.review.build_reliability(records)
        mid = next(b for b in bins if b["lo_pct"] == 60.0 and b["hi_pct"] == 65.0)
        self.assertEqual(mid["n"], 90)
        self.assertAlmostEqual(mid["mean_pred_pct"], 62.0, places=1)
        self.assertAlmostEqual(mid["empirical_win_pct"], 40.0, places=1)
        self.assertLess(mid["gap_pct"], -5)

        flags = self.review._build_flags(
            {"config": {}, "by_sport": []},
            {"pass": True, "reasons": []},
            {"folds": 0, "negative_roi_folds": 0},
            None,
            None,
            bins,
        )
        codes = {f["code"] for f in flags}
        self.assertIn("overconfident_probs", codes)


if __name__ == "__main__":
    unittest.main()
