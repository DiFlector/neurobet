"""Fixed match windows for gate search and the later model comparison.

The newest finished matches are the gate final. The next unused hundred is kept
for the model comparison. Gate parameters are chosen only on the window before
those two. Matches already used to choose ``tennis_v3.0.0`` stay out of search
and training.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

GATE_FINAL_COUNT = 100
MODEL_FINAL_COUNT = 100
SEARCH_COUNT = 200


def split_match_ids(newest_first: Sequence[str], blocked: Iterable[str] = ()) -> Dict[str, List[str]]:
    """Split whole matches. ``newest_first`` is already ordered by start time."""
    ordered = [str(item) for item in newest_first]
    if len(ordered) != len(set(ordered)):
        raise ValueError("match ids must be unique")
    held = {str(item) for item in blocked}
    gate_final = ordered[:GATE_FINAL_COUNT]
    remainder = [item for item in ordered[GATE_FINAL_COUNT:] if item not in held]
    model_final = remainder[:MODEL_FINAL_COUNT]
    search = remainder[MODEL_FINAL_COUNT : MODEL_FINAL_COUNT + SEARCH_COUNT]
    train = remainder[MODEL_FINAL_COUNT + SEARCH_COUNT :]
    windows = {
        "gate_final": gate_final,
        "model_final": model_final,
        "search": search,
        "train": train,
        "blocked": [item for item in ordered if item in held and item not in gate_final],
    }
    _assert_disjoint(windows)
    return windows


def _assert_disjoint(windows: Dict[str, List[str]]) -> None:
    seen = {}
    for name, items in windows.items():
        for item in items:
            if item in seen:
                raise ValueError(f"{item} is in both {seen[item]} and {name}")
            seen[item] = name
