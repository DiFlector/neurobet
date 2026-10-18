"""HistGradientBoosting ROI search stays on earlier matches and stops on a plateau."""

import sys
from datetime import datetime, timedelta, timezone

for path in (
    "/srv/neurobet/services/neural",
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/app",
):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.dataset import MatchSnapshots
from app.replay.hist_roi_search import (
    EPOCH_NAME,
    EPOCH_TAG,
    TUNED_NAME,
    TUNED_TAG,
    epoch_loop,
    epoch_neighbors,
    initial_hist_params,
    search_until_plateau,
    tuned_neighbors,
)


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _match(index: int, label: int):
    started = ORIGIN + timedelta(days=index)
    rows = [[float(label), 0.2 if label else -0.2, 0.0] for _ in range(3)]
    return MatchSnapshots(f"m{index}", started, label, rows, [started] * 3, [label] * 3, ["match_winner"] * 3)


def test_names_and_tags_do_not_replace_the_current_line():
    assert (TUNED_NAME, TUNED_TAG) != ("tennis_hist_gradient_roi", "tennis_v3.1.0")
    assert (EPOCH_NAME, EPOCH_TAG) != ("tennis_hist_gradient_roi", "tennis_v3.1.0")
    assert TUNED_TAG != EPOCH_TAG


def test_tuned_neighbors_change_one_setting_at_a_time():
    start = initial_hist_params()
    neighbors = tuned_neighbors(start)
    assert neighbors
    for candidate in neighbors:
        changed = [key for key, value in candidate.items() if value != start[key]]
        assert changed == [changed[0]]
    assert any(item["l2_regularization"] == 1.0 for item in neighbors)
    assert any(item["max_iter"] == 120 for item in neighbors)


def test_epoch_neighbors_leave_the_tree_count_to_early_stopping():
    neighbors = epoch_neighbors(initial_hist_params())
    assert neighbors
    assert all(item["max_iter"] == 80 for item in neighbors)
    assert all(item["max_depth"] == 4 for item in neighbors)
    assert any(item["min_samples_leaf"] == 50 for item in neighbors)


def test_search_adopts_the_improvement_and_then_stops():
    def evaluate(params):
        return {"log_loss": 0.5 if int(params["max_iter"]) == 80 else 0.4, "brier": 0.2, "reason": "fixed_iterations"}

    found = search_until_plateau(initial_hist_params(), evaluate, tuned_neighbors)
    assert found["params"]["max_iter"] != 80
    assert found["log_loss"] == 0.4
    assert len(found["visited"]) > 1


def test_epoch_loop_stops_before_the_ceiling():
    train = [_match(index, index % 2) for index in range(8)]
    validation = [_match(index, index % 2) for index in range(8, 12)]
    report = epoch_loop(train, validation, ["strength", "noise", "flat"], initial_hist_params())
    assert report["reason"] == "validation_plateau"
    assert 1 <= report["best_epoch"] < 100
    assert len(report["curve"]) == report["best_epoch"] + 8


if __name__ == "__main__":
    test_names_and_tags_do_not_replace_the_current_line()
    test_tuned_neighbors_change_one_setting_at_a_time()
    test_epoch_neighbors_leave_the_tree_count_to_early_stopping()
    test_search_adopts_the_improvement_and_then_stops()
    test_epoch_loop_stops_before_the_ceiling()
    print("test_hist_roi_search: OK")
