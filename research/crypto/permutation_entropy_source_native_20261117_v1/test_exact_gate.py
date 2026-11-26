"""Exact source comparisons at the real rolling-median boundary, no tolerance."""

import numpy as np
import pandas as pd

from freqtrade.data.ordinal_entropy import entropy_gate
from research.crypto.permutation_entropy_source_native_20261117_v1.test_features import (
    prices,
    source_functions,
)


def test_full_history_source_entropy_and_gate_exactly_equal():
    data = prices()
    source = source_functions()
    expected = pd.DataFrame(
        {p: source["permutation_entropy"](data[p].to_numpy(), 3, 28) for p in data},
        index=data.index + pd.Timedelta(hours=1),
    )
    threshold = expected.shift(1).rolling(8760, min_periods=8760).median()
    gate = expected.le(threshold) & expected.notna() & threshold.notna()
    actual_entropy, actual_threshold, actual_gate = entropy_gate(data)
    np.testing.assert_array_equal(actual_entropy.to_numpy(), expected.to_numpy())
    pd.testing.assert_frame_equal(actual_threshold, threshold)
    pd.testing.assert_frame_equal(actual_gate, gate)
