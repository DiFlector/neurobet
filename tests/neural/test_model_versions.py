"""Version tags name the sport family: tennis_v1.0.0, tests use test_tennis_v1.0.0."""

from app.versioning import allocate_test_version, next_release_version


def test_first_release_is_sport_v1():
    assert next_release_version(["v1.0.0_gb_20261013_164130_00b2"], "patch", "tennis") == "tennis_v1.0.0"
    assert next_release_version([], "minor", "tennis") == "tennis_v1.0.0"
    assert next_release_version(["tennis_v1.0.0"], "patch", "hockey") == "hockey_v1.0.0"


def test_patch_minor_and_major_bumps():
    tags = ["tennis_v1.0.0", "tennis_v1.0.2", "tennis_v1.2.0", "v1.9.9"]
    assert next_release_version(tags, "patch", "tennis") == "tennis_v1.2.1"
    assert next_release_version(tags, "minor", "tennis") == "tennis_v1.3.0"
    assert next_release_version(tags, "major", "tennis") == "tennis_v2.0.0"


def test_canonical_test_tag():
    assert allocate_test_version([], "tennis") == "test_tennis_v1.0.0"
    assert allocate_test_version(["test_tennis_v1.0.0"], "tennis") == "test_tennis_v1.0.0_2"
    assert allocate_test_version(["test_tennis_v1.0.0", "test_tennis_v1.0.0_2"], "tennis") == "test_tennis_v1.0.0_3"


if __name__ == "__main__":
    test_first_release_is_sport_v1()
    test_patch_minor_and_major_bumps()
    test_canonical_test_tag()
    print("ALL VERSION TESTS OK")
