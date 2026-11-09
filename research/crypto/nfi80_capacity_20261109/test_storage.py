# ruff: noqa: S101
"""Compressed native research storage must preserve values, mutation and iteration order."""

import numpy as np
import pandas as pd

from research.crypto.nfi80_capacity_20261109.compressed_storage import DiskMapping
from research.crypto.nfi_original_storage import DiskMapping as OriginalMapping


def test_compression_preserves_float_bits_and_native_mutation_semantics(tmp_path):
    data = pd.DataFrame({"value": [0.0, -0.0, np.nan, np.inf, 1.0000000000000002]})
    data["date"] = pd.date_range("2026-01-01", periods=len(data), freq="5min", tz="UTC")
    reference = OriginalMapping(tmp_path / "original", 1)
    compressed = DiskMapping(tmp_path / "compressed", 1)
    for mapping in (reference, compressed):
        mapping["first"] = data.copy(deep=True)
        mapping["second"] = data.copy(deep=True)
        mapping["first"].iloc[4, 0] = 3.141592653589793
        mapping["second"]  # Evicts and persists the mutated frame.
        mapping.flush()
    pd.testing.assert_frame_equal(reference["first"], compressed["first"], check_exact=True)
    assert (
        reference["first"].value.to_numpy().tobytes()
        == compressed["first"].value.to_numpy().tobytes()
    )
    assert list(reference) == list(compressed) == ["first", "second"]
    assert compressed.maximum_observed_cached == 1


def test_empty_frames_and_tuple_keys_roundtrip_without_new_numeric_policy(tmp_path):
    mapping = DiskMapping(tmp_path / "frames", 0)
    data = pd.DataFrame({"empty": pd.Series(dtype="float64")})
    key = ("BTC/USDT:USDT", "5m")
    mapping[key] = data
    pd.testing.assert_frame_equal(mapping[key], data, check_exact=True)
    assert list(mapping) == [key]
