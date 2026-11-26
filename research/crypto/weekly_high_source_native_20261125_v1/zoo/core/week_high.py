"""Cross-sectional distance to the completed week's high, in log price."""

import numpy as np


__alpha_meta__ = {
    "id": "core_crypto_week_high_distance",
    "nickname": "Crypto weekly high distance",
    "theme": ["momentum"],
    "formula_latex": r"\ln(C_t)-\ln(H_{t,1})",
    "columns_required": ["close", "high"],
    "universe": ["crypto"],
    "frequency": ["1w"],
    "decay_horizon": 7,
    "min_warmup_bars": 0,
    "stage": "research",
}


def compute(panel):
    return np.log(panel["close"]) - np.log(panel["high"])
