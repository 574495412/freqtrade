__alpha_meta__ = {'id': 'core_flow_cvd_20', 'nickname': 'Native flow cvd_20', 'theme': ['microstructure'], 'formula_latex': 'cvd_20', 'columns_required': ['close'], 'extras_required': ['native_cvd_20'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 4, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Isolated local diagnostic view of exact author kernel computed inside native IStrategy.20completed1h bars,decision then1h native execution delay. A local direction hypothesis,not author strategy,accepted alpha or funded account.'}

def compute(panel):
    return panel['native_cvd_20'].copy()
