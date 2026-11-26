"""Isolated nominal-quote source adaptation, not new accepted alpha."""

from freqtrade.data.equity_overnight import quote_overnight126


__alpha_meta__ = {
    "id": "local_quote_overnight126",
    "nickname": "条件性126日隔夜报价动量",
    "theme": ["momentum"],
    "formula_latex": r"\prod_{j=0}^{125} O_{t-j}/C_{t-j-1}-1",
    "columns_required": ["open", "close", "volume"],
    "universe": ["equity_us"],
    "frequency": ["1d"],
    "min_warmup_bars": 131,
    "decay_horizon": 1,
    "stage": "research",
    "notes": (
        "Source126-session positive ratio and USD10M 20-session liquidity. "
        "Nominal quotes, not source adjusted OHLC. Complete session loader and "
        "after-close callbacks required; no account or historical availability certification."
    ),
}


def compute(panel):
    return quote_overnight126(panel)
