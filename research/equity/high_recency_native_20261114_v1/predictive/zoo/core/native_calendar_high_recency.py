__alpha_meta__ = {'id': 'core_native_calendar_high_recency', 'nickname': 'Native quote-price high recency', 'theme': ['momentum'], 'formula_latex': '1 - days_since_latest_52week_high / 364', 'columns_required': ['close'], 'extras_required': ['native_recency'], 'universe': ['equity_us', 'equity_hk'], 'frequency': ['1d'], 'decay_horizon': 21, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Isolated view of actual Freqtrade IStrategy output; quote-price local hypothesis, not corporate-action-adjusted source PEAD or a funded return.'}

def compute(panel):
    return panel["native_recency"].copy()
