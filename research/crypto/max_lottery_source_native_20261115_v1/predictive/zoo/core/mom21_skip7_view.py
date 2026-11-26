__alpha_meta__ = {'id': 'core_native_mom21_skip7_view', 'nickname': 'Native MAX source mom21_skip7 diagnostic', 'theme': ['momentum'], 'formula_latex': 'mom21_skip7', 'columns_required': ['close'], 'extras_required': ['native_mom21_skip7'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Isolated native Registry view of actual Freqtrade IStrategy/DataProvider values. MAX28positive/source footnote9; local perpetual/simple-return adaptation. Not upstream Freqtrade factor library or new validated alpha.'}

def compute(panel):
    return panel['native_mom21_skip7'].copy()
