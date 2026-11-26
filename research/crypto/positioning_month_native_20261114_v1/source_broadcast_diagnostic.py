"""Run only inspected indicator assignment fragment with deterministic stubs.

No strategy constructor, API client, account, fees or source trading engine runs.
The fake RSI is not a numerical validation; only assignment clocks are tested.
"""
import ast,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
h=Path(__file__).resolve().parent;p=h/'source/coinversaa_coinversaa_smart_money_strategy.py'
tree=ast.parse(p.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
node=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='populate_indicators')
fragment=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
globals_={'DataFrame':pd.DataFrame,'ta':SimpleNamespace(RSI=lambda f,timeperiod:pd.Series(50,index=f.index))}
exec(compile(fragment,str(p),'exec'),globals_)
calls=[]
def get(coin):
 calls.append(coin);return {'smart_money_bias':'long','smart_money_score':.8,'whale_short_notional':55.}
obj=SimpleNamespace(_get_signals=get,_freqtrade_pair_to_coin=lambda pair:pair.split('/')[0])
raw=pd.DataFrame({'date':pd.to_datetime(['2024-01-01','2025-01-01','2026-08-31'],utc=True),'close':[1.,2.,3.]})
x=globals_['populate_indicators'](obj,raw.copy(),{'pair':'BTC/USDT:USDT'})
assert calls==['BTC']and x.cvsa_bias.eq('long').all()and x.cvsa_score.eq(.8).all()and x.cvsa_whale_short.eq(55).all()
entry=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='populate_entry_trend')
entry_fields=[n.slice.value for n in ast.walk(entry)if isinstance(n,ast.Subscript)and isinstance(n.slice,ast.Constant)and isinstance(n.slice.value,str)]
assert 'cvsa_whale_short'not in entry_fields
out={'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_fragment':'populate_indicators only',
 'source_api_executed':False,'strategy_constructor_executed':False,'source_financial_engine_executed':False,
 'fake_RSI_not_indicator_validation':True,'staged_current_signal_calls':calls,'historical_rows':3,
 'all_historical_rows_received_same_staged_current_values':True,'actual_entry_fields':entry_fields,
 'whale_short_field_used_by_actual_entry':False,'historical_backtest_admission':False,'native_funded_accounts_added':0}
(h/'source_broadcast_diagnostic.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
