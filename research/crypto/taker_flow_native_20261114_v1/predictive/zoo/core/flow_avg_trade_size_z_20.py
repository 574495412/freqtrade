__alpha_meta__ = {'id': 'core_flow_avg_trade_size_z_20', 'nickname': 'Native flow avg_trade_size_z_20', 'theme': ['microstructure'], 'formula_latex': 'avg_trade_size_z_20', 'columns_required': ['close'], 'extras_required': ['native_avg_trade_size_z_20'], 'universe': ['crypto'], 'frequency': ['1h'], 'decay_horizon': 4, 'min_warmup_bars': 0, 'stage': 'research', 'notes': 'Isolated local diagnostic view of exact author kernel computed inside native IStrategy.20completed1h bars,decision then1h native execution delay. A local direction hypothesis,not author strategy,accepted alpha or funded account.'}

def compute(panel):
    return panel['native_avg_trade_size_z_20'].copy()
