"""Version tags are semantic versions, and tests use test_v1.0.0."""

from app.versioning import allocate_test_version, next_release_version


def test_first_release_is_v1():
    assert next_release_version(["v1.0.0_gb_20261013_164130_00b2"], "patch") == "v1.0.0"
    assert next_release_version([], "minor") == "v1.0.0"


def test_patch_minor_and_major_bumps():
    tags = ["v1.0.0", "v1.0.2", "v1.2.0"]
    assert next_release_version(tags, "patch") == "v1.2.1"
    assert next_release_version(tags, "minor") == "v1.3.0"
    assert next_release_version(tags, "major") == "v2.0.0"


def test_canonical_test_tag():
    assert allocate_test_version([]) == "test_v1.0.0"
    assert allocate_test_version(["test_v1.0.0"]) == "test_v1.0.0_2"
    assert allocate_test_version(["test_v1.0.0", "test_v1.0.0_2"]) == "test_v1.0.0_3"
