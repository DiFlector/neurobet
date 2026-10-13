"""Release and test version tags for a model line.

A model line is one sport, one model name, and one market.
The tag is only the version. The algorithm stays in model_name.
"""

import re
from typing import Iterable

RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
TEST_TAG = "test_v1.0.0"


def next_release_version(existing_tags: Iterable[str], bump: str) -> str:
    """Next vMAJOR.MINOR.PATCH for this line.

    Tags that are not exactly vMAJOR.MINOR.PATCH are legacy and are ignored.
    The first release tag of a line is v1.0.0.
    """
    parsed = []
    for tag in existing_tags:
        match = RELEASE_TAG.fullmatch(tag)
        if match:
            parsed.append(tuple(int(part) for part in match.groups()))

    if not parsed:
        return "v1.0.0"

    major, minor, patch = max(parsed)
    if bump == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"v{major}.{minor + 1}.0"
    if bump == "major":
        return f"v{major + 1}.0.0"
    raise ValueError("bump must be 'patch', 'minor', or 'major'")


def allocate_test_version(existing_tags: Iterable[str]) -> str:
    """Canonical test tag is test_v1.0.0. A numeric suffix is added only when that tag is taken."""
    taken = set(existing_tags)
    if TEST_TAG not in taken:
        return TEST_TAG
    index = 2
    while f"{TEST_TAG}_{index}" in taken:
        index += 1
    return f"{TEST_TAG}_{index}"
