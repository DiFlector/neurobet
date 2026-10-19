"""Unit tests for chronological_folds with temporal embargo."""

from app.dataset import chronological_folds


def test_chronological_folds_without_embargo():
    mode, folds = chronological_folds(20, n_splits=4, embargo_matches=0)
    assert mode == "walk_forward"
    assert len(folds) == 4
    for train_index, test_index in folds:
        assert set(train_index).isdisjoint(test_index)
        assert max(train_index) < min(test_index)
        assert min(test_index) - max(train_index) == 1


def test_chronological_folds_with_embargo():
    embargo = 2
    mode, folds = chronological_folds(25, n_splits=4, embargo_matches=embargo)
    assert mode == "walk_forward"
    assert len(folds) == 4
    for train_index, test_index in folds:
        assert set(train_index).isdisjoint(test_index)
        # Gap between the last training match and first test match must be at least embargo
        gap = min(test_index) - max(train_index)
        assert gap > embargo


def test_chronological_folds_holdout_with_embargo():
    mode, folds = chronological_folds(6, n_splits=4, embargo_matches=1)
    assert mode == "holdout"
    train_index, test_index = folds[0]
    assert set(train_index).isdisjoint(test_index)
    assert max(train_index) < min(test_index)


if __name__ == "__main__":
    test_chronological_folds_without_embargo()
    test_chronological_folds_with_embargo()
    test_chronological_folds_holdout_with_embargo()
    print("test_purged_folds: ALL PASSED")
