"""Release and test version tags for a model line.

A model line is one sport, one model name, and one market.
The tag names the sport family and the version: tennis_v1.0.0.
The algorithm stays in model_name.
"""

import re
from typing import Iterable

_SPORT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def release_tag_pattern(sport_code: str) -> re.Pattern[str]:
    sport = _sport_token(sport_code)
    return re.compile(rf"^{re.escape(sport)}_v(\d+)\.(\d+)\.(\d+)$")


def test_tag(sport_code: str) -> str:
    return f"test_{_sport_token(sport_code)}_v1.0.0"


def is_test_tag(version_tag: str) -> bool:
    """A test tag is for a check run. It is never a production model."""
    return str(version_tag or "").startswith("test_")


def _sport_token(sport_code: str) -> str:
    sport = (sport_code or "").strip().lower()
    if not _SPORT.fullmatch(sport):
        raise ValueError(f"sport_code must be a short latin name, got {sport_code!r}")
    return sport


def next_release_version(existing_tags: Iterable[str], bump: str, sport_code: str) -> str:
    """Next {sport}_vMAJOR.MINOR.PATCH for this line.

    Tags that do not match this sport's version are legacy and are ignored.
    The first release tag of a line is {sport}_v1.0.0.
    """
    pattern = release_tag_pattern(sport_code)
    sport = _sport_token(sport_code)
    parsed = []
    for tag in existing_tags:
        match = pattern.fullmatch(tag)
        if match:
            parsed.append(tuple(int(part) for part in match.groups()))

    if not parsed:
        return f"{sport}_v1.0.0"

    major, minor, patch = max(parsed)
    if bump == "patch":
        return f"{sport}_v{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{sport}_v{major}.{minor + 1}.0"
    if bump == "major":
        return f"{sport}_v{major + 1}.0.0"
    raise ValueError("bump must be 'patch', 'minor', or 'major'")


def allocate_test_version(existing_tags: Iterable[str], sport_code: str) -> str:
    """Canonical test tag is test_{sport}_v1.0.0. A numeric suffix is added only when that tag is taken."""
    canonical = test_tag(sport_code)
    taken = set(existing_tags)
    if canonical not in taken:
        return canonical
    index = 2
    while f"{canonical}_{index}" in taken:
        index += 1
    return f"{canonical}_{index}"
