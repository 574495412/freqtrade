__alpha_meta__ = {'id': 'core_native_lowvol30_view', 'nickname': 'Native residual source lowvol30 input view', 'theme': ['volatility'], 'formula_latex': 'lowvol30', 'columns_required': ['close'], 'extras_required': ['native_lowvol30'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local pre-financial isolated view of actual native IStrategy/DataProvider scores; source720/336 windows and joint input admission owned by freqtrade.data.residual_momentum. Not new accepted alpha or upstream Freqtrade factor registry.'}

def compute(panel):
    return panel['native_lowvol30'].copy()
