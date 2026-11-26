__alpha_meta__ = {'id': 'core_native_amihud30_view', 'nickname': 'Native Freqtrade amihud30 diagnostic view', 'theme': ['liquidity'], 'formula_latex': 'amihud30', 'columns_required': ['close'], 'extras_required': ['native_amihud30'], 'universe': ['crypto'], 'frequency': ['1d'], 'decay_horizon': 1, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local diagnostic view of actual native feature table. No global Registry mutation, new estimator, sign/window change, account engine or promotion. Underlying source/data warmup owned by frozen native strategy.'}

def compute(panel):
    return panel['native_amihud30'].copy()
