__alpha_meta__ = {'id': 'core_native_raw14_view', 'nickname': 'Native residual source raw14 input view', 'theme': ['momentum'], 'formula_latex': 'raw14', 'columns_required': ['close'], 'extras_required': ['native_raw14'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local pre-financial isolated view of actual native IStrategy/DataProvider scores; source720/336 windows and joint input admission owned by freqtrade.data.residual_momentum. Not new accepted alpha or upstream Freqtrade factor registry.'}

def compute(panel):
    return panel['native_raw14'].copy()
