from qualys_tracker.version_compare import (
    Ordering,
    compare_public_to_tenant,
    compare_versions,
    is_same_release,
    latest_version,
)


def test_equal_strings():
    assert compare_versions("4.9.4", "4.9.4") == Ordering.EQUAL


def test_simple_numeric_newer():
    assert compare_versions("4.9.3.0-34", "4.9.3.0-33") == Ordering.NEWER


def test_simple_numeric_older():
    assert compare_versions("1.44.5-9", "1.45.0-1") == Ordering.OLDER


def test_zero_padding_is_equal():
    assert compare_versions("4.9.4", "4.9.4.0") == Ordering.EQUAL


def test_nonzero_trailing_component_is_newer():
    assert compare_versions("4.9.4.1", "4.9.4") == Ordering.NEWER
    assert compare_versions("4.9.4", "4.9.4.1") == Ordering.OLDER


def test_multi_segment_build_numbers():
    assert compare_versions("2.12.0-12-101", "2.12.0-12-100") == Ordering.NEWER


def test_snapshot_suffix_is_unknown():
    assert compare_versions("2.33.0.0", "2.33.0.0-SNAPSHOT-1") == Ordering.UNKNOWN


def test_differing_alpha_tokens_are_unknown():
    assert compare_versions("1.0.0-RC", "1.0.0-BETA") == Ordering.UNKNOWN


def test_completely_different_schemes_are_unknown():
    assert compare_versions("1.44.5-9", "2.33.0.0-SNAPSHOT-1") in (Ordering.NEWER, Ordering.OLDER, Ordering.UNKNOWN)
    # First numeric token differs (1 vs 2) -- that alone is confidently orderable.
    assert compare_versions("1.44.5-9", "2.33.0.0-SNAPSHOT-1") == Ordering.OLDER


def test_latest_version_single_candidate():
    result = latest_version(["4.9.4"])
    assert result.confident
    assert result.version == "4.9.4"


def test_latest_version_tenant_equals_latest_public():
    result = latest_version(["4.9.4", "4.9.4"])
    assert result.confident
    assert result.version == "4.9.4"


def test_latest_version_picks_newer_of_several():
    result = latest_version(["4.9.2", "4.9.4", "4.9.3"])
    assert result.confident
    assert result.version == "4.9.4"


def test_latest_version_unknown_ordering_reported_not_guessed():
    result = latest_version(["2.33.0.0", "2.33.0.0-SNAPSHOT-1"])
    assert not result.confident
    assert result.version is None
    assert set(result.candidates) == {"2.33.0.0", "2.33.0.0-SNAPSHOT-1"}


def test_latest_version_empty_list():
    result = latest_version([])
    assert result.confident
    assert result.version is None


# --- Published-label granularity ----------------------------------------
# Qualys publishes "FIM 4.9.4"; the portal API reports "4.9.4.0-38".
# Those are the same release and must not read as tenant-ahead.


def test_tenant_version_extending_published_label_is_the_same_release():
    assert is_same_release("4.9.4", "4.9.4.0-38") is True
    assert compare_public_to_tenant("4.9.4", "4.9.4.0-38") == Ordering.EQUAL
    assert compare_public_to_tenant("1.45", "1.45.0-116") == Ordering.EQUAL


def test_published_label_newer_than_tenant_still_reads_as_newer():
    assert compare_public_to_tenant("4.9.4", "4.9.3.0-34") == Ordering.NEWER


def test_tenant_genuinely_ahead_of_published_label_still_reads_as_older():
    # ETM: tenant on 1.13.x, Qualys has only published 1.12.
    assert is_same_release("1.12", "1.13.0-260") is False
    assert compare_public_to_tenant("1.12", "1.13.0-260") == Ordering.OLDER


def test_bare_major_published_label_is_too_coarse_to_claim_a_match():
    assert is_same_release("2", "2.12.0-12-101") is False


def test_similar_but_different_component_is_not_the_same_release():
    assert is_same_release("4.9.4", "4.9.40-1") is False
