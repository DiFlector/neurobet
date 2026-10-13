"""Search queue states shown in the scheduler and on the match card."""

from research_queue import display_status


def test_sources_win_over_a_finished_empty_run():
    assert display_status(True, "completed", 0) == "ready"


def test_running_search_is_in_progress():
    assert display_status(False, "running", 0) == "running"


def test_completed_without_sources_is_empty():
    assert display_status(False, "completed", 0) == "empty"


def test_missing_or_waiting_run_stays_queued():
    assert display_status(False, None, 0) == "queued"
    assert display_status(False, "queued", 0) == "queued"
