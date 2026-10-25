from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/research/flashscore_capacity_http_soak.py"
SPEC = importlib.util.spec_from_file_location("flashscore_capacity_http_soak", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_container_selection_uses_canonical_service_container(monkeypatch):
    monkeypatch.setattr(
        MODULE,
        "command",
        lambda _args: "a1 temporary_research\nb2 neurobet_research\nc3 neurobet_research_test",
    )
    assert MODULE.container_id("research") == "b2"


def test_container_selection_fails_if_canonical_container_is_not_running(monkeypatch):
    monkeypatch.setattr(MODULE, "command", lambda _args: "a1 temporary_research")
    try:
        MODULE.container_id("research")
    except RuntimeError as exc:
        assert "neurobet_research" in str(exc)
    else:
        raise AssertionError("missing canonical service container must be visible")
