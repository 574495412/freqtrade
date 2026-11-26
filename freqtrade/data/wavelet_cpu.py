"""Local cold-CPU wavelet forecast feature, not an upstream Freqtrade strategy.

Reference: nateemma/strategies 9bb7dab5, GPL-3.0. Implements the selected
TS_Wavelet_DWT numerical path; native strategy/risk/wallet ownership is separate.
No external model/scaler files are read and no trading account is simulated.
"""

import numpy as np
import pandas as pd
import pywt
from lightgbm import LGBMRegressor
from sklearn.multioutput import MultiOutputRegressor
from sklearn.preprocessing import RobustScaler


def normalized_gains(close):
    """Actual source sixbar percent gain and rolling128 robust normalization."""
    gain = (100 * (close - close.shift(6)) / close.shift(6)).fillna(0).round(4)
    median = gain.rolling(128, min_periods=1).median()
    q75 = gain.rolling(128, min_periods=1).quantile(0.75)
    q25 = gain.rolling(128, min_periods=1).quantile(0.25)
    normalized = ((gain - median) / (q75 - q25).clip(lower=1e-6)).clip(-10, 10)
    return gain, normalized


def coefficient_table(normalized):
    """Source bior3.9/symmetric/level2 transform, including boundary behavior."""
    values = np.asarray(normalized, dtype=float)
    if len(values) < 64:
        return np.zeros((len(values), 1)), None
    wavelet = pywt.Wavelet("bior3.9")
    features = []
    slices = None
    for window in np.lib.stride_tricks.sliding_window_view(values, 64):
        coeffs = pywt.wavedec(window.copy(), wavelet, mode="symmetric", level=2)
        flattened, slices = pywt.coeffs_to_array(coeffs)
        features.append(np.array(flattened))
    array = np.asarray(features)
    result = np.zeros((len(values), array.shape[1]), dtype=float)
    result[63:] = array
    return np.nan_to_num(result), slices


def forecast(close):
    """Frozen CPU/no-saved-scaler forecast indexed by candle OPEN.

    known_at denotes the completed-bar simulation clock, not observed receipt
    time. The source class scale_results=False is retained despite the separate
    StrategyConfig field being True. Entry/exit and risk policy are not defined.
    """
    if not isinstance(close, pd.Series) or close.empty:
        raise ValueError("A nonempty15minute close Series is required")
    ix = close.index
    if not isinstance(ix, pd.DatetimeIndex) or str(ix.tz) != "UTC":
        raise ValueError("UTC candle OPEN timestamps required")
    expected = pd.date_range(ix[0], ix[-1], freq="15min").as_unit("ns")
    if not ix.as_unit("ns").equals(expected):
        raise ValueError("Complete contiguous15minute history required")
    if not np.isfinite(close.to_numpy()).all() or not close.gt(0).all():
        raise ValueError("Finite positive closes required; no imputation")
    gain, normalized = normalized_gains(close)
    coefficients, slices = coefficient_table(normalized.to_numpy())
    predictions = np.zeros(len(close))
    training = []
    for start in range(1000, len(close), 2500):
        train_end = start - 6
        stop = min(start + 2500, len(close))
        x = coefficients[:train_end]
        y = coefficients[6 : train_end + 6]
        scaler = RobustScaler()
        scaled_x = scaler.fit_transform(x)
        scaled_prediction = scaler.transform(coefficients[start:stop])
        model = MultiOutputRegressor(LGBMRegressor(n_estimators=100, random_state=42))
        model.fit(scaled_x, y)
        predicted_coeffs = model.predict(scaled_prediction)
        if not np.isfinite(predicted_coeffs).all():
            raise ValueError("Source CPU model produced nonfinite coefficients")
        for offset, row in enumerate(predicted_coeffs):
            restored = pywt.array_to_coeffs(row, slices, output_format="wavedec")
            values = pywt.waverec(restored, pywt.Wavelet("bior3.9"), mode="symmetric")
            predictions[start + offset] = values[-1]
        training.append(
            {
                "first_prediction": ix[start],
                "last_prediction": ix[stop - 1],
                "latest_feature": ix[train_end - 1],
                "latest_label": ix[start - 1],
                "train_rows": train_end,
                "coefficient_columns": coefficients.shape[1],
            }
        )
    predictions = np.nan_to_num(predictions).clip(-3, 3)
    result = pd.DataFrame(
        {
            "source_gain": gain,
            "normalized_gain": normalized,
            "source_predicted_gain": predictions,
            "model_ready": np.arange(len(close)) >= 1000,
            "known_at": ix + pd.Timedelta(minutes=15),
        },
        index=ix,
    )
    return result, training
