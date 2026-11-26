"""Actual source-normalizer/DWT parity and input/availability contracts."""

# ruff: noqa: S101
# Test assertions validate the research extension against original source functions.

import importlib.util

import pytest

from research.crypto.wavelet_feature_native_20261117_v2.source_reference import (
    HERE,
    bootstrap,
    build_reference,
)


def native_module():
    bootstrap()
    path = HERE.parents[2] / "freqtrade/data/wavelet_cpu.py"
    spec = importlib.util.spec_from_file_location("local_native_wavelet_cpu", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_real_source_normalizer_and_dwt_exact(asset):
    libs = bootstrap()
    np, pd = libs["np"], libs["pd"]
    native = native_module()
    data = pd.read_parquet(HERE / (asset + "_15m.parquet")).iloc[:6000]
    owner = build_reference()
    frame = data.copy()
    frame["gain"] = (
        (100 * (frame.close - frame.close.shift(6)) / frame.close.shift(6)).fillna(0).round(4)
    )
    owner.set_data(frame)
    owner.build_coefficient_table(0, len(frame))
    gain, normalized = native.normalized_gains(data.close)
    np.testing.assert_array_equal(gain, frame.gain)
    np.testing.assert_array_equal(normalized, owner.data)
    table, slices = native.coefficient_table(normalized.to_numpy())
    np.testing.assert_array_equal(table, owner.coeff_table)
    assert slices == owner.wavelet.coeff_slices
    np.testing.assert_array_equal(table[:5800], native.coefficient_table(normalized.iloc[:5800])[0])
    changed = data.close.copy()
    changed.iloc[5800:] *= np.linspace(2, 10, 200)
    _, after = native.normalized_gains(changed)
    np.testing.assert_array_equal(normalized.iloc[:5800], after.iloc[:5800])


def test_native_input_and_short_history_contract():
    native = native_module()
    libs = bootstrap()
    pd, np = libs["pd"], libs["np"]
    close = pd.Series(
        np.arange(200.0) + 100,
        index=pd.date_range("2025-01-01", periods=200, freq="15min", tz="UTC"),
    )
    result, training = native.forecast(close)
    assert training == [] and result.source_predicted_gain.eq(0).all()
    assert not result.model_ready.any()
    pd.testing.assert_index_equal(
        pd.DatetimeIndex(result.known_at), close.index + pd.Timedelta(minutes=15), check_names=False
    )
    with pytest.raises(ValueError, match="contiguous"):
        native.forecast(close.drop(close.index[20]))
    bad = close.copy()
    bad.iloc[20] = np.nan
    with pytest.raises(ValueError, match="Finite"):
        native.forecast(bad)
    with pytest.raises(ValueError, match="UTC"):
        native.forecast(close.tz_localize(None))
