import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "streams" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "worker"))

from app.ingest import source_payload_provenance, should_reconcile_live_board


def test_fonbet_raw_envelope_resolves_archived_source_provenance():
    payload = {
        "source_payload_hash": "sha256:" + "a" * 64,
        "source_payload_s3_key": "fonbet/tennis/2026-10-26/aaaa/source.json",
        "events": [],
    }
    assert source_payload_provenance(payload) == (
        payload["source_payload_hash"], payload["source_payload_s3_key"]
    )
    assert source_payload_provenance({"source_payload_hash": 17, "source_payload_s3_key": None}) == (None, None)


def test_prematch_snapshot_never_reconciles_or_clears_live_board():
    ids = ["fonbet-prematch-1"]
    assert not should_reconcile_live_board({"page_type": "prematch"}, current_snapshot=True, seen_ids=ids)
    assert should_reconcile_live_board({"page_type": "live"}, current_snapshot=True, seen_ids=ids)
    assert not should_reconcile_live_board({"page_type": "live"}, current_snapshot=False, seen_ids=ids)
    assert not should_reconcile_live_board({"page_type": "live"}, current_snapshot=True, seen_ids=[])
