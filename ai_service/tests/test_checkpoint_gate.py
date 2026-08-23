"""Online checkpoint accept/reject (no torch)."""
from __future__ import annotations

import os
import sys
import unittest

_AI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GATE = os.path.join(_AI_ROOT, "app", "neuralbet", "checkpoint_gate.py")

import importlib.util

spec = importlib.util.spec_from_file_location("neurobet_checkpoint_gate", _GATE)
assert spec and spec.loader
_mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = _mod
spec.loader.exec_module(_mod)
decide = _mod.decide_online_checkpoint


def _call(**overrides):
    kwargs = dict(
        attempted_brier=0.20,
        incoming_brier=0.21,
        last_accepted=0.19,
        floor_tol=0.02,
        brier_eps=1e-4,
        min_best_epoch=3,
        best_epoch=4,
    )
    kwargs.update(overrides)
    return decide(**kwargs)


class CheckpointGateTests(unittest.TestCase):
    def test_rejects_low_best_epoch_first(self):
        ok, reason = _call(best_epoch=1, attempted_brier=0.18, incoming_brier=0.24)
        self.assertFalse(ok)
        self.assertEqual(reason, "best_epoch")

    def test_floor_blocks_when_incoming_still_ok(self):
        ok, reason = _call(
            last_accepted=0.19,
            incoming_brier=0.20,
            attempted_brier=0.24,
            best_epoch=5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "floor")

    def test_recovery_when_incoming_already_over_floor(self):
        ok, reason = _call(
            last_accepted=0.19,
            incoming_brier=0.240,
            attempted_brier=0.235,
            best_epoch=5,
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_no_recovery_if_attempted_not_better(self):
        ok, reason = _call(
            last_accepted=0.19,
            incoming_brier=0.240,
            attempted_brier=0.241,
            best_epoch=5,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "floor")

    def test_normal_accept_within_floor(self):
        ok, reason = _call(
            last_accepted=0.19,
            incoming_brier=0.205,
            attempted_brier=0.200,
            best_epoch=4,
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_reject_incoming_when_no_improvement(self):
        ok, reason = _call(
            last_accepted=0.19,
            incoming_brier=0.200,
            attempted_brier=0.201,
            best_epoch=4,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "incoming")


if __name__ == "__main__":
    unittest.main()
