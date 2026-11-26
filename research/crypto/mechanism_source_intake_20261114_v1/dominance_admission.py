"""Canonical full-universe admission and a synthetic stale-return source probe.

No source financial engine or account simulation is executed. Existing real
native history is inspected without substituting a token or dropping a name.
"""
import ast,hashlib,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    engine=Path('/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade');manifest=json.loads((engine.parent/'FREQTRADE_SOURCE.json').read_text())
    for p,h in manifest['files'].items():assert sha(engine/p)==h
    sys.path.insert(0,str(engine))
    from freqtrade.data.history import load_pair_history
    from freqtrade.enums import CandleType
    spec=json.loads((HERE/'sources/dominance/forward_validation/frozen_strategy2.json').read_text())
    directory=ROOT/'user_data/crypto_quant/pairs_cointegration_native_20261114_v1/candles';expected=json.loads((directory.parent/'data_receipts.json').read_text());records=[]
    for symbol in spec['symbols']:
        relative=f'{symbol}_USDT-1d.feather';present=(directory/relative).exists()
        if present:assert sha(directory/relative)==expected[relative]
        frame=load_pair_history(symbol+'/USDT','1d',directory,fill_up_missing=False,drop_incomplete=False,candle_type=CandleType.SPOT)
        if len(frame):
            selected=frame.loc[frame.date.ge(pd.Timestamp('2025-01-01T00:00Z'))&frame.date.lt(pd.Timestamp('2026-09-01T00:00Z'))];count=len(selected)
        else:count=0
        records.append({'symbol':symbol,'native_file_present':present,'original_window_observations':count,'source_frozen_forward_unavailable':symbol in spec['unavailable_forward_symbols'],'sha256':sha(directory/relative)if present else None})
    # Execute only the pinned pure feature function and its UTC-index helper.
    scope={'np':np,'pd':pd}
    for file,name in [('systematic_crypto/data.py','ensure_utc_index'),('strategy2_pipeline.py','build_s2_features')]:
        tree=ast.parse((HERE/'sources/dominance'/file).read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name==name);exec(compile(ast.Module(body=[node],type_ignores=[]),file,'exec'),scope)
    index=pd.date_range('2025-01-01',periods=45,tz='UTC');close=pd.DataFrame({'BTC':np.linspace(100,110,45),'ETH':np.linspace(10,9,45),'MATIC':np.ones(45)},index=index);notional=close*100
    close.loc[index[-5]:,'MATIC']=np.nan;notional.loc[index[-5]:,'MATIC']=np.nan
    features=scope['build_s2_features'](close,notional)
    assert features['btc_dom'].iloc[-5:].notna().all()
    assert features['alt_returns']['MATIC'].iloc[-5:].eq(0).all()
    assert features['basket_weights']['MATIC'].iloc[-5:].eq(.5).all()
    result={'scope':'Canonical11spot-symbol input availability; no historical strategy scores or accounts','frozen_source_symbols':list(spec['symbols']),'records':records,'current_native_cached_subset_only':True,'not_a_claim_no_other_history_exists':True,'complete_original_universe_admitted':False,'failed_symbol':'MATIC','source_notional_definition':'dailyclose times dailybasevolume; BTCshare in11source assets; not actualquotevolume or market-cap dominance','synthetic_probe':{'only_pure_feature_AST_executed':True,'missing_MATIC_input_cells':10,'missing_daily_rows':5,'BTCshare_still_finite':True,'MATIC_ffill_returns_zero':True,'equal_alt_weight_still_nonzero':True,'claim':'Missing source closes can become zero quoted returns after ffill; this is not observed tradability. No source money engine executed.'},'original_frozen_history_pickle_available_here':False,'new_registry':0,'factor_analysis_calls':0,'funded_accounts':0,'accepted_new_independent_alphas':0,'return_metrics':None,'no_posthoc_switch':'Do not select the source24-variant screened ETH/BNB/SOLwinner or renamePOL toMATIC to repair this canonical full-pool intake.'}
    (HERE/'dominance_input_admission.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k!='records'},indent=2))
if __name__=='__main__':main()
