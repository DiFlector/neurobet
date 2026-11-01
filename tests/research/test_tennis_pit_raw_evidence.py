import hashlib
import json
from datetime import datetime, timezone

from app.tennis_pit_raw_evidence import verify_fonbet_snapshot_evidence


def _snapshot(collected_at="2026-10-13T10:51:12.592036+00:00", *, event_id="123", score="0-0"):
    event = {
        "source_event_id": event_id, "is_live": True, "score": None,
        "score_state": {"raw_score_string": score},
        "expected_state": {"score": score},
    }
    payload = {"source": "fonbet", "sport_code": "tennis", "url": "https://fon.bet/live/tennis",
               "events": [event]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    document = {**payload, "collector_version": "1.0.0", "collected_at": collected_at,
                "page_type": "live", "content_hash": f"sha256:{digest}"}
    return f"fonbet/tennis/2026-10-13/{digest[:4]}/{digest}.json", json.dumps(
        document, sort_keys=True, ensure_ascii=False
    ).encode()


def _parse(raw):
    return raw["expected_state"]


def test_source_before_cutoff_is_accepted_with_original_available_at():
    key, body = _snapshot()
    result = verify_fonbet_snapshot_evidence(
        raw_key=key, body=body, source_event_id="123",
        snapshot_at=datetime.fromisoformat("2026-10-13T10:51:14.159136+00:00"),
        expected_score="0-0", expected_state={"score": "0-0"}, state_parser=_parse,
    )
    assert result["verified"] is True
    assert result["available_at"].isoformat() == "2026-10-13T10:51:12.592036+00:00"


def test_source_exactly_at_cutoff_is_accepted():
    cutoff = "2026-10-13T10:51:14.159136+00:00"
    key, body = _snapshot(cutoff)
    result = verify_fonbet_snapshot_evidence(
        raw_key=key, body=body, source_event_id="123", snapshot_at=datetime.fromisoformat(cutoff),
        expected_score="0-0", expected_state={"score": "0-0"}, state_parser=_parse,
    )
    assert result["verified"] is True


def test_late_source_is_rejected():
    key, body = _snapshot("2026-10-13T10:51:14.159137+00:00")
    result = verify_fonbet_snapshot_evidence(
        raw_key=key, body=body, source_event_id="123",
        snapshot_at=datetime.fromisoformat("2026-10-13T10:51:14.159136+00:00"),
        expected_score="0-0", expected_state={"score": "0-0"}, state_parser=_parse,
    )
    assert result == {"verified": False, "reason": "source_after_snapshot"}


def test_wrong_key_hash_event_or_state_is_rejected():
    key, body = _snapshot()
    common = {"raw_key": key, "body": body, "snapshot_at": datetime.fromisoformat("2026-10-13T10:52:00+00:00"),
              "expected_score": "0-0", "expected_state": {"score": "0-0"}, "state_parser": _parse}
    assert verify_fonbet_snapshot_evidence(**{**common, "source_event_id": "missing"})["reason"] == "source_event_missing_or_ambiguous"
    assert verify_fonbet_snapshot_evidence(**{**common, "source_event_id": "123", "expected_state": {"score": "1-0"}})["reason"] == "state_mismatch"
    assert verify_fonbet_snapshot_evidence(**{**common, "source_event_id": "123", "raw_key": "wrong.json"})["reason"] == "object_key_hash_mismatch"


def test_duplicate_event_rows_are_ambiguous():
    key, body = _snapshot()
    doc = json.loads(body)
    doc["events"].append(doc["events"][0])
    payload = {name: doc[name] for name in ("source", "sport_code", "url", "events")}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    doc["content_hash"] = f"sha256:{digest}"
    key = f"fonbet/tennis/2026-10-13/{digest[:4]}/{digest}.json"
    result = verify_fonbet_snapshot_evidence(
        raw_key=key, body=json.dumps(doc, sort_keys=True).encode(), source_event_id="123",
        snapshot_at=datetime.fromisoformat("2026-10-13T10:52:00+00:00"), expected_score="0-0",
        expected_state={"score": "0-0"}, state_parser=_parse,
    )
    assert result["reason"] == "source_event_missing_or_ambiguous"
