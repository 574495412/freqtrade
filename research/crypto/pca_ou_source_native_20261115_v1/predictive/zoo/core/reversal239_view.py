__alpha_meta__ = {'id': 'core_native_reversal239_view', 'nickname': 'PCA OU reversal239 diagnostic', 'theme': ['momentum'], 'formula_latex': 'reversal239', 'columns_required': ['close'], 'extras_required': ['native_reversal239'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 1, 'min_warmup_bars': 240, 'stage': 'research', 'notes': 'Isolated native diagnostic view,not global acceptedfactor. Metadata onebar;explicit1h/24hforwardlabels are not a validated holding period. Fixed40localuniverse,source2PC240price239logreturn OU math,not source account or historicalmarketcap replication.'}

def compute(panel):
    return panel['native_reversal239'].copy()
