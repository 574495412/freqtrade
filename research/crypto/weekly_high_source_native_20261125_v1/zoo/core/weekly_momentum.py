"""Conventional completed weekly close-to-close momentum control."""

import numpy as np


__alpha_meta__ = {
    "id": "core_crypto_week_momentum_control",
    "nickname": "Crypto weekly momentum control",
    "theme": ["momentum"],
    "formula_latex": r"\ln(C_t)-\ln(C_{t-1})",
    "columns_required": ["close"],
    "extras_required": ["prior_week_close"],
    "universe": ["crypto"],
    "frequency": ["1w"],
    "decay_horizon": 7,
    "min_warmup_bars": 1,
    "stage": "research",
}


def compute(panel):
    return np.log(panel["close"]) - np.log(panel["prior_week_close"])
