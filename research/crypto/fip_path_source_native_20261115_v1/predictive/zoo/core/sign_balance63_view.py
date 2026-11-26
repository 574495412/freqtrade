__alpha_meta__ = {'id': 'core_native_sign_balance63_view', 'nickname': 'Native FIP source sign_balance63 diagnostic', 'theme': ['momentum'], 'formula_latex': 'sign_balance63', 'columns_required': ['close'], 'extras_required': ['native_sign_balance63'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local isolated Registry view of actual native IStrategy/DataProvider. Signed FIP=PRET*source_modified is local directional hypothesis; source original/modified are not unconditional directional alpha. Not source account reproduction or a validated factor.'}

def compute(panel):
    return panel['native_sign_balance63'].copy()
