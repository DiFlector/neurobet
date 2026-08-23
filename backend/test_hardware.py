"""Hardware snapshot shape — no GPU required."""
from __future__ import annotations

from hardware import get_hardware_snapshot


def test_hardware_snapshot_has_required_blocks():
    snap = get_hardware_snapshot(ai_url=None)
    for key in ("cpu", "memory", "disk", "gpu", "sampled_at"):
        assert key in snap, key
    assert isinstance(snap["cpu"]["percent"], float)
    assert snap["cpu"]["cores_logical"] >= 1
    assert snap["memory"]["total_bytes"] > 0
    assert 0 <= snap["memory"]["percent"] <= 100
    assert snap["disk"]["total_bytes"] > 0
    assert 0 <= snap["disk"]["percent"] <= 100
    assert "available" in snap["gpu"]
    assert isinstance(snap["gpu"]["gpus"], list)


if __name__ == "__main__":
    test_hardware_snapshot_has_required_blocks()
    print("ok")
