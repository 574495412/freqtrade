__alpha_meta__ = {'id': 'core_native_lowvol28_view', 'nickname': 'Native MAX source lowvol28 diagnostic', 'theme': ['volatility'], 'formula_latex': 'lowvol28', 'columns_required': ['close'], 'extras_required': ['native_lowvol28'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Isolated native Registry view of actual Freqtrade IStrategy/DataProvider values. MAX28positive/source footnote9; local perpetual/simple-return adaptation. Not upstream Freqtrade factor library or new validated alpha.'}

def compute(panel):
    return panel['native_lowvol28'].copy()
