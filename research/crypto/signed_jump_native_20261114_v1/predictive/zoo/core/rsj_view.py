__alpha_meta__ = {'id': 'core_intraday_rsj_view', 'nickname': 'Native signedvariation negative rsj', 'theme': ['volatility'], 'formula_latex': '-rsj', 'columns_required': ['close'], 'extras_required': ['native_rsj'], 'universe': ['crypto'], 'frequency': ['1d'], 'decay_horizon': 1, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local isolated view of actual native IStrategy daily minuteVWAP feature; no globalRegistry edit, acceptedalpha, account or source51spot replication. Joint knowninput mask disclosed.'}

def compute(panel):
    return panel['native_rsj'].copy()
