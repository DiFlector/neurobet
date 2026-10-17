"""Training tape: one full build, then batches of at least ten new matches."""

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.dataset import MatchSnapshots
from app.dataset_cache import append_store, ids_to_build, read_event_matches, read_store


def _match(event_id: str, minute: int) -> MatchSnapshots:
    moment = datetime(2026, 10, 17, 12, minute, tzinfo=timezone.utc)
    return MatchSnapshots(
        event_id=event_id,
        started_at=moment,
        label=1,
        rows=[[float(minute), 0.5]],
        observed_at=[moment],
        row_labels=[1],
        row_markets=["match_winner"],
    )


def test_batch_waits_for_ten_and_keeps_a_larger_backlog():
    assert ids_to_build(["a"], [], [], store_exists=False) == ["a"]
    saved = [str(index) for index in range(9)]
    assert ids_to_build(saved, [], [], store_exists=True) == []
    ten = [str(index) for index in range(10)]
    assert ids_to_build(ten, [], [], store_exists=True) == ten
    fifteen = [str(index) for index in range(15)]
    assert ids_to_build(fifteen, [], [], store_exists=True) == fifteen
    assert ids_to_build(["old", "new"], ["old"], [], store_exists=True) == []


def test_append_keeps_start_order(tmp_path=None):
    root = tmp_path or Path("/tmp/neurobet-dataset-cache-test")
    os.environ["NEUROBET_DATASET_CACHE"] = str(root)
    later = _match("later", 30)
    earlier = _match("earlier", 5)
    append_store("tennis", "1.1.0", [later], ["odds_a", "prob_a"], [])
    append_store("tennis", "1.1.0", [earlier], ["odds_a", "prob_a"], ["skipped"])
    matches, names = read_store("tennis", "1.1.0")
    assert names == ["odds_a", "prob_a"]
    assert [match.event_id for match in matches] == ["earlier", "later"]
    assert np.allclose(matches[0].rows[0], [5.0, 0.5])
    chosen, chosen_names = read_event_matches("tennis", "1.1.0", ["later"])
    assert chosen_names == ["odds_a", "prob_a"]
    assert [match.event_id for match in chosen] == ["later"]
    assert np.allclose(chosen[0].rows[0], [30.0, 0.5])
    assert chosen[0].row_markets == ["match_winner"]


if __name__ == "__main__":
    os.environ["NEUROBET_DATASET_CACHE"] = "/tmp/neurobet-dataset-cache-test"
    path = Path("/tmp/neurobet-dataset-cache-test")
    if path.exists():
        for child in path.rglob("*"):
            if child.is_file():
                child.unlink()
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
    path.mkdir(parents=True, exist_ok=True)
    test_batch_waits_for_ten_and_keeps_a_larger_backlog()
    test_append_keeps_start_order(path)
    print("test_dataset_cache: OK")
