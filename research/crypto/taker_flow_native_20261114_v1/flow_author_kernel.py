import numpy as np
import pandas as pd

FLOW_WINDOWS = [5, 20, 50, 100]

def build_flow_features(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Order-flow features from Binance taker/volume candle fields.

    Captures who is aggressing (taker buys vs sells) — information OHLCV
    indicators cannot see. Strictly backward-looking rolling windows; returns
    {} when the flow columns are absent so callers can merge unconditionally.
    """
    required = {"volume", "taker_buy_base_volume", "number_of_trades"}
    if not required.issubset(df.columns):
        return {}
    volume = df["volume"].astype(float)
    taker_buy = df["taker_buy_base_volume"].astype(float)
    trades = df["number_of_trades"].astype(float)
    safe_volume = volume.replace(0, np.nan)
    # Net aggressive buy volume: buys minus sells, where sells = volume - buys.
    delta = 2.0 * taker_buy - volume
    imbalance = delta / safe_volume
    features: dict[str, pd.Series] = {
        "taker_buy_ratio": taker_buy / safe_volume,
        "taker_imbalance": imbalance,
        "avg_trade_size": volume / trades.replace(0, np.nan),
    }
    for window in FLOW_WINDOWS:
        rolling_volume = volume.rolling(window).sum().replace(0, np.nan)
        features[f"cvd_{window}"] = delta.rolling(window).sum() / rolling_volume
        features[f"taker_imbalance_ma_{window}"] = imbalance.rolling(window).mean()
        volume_std = volume.rolling(window).std().replace(0, np.nan)
        features[f"volume_z_{window}"] = (volume - volume.rolling(window).mean()) / volume_std
        trades_std = trades.rolling(window).std().replace(0, np.nan)
        features[f"trades_z_{window}"] = (trades - trades.rolling(window).mean()) / trades_std
        size = features["avg_trade_size"]
        size_std = size.rolling(window).std().replace(0, np.nan)
        features[f"avg_trade_size_z_{window}"] = (size - size.rolling(window).mean()) / size_std
    return features
