__alpha_meta__ = {'id': 'core_native_edge30_view', 'nickname': 'Native Freqtrade edge30 diagnostic view', 'theme': ['liquidity'], 'formula_latex': 'edge30', 'columns_required': ['close'], 'extras_required': ['native_edge30'], 'universe': ['crypto'], 'frequency': ['1d'], 'decay_horizon': 1, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local diagnostic view of actual native feature table. No global Registry mutation, new estimator, sign/window change, account engine or promotion. Underlying source/data warmup owned by frozen native strategy.'}

def compute(panel):
    return panel['native_edge30'].copy()
