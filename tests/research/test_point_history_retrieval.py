from datetime import datetime, timedelta, timezone

from app.point_history import (
    _is_live_observed_document,
    _point_by_point_set_tabs,
    _point_history_tab_missing_document,
)


class _Page:
    url = "https://www.flashscore.com/match/tennis/player-a-id/player-b-id/summary/?mid=AbCd1234"

    def content(self):
        return "<html><body>Summary only</body></html>"


class _Response:
    status = 200


def test_no_pbp_tab_archives_summary_without_fabricating_set_one():
    assert _point_by_point_set_tabs([], "AbCd1234") == {}

    document = _point_history_tab_missing_document(_Page(), _Response())

    assert document["set"] is None
    assert document["url"].endswith("/summary/?mid=AbCd1234")
    assert document["html"] == "<html><body>Summary only</body></html>"
    assert document["fetch_status"] == "point_history_tab_missing"
    assert document["collection_status"] == "PBP_TAB_NOT_PUBLISHED"
    assert document["set_tab_confirmed"] is False


def test_live_provenance_requires_fresh_snapshot_and_does_not_backdate():
    retrieved = datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)
    snapshot = retrieved - timedelta(seconds=30)

    assert _is_live_observed_document("live", snapshot, retrieved)
    assert not _is_live_observed_document("finished", snapshot, retrieved)
    assert not _is_live_observed_document("live", retrieved - timedelta(seconds=91), retrieved)
    assert not _is_live_observed_document("live", retrieved + timedelta(seconds=1), retrieved)
    assert not _is_live_observed_document("live", snapshot, retrieved, event_finished_at=retrieved)
