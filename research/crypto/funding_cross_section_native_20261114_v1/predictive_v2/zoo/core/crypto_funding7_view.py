__alpha_meta__ = {'id': 'core_crypto_funding7_view', 'nickname': 'Native funding7 input view', 'theme': ['leverage'], 'formula_latex': 'Rank(-Mean_7(Sum_actual_funding_events_in_completed_UTC_day))', 'columns_required': ['close'], 'extras_required': ['native_funding7'], 'universe': ['crypto'], 'frequency': ['1d'], 'decay_horizon': 7, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Local isolated diagnostic view;actual IStrategy input calculation owns7day support and missing event mask; no accepted factor/global Registry edit or funded account claim.'}

from src.factors.base import rank

def compute(panel):
    return rank(panel["native_funding7"])
