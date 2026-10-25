"""Checkpoint/resume coverage for the offline API-Tennis canonical import."""

from datetime import datetime, timezone
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import import_api_tennis_canonical_archive as importer  # noqa: E402


class _Result:
    def __init__(self, *, first=None, rows=None):
        self._first = first
        self._rows = rows or []

    def first(self):
        return self._first

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, store):
        self.store = store
        self.cursor_seen = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "SELECT state_json" in sql:
            return _Result(first=(self.store["state"],) if self.store["state"] else None)
        if "SELECT source_match_id, available_at, payload" in sql:
            self.cursor_seen.append((params.get("last_id"), params.get("last_at")))
            if params.get("last_id") is None:
                return _Result(rows=self.store["rows"])
            return _Result(rows=[])
        if "INSERT INTO external_tennis_source_state" in sql:
            import json

            self.store["state"] = json.loads(params["state"])
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_import_resumes_after_committed_checkpoint_without_reprojecting_page(monkeypatch):
    available_at = datetime(2026, 10, 22, tzinfo=timezone.utc)
    store = {
        "state": {},
        "rows": [{
            "source_match_id": "api-match-1",
            "available_at": available_at,
            "payload": {"event": {"id": "api-match-1"}},
        }],
        "sessions": [],
        "projected": [],
    }

    def session_factory():
        session = _Session(store)
        store["sessions"].append(session)
        return session

    monkeypatch.setattr(importer, "SessionLocal", session_factory)
    monkeypatch.setattr(importer, "_api_tennis_projection_item", lambda event, at: {"event": event})

    def project(session, items):
        store["projected"].extend(items)
        return {"projected": len(items), "merged": 0, "ambiguous": 0,
                "unlinked": 0, "unchanged": 0}

    monkeypatch.setattr(importer, "project_api_tennis_results", project)

    first = importer.run(max_batches=1)
    assert first["status"] == "checkpointed"
    assert store["state"]["last_source_match_id"] == "api-match-1"
    assert store["state"]["status"] == "checkpointed"
    assert "paused_at" not in store["state"]
    assert store["state"]["checkpointed_at"]
    assert len(store["projected"]) == 1

    second = importer.run()
    assert second["status"] == "complete"
    assert len(store["projected"]) == 1
    assert store["sessions"][-1].cursor_seen == [
        ("api-match-1", available_at.isoformat())
    ]
