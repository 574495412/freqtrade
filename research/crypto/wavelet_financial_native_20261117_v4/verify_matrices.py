"""Reconstruct real source CPU training matrices without importing its calculator.

No sklearn predictor/scaler, strategy projector, Freqtrade engine or account is
imported. Numerical definitions are independently evaluated with NumPy/PyWavelets
from the sealed raw candle inputs. This is a matrix/clock audit, not return proof.
"""

# ruff: noqa: S101
import argparse
import hashlib
import json
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
FEATURE = HERE.with_name("wavelet_feature_native_20261117_v2")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_digest(values):
    array = np.ascontiguousarray(values)
    prefix = str((array.shape, str(array.dtype))).encode()
    return hashlib.sha256(prefix + array.tobytes()).hexdigest()


def checked_wavelets():
    manifest = read(FEATURE / "dependency_manifest.json")
    for relative, digest in manifest["files"].items():
        assert sha(FEATURE / "dependencies" / relative) == digest, relative
    sys.path.insert(0, str(FEATURE / "dependencies"))
    import pywt

    assert pywt.__version__ == "1.8.0"
    assert Path(pywt.__file__).parent == FEATURE / "dependencies/pywt"
    return pywt


def independent_coefficients(close, pywt):
    # Percent changes include the source's IEEE arithmetic and decimal rounding.
    gains = (100 * (close - close.shift(6)) / close.shift(6)).fillna(0).round(4)
    trailing = gains.rolling(window=128, min_periods=1)
    median = trailing.median()
    spread = (trailing.quantile(0.75) - trailing.quantile(0.25)).clip(lower=1e-6)
    values = ((gains - median) / spread).clip(lower=-10, upper=10).to_numpy(dtype="float64")
    rows = []
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("once")
        for end in range(64, len(values) + 1):
            coefficients = pywt.wavedec(
                values[end - 64 : end].copy(), "bior3.9", mode="symmetric", level=2
            )
            flattened, _ = pywt.coeffs_to_array(coefficients)
            rows.append(flattened)
    tail = np.asarray(rows, dtype="float64")
    assert tail.shape[1] == 101
    table = np.zeros((len(values), 101), dtype="float64")
    table[63:] = tail
    assert np.isfinite(table).all()
    return table, sorted({str(w.message) for w in captured})


def independently_scaled(x):
    # RobustScaler1.9.1 default median/IQR arithmetic; no sklearn import.
    median = np.nanmedian(x, axis=0)
    limits = np.stack([np.nanpercentile(column, [25, 75]) for column in x.T], axis=1)
    scale = limits[1] - limits[0]
    scale[scale < 10 * np.finfo(scale.dtype).eps] = 1.0
    result = x.copy()
    result -= median
    result /= scale
    return result


def reconstruct(plan, asset, events, pywt):
    maximum = max(e["rows"] + 6 for e in events)
    source = Path(plan["datadir"]) / (asset + "_USDT-15m.feather")
    receipt = read(plan["data_receipts"]["path"])
    assert sha(source) == receipt[source.name]
    raw = pd.read_feather(source)
    beginning = pd.Timestamp(plan["start"]) - pd.Timedelta(minutes=15 * 128)
    data = raw.loc[raw.date.ge(beginning) & raw.date.lt(pd.Timestamp(plan["end"]))].reset_index(
        drop=True
    )
    assert data.date.iloc[0] == beginning
    expected = pd.date_range(beginning, data.date.iloc[-1], freq="15min")
    assert pd.DatetimeIndex(data.date).as_unit("ns").equals(expected.as_unit("ns"))
    assert len(data) >= maximum
    coefficients, cautions = independent_coefficients(data.close.iloc[:maximum], pywt)
    results = []
    for event in events:
        n = event["rows"]
        assert data.date.iloc[n + 5] == pd.Timestamp(event["label_last"])
        assert data.date.iloc[n + 6] == pd.Timestamp(event["first_prediction"])
        assert (n + 6 - 1000) % 2500 == 0
        x = independently_scaled(coefficients[:n])
        y = coefficients[6 : n + 6]
        observed_x, observed_y = array_digest(x), array_digest(y)
        assert observed_x == event["x_sha256"], ("X", asset, n, observed_x, event["x_sha256"])
        assert observed_y == event["y_sha256"], ("Y", asset, n, observed_y, event["y_sha256"])
        results.append(
            {
                "asset": asset,
                "rows": n,
                "columns": 101,
                "x_sha256": observed_x,
                "y_sha256": observed_y,
                "source_matrices_exactly_equal": True,
                "latest_label_open": event["label_last"],
                "latest_label_complete": str(data.date.iloc[n + 5] + pd.Timedelta(minutes=15)),
                "first_prediction_bar_complete": str(
                    data.date.iloc[n + 6] + pd.Timedelta(minutes=15)
                ),
            }
        )
    return {
        "asset": asset,
        "input": str(source),
        "input_sha256": sha(source),
        "native_source_beginning": str(beginning),
        "matrix_cases": results,
        "warnings": cautions,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--cache-all", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if bool(args.snapshot) == bool(args.cache_all):
        raise ValueError("Select exactly one input scope")
    assert not args.output.exists(), "Preserve earlier matrix audit"
    plan = read(HERE / "plan_attempt2.json")
    groups = {}
    if args.snapshot:
        event = read(args.snapshot)["model_progress"]
        assert "x_sha256" in event and "y_sha256" in event
        groups[event["pair"].split("/")[0]] = [event]
    else:
        config = read(plan["candidates"][0]["config"]["path"])
        cache = Path(config["research_feature_cache"])
        for asset in ["BTC", "ETH"]:
            receipt = read(cache / (asset + "_USDT.json"))
            groups[asset] = [
                e for e in receipt["model_events"] if e["stage"] == "source_model_fit_complete"
            ]
    pywt = checked_wavelets()
    records = [reconstruct(plan, asset, events, pywt) for asset, events in groups.items()]
    result = {
        "passed": True,
        "verified_at": datetime.now(UTC).isoformat(),
        "scope": "All completed source matrices" if args.cache_all else "One captured live matrix",
        "plan_sha256": sha(HERE / "plan_attempt2.json"),
        "records": records,
        "candidate_calculator_scaler_or_model_imported": False,
        "financial_account_profitability_proven": False,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "matrix_cases": sum(len(r["matrix_cases"]) for r in records),
                "financial_account_profitability_proven": False,
            }
        )
    )


if __name__ == "__main__":
    main()
