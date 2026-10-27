"""Out-of-core storage must retain all values, dtypes, order and mutations."""

import numpy as np
import pandas as pd

from research.crypto.nfi_original_storage import DiskMapping, disk_map_each


def frame():
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=3, tz="UTC"),
            "close": np.array([1.000000000000003, 2.3, np.nan], dtype="float64"),
            "tag": pd.Series([None, "original tag", np.nan], dtype=object),
            "flag": pd.Series([True, False, True], dtype=bool),
        },
        index=pd.RangeIndex(3),
    )


def test_lossless_eviction_and_in_place_mutation(tmp_path):
    mapping = DiskMapping(tmp_path / "cache", maximum_cached=1)
    expected = frame()
    mapping[("X", "5m")] = expected.copy(deep=True)
    mapping[("X", "5m")].loc[0, "tag"] = "native mutation"
    expected.loc[0, "tag"] = "native mutation"
    mapping[("Y", "5m")] = frame()
    pd.testing.assert_frame_equal(mapping[("X", "5m")], expected, check_exact=True)
    assert list(mapping) == [("X", "5m"), ("Y", "5m")]
    assert mapping.maximum_observed_cached == 1


def test_pair_local_operation_matches_whole_mapping_without_precision_change(tmp_path):
    data = {"X": frame(), "Y": frame()}

    def original(frames, suffix):
        return {pair: value.assign(tag=suffix) for pair, value in frames.items()}

    expected = original(data, "unchanged")
    actual = disk_map_each(original, data, tmp_path / "output", "unchanged")
    for pair in expected:
        pd.testing.assert_frame_equal(actual[pair], expected[pair], check_exact=True)
    assert list(actual) == list(expected)
