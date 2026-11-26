from typing import Dict
import numpy as np
import pandas as pd

class AuthorMath:
    lb_1m_days=30
    lb_3m_days=90
    lb_12m_days=365
    vol_lookback_days=30

    def compute_tsmom_signals(
        self,
        close_matrix: pd.DataFrame,
        bars_per_day: int,
        lag_periods: int = 0
    ) -> Dict[str, pd.DataFrame]:
        """
        Computes 1M, 3M, 12M directional signals and the composite equal-weighted signal.
        Lookbacks are strictly historical (no look-ahead bias).
        """
        w_1m = max(1, int(self.lb_1m_days * bars_per_day))
        w_3m = max(1, int(self.lb_3m_days * bars_per_day))
        w_12m = max(1, int(self.lb_12m_days * bars_per_day))
        
        # Past returns over horizons: R_{t-k, t} = Close_t / Close_{t-k} - 1
        ret_1m = close_matrix.pct_change(periods=w_1m, fill_method=None)
        ret_3m = close_matrix.pct_change(periods=w_3m, fill_method=None)
        ret_12m = close_matrix.pct_change(periods=w_12m, fill_method=None)
        
        # Directional sign signals: +1 if positive, -1 if negative, 0 if flat
        sig_1m = np.sign(ret_1m).fillna(0.0)
        sig_3m = np.sign(ret_3m).fillna(0.0)
        sig_12m = np.sign(ret_12m).fillna(0.0)
        
        # Handle cases where 12M or 3M may not have sufficient history yet:
        # Average across available lookback horizons with non-zero valid data
        valid_1m = (~ret_1m.isna()).astype(float)
        valid_3m = (~ret_3m.isna()).astype(float)
        valid_12m = (~ret_12m.isna()).astype(float)
        
        valid_sum = valid_1m + valid_3m + valid_12m
        raw_sum = sig_1m + sig_3m + sig_12m
        
        with np.errstate(divide='ignore', invalid='ignore'):
            composite_sig = np.where(valid_sum > 0, raw_sum / valid_sum, 0.0)
        
        composite_df = pd.DataFrame(composite_sig, index=close_matrix.index, columns=close_matrix.columns)
        
        if lag_periods > 0:
            sig_1m = sig_1m.shift(lag_periods).fillna(0.0)
            sig_3m = sig_3m.shift(lag_periods).fillna(0.0)
            sig_12m = sig_12m.shift(lag_periods).fillna(0.0)
            composite_df = composite_df.shift(lag_periods).fillna(0.0)
            
        return {
            '1M': sig_1m,
            '3M': sig_3m,
            '12M': sig_12m,
            'composite': composite_df
        }

    def compute_rolling_volatilities(
        self,
        close_matrix: pd.DataFrame,
        bars_per_day: int
    ) -> pd.DataFrame:
        """
        Computes rolling annualized return standard deviation for each asset.
        Epsilon-guarded against zero variance and NaNs.
        """
        w_vol = max(2, int(self.vol_lookback_days * bars_per_day))
        bars_per_year = 365.0 * bars_per_day
        
        bar_returns = close_matrix.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        roll_std = bar_returns.rolling(window=w_vol, min_periods=max(2, w_vol // 3)).std(ddof=1)
        ann_vol = (roll_std * np.sqrt(bars_per_year)).clip(lower=1e-4).fillna(1e-4)
        return ann_vol
