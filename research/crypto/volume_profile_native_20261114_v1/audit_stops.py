"""Retained absolute stop levels and native entry-bar custom-stop fill semantics."""
from pathlib import Path
from types import SimpleNamespace
import gzip,json
import pandas as pd
from .bootstrap import boot,HERE
boot()
from freqtrade.exchange import price_to_precision
from ccxt import TICK_SIZE
from research.crypto.band_trend_native_20261113_v1.analyze import read_native

def main():
    plan=json.loads((HERE/'plan.json').read_text());root=Path(plan['output_root']);raw=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-5m-futures.feather').set_index('date');records=[];count=specials=0;maximum=fill_error=0.
    for c in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            key=f'{c["name"]}_{cost}bps';path=root/key;stats,_,_=read_native(path,c['strategy']);trace=json.load(gzip.open(path/'strategy_trace.json.gz','rt'));proof={pd.Timestamp(r['clock']):r for r in trace['fills']};local=0
            for t in stats['trades']:
                opened=pd.Timestamp(t['open_timestamp'],unit='ms',tz='UTC');closed=pd.Timestamp(t['close_timestamp'],unit='ms',tz='UTC');p=proof[opened];error=abs(t['stop_loss_abs']-p['stop']);maximum=max(maximum,error);assert error<=.100001
                if t['exit_reason']not in ['stop_loss','trailing_stop_loss']:continue
                row=raw.loc[closed];short=t['is_short'];stop=t['stop_loss_abs']
                if(short and stop<row.low)or(not short and stop>row.high):expected=row.open
                elif t['exit_reason']=='trailing_stop_loss'and t['trade_duration']==0:
                    sign=-1 if short else 1;value=row.open*(1-sign*abs(t['stop_loss_ratio']/t['leverage']));expected=min(row.high,value)if short else max(row.low,value);specials+=1;local+=1
                else:expected=stop
                expected=price_to_precision(float(expected),.1,TICK_SIZE);difference=abs(expected-t['close_rate']);fill_error=max(fill_error,difference);assert difference<1e-6,(key,opened,closed,expected,t['close_rate'])
                count+=1
            records.append({'account':key,'entrybar_native_custom_stop_special_fills':local})
    result={'passed':True,'absolute_source_stop_level_max_error':maximum,'stop_fills_independently_checked':count,'native_entrybar_custom_stop_special_fills':specials,'stop_fill_max_error':fill_error,'scope':'Native labels some fixed absolute custom stops trailing_stop_loss. Entrybar trade_duration0 uses native pessimistic custom-stop formula, not an invented direct sourceSL fill. Source account not replicated.','records':records}
    (HERE/'stop_semantics_audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k!='records'},indent=2))
if __name__=='__main__':main()
