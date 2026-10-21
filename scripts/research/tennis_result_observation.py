"""Small canonicalizers shared by tennis result-time audit tooling."""
from __future__ import annotations

import re


def score_signature(value: str | None) -> tuple[int, ...] | None:
    """Return ordered numeric tokens for an exact match/set-score comparison."""
    digits = tuple(int(item) for item in re.findall(r"\d+", str(value or "")))
    return digits if len(digits) >= 6 else None
