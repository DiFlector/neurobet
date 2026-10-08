from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Any, Optional
import numpy as np


class ChronologicalSplitError(ValueError):
    """Raised when temporal causality or chronological ordering is violated."""
    pass


class WalkForwardSplitter:
    """
    Chronological Walk-Forward Cross-Validation splitter with purge/embargo support.
    
    Strictly forbids random splitting. Enforces:
    max(train_timestamps) + purge_window <= min(test_timestamps)
    """

    def __init__(
        self,
        n_splits: int = 4,
        split_mode: str = "EXPANDING",  # "EXPANDING" or "ROLLING"
        purge_window_sec: float = 300.0,
    ):
        if n_splits < 2:
            raise ValueError(f"n_splits must be at least 2, got {n_splits}")
        self.n_splits = n_splits
        self.split_mode = split_mode.upper()
        self.purge_window_sec = float(purge_window_sec)

    def split(
        self,
        timestamps: List[datetime],
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate (train_indices, test_indices) splits based on timestamps.
        
        Args:
            timestamps: list of datetime objects corresponding to each sample.
            
        Returns:
            List of (train_idx, test_idx) numpy arrays.
        """
        n_samples = len(timestamps)
        if n_samples < (self.n_splits + 1) * 2:
            raise ValueError(f"Not enough samples ({n_samples}) for {self.n_splits} splits.")

        # Convert timestamps to float seconds for fast chronological sorting and comparison
        time_floats = np.array([ts.timestamp() for ts in timestamps], dtype=np.float64)

        # Sort order check / verification
        sort_idx = np.argsort(time_floats)
        sorted_times = time_floats[sort_idx]

        # Divide chronological timeline into (n_splits + 1) chunks
        # Chunk 0: initial training warmup
        # Chunk 1..n_splits: sequential test periods
        chunk_size = n_samples // (self.n_splits + 1)
        if chunk_size < 1:
            raise ValueError("Chunk size too small for chronological splitting.")

        splits = []
        purge_delta = self.purge_window_sec

        for fold in range(self.n_splits):
            test_start_pos = (fold + 1) * chunk_size
            test_end_pos = (fold + 2) * chunk_size if fold < self.n_splits - 1 else n_samples

            test_indices_sorted = sort_idx[test_start_pos:test_end_pos]
            test_start_time = sorted_times[test_start_pos]

            if self.split_mode == "ROLLING":
                train_start_pos = fold * chunk_size
                candidate_train = sort_idx[train_start_pos:test_start_pos]
            else:  # EXPANDING
                candidate_train = sort_idx[0:test_start_pos]

            # Apply Purge: filter out samples in candidate_train whose timestamp is within purge_delta of test_start_time
            valid_train = []
            for idx in candidate_train:
                t = time_floats[idx]
                if t + purge_delta <= test_start_time:
                    valid_train.append(idx)

            train_indices = np.array(valid_train, dtype=np.int64)
            test_indices = np.array(test_indices_sorted, dtype=np.int64)

            if len(train_indices) == 0:
                raise ChronologicalSplitError(
                    f"Fold {fold}: purge window {purge_delta}s removed all training samples! "
                    f"Reduce purge_window_sec or increase sample density."
                )

            # Strict verification of zero data leakage
            self.validate_no_leakage(train_indices, test_indices, time_floats)
            splits.append((train_indices, test_indices))

        return splits

    def validate_no_leakage(
        self,
        train_idx: np.ndarray,
        test_idx: np.ndarray,
        time_floats: np.ndarray,
    ) -> None:
        """
        Verify that no test indices exist in train, and no train sample has a timestamp
        greater than (or within purge window of) any test sample.
        """
        overlap = np.intersect1d(train_idx, test_idx)
        if len(overlap) > 0:
            raise ChronologicalSplitError(
                f"Data Leakage detected: {len(overlap)} samples present in both train and test sets!"
            )

        max_train_t = np.max(time_floats[train_idx])
        min_test_t = np.min(time_floats[test_idx])

        if max_train_t > min_test_t:
            raise ChronologicalSplitError(
                f"Lookahead violation: max_train_t ({max_train_t}) > min_test_t ({min_test_t})!"
            )

        if max_train_t + self.purge_window_sec > min_test_t + 1e-6:
            raise ChronologicalSplitError(
                f"Purge violation: train cutoff {max_train_t} + purge {self.purge_window_sec} > test start {min_test_t}!"
            )
