"""Read-only native minimum-stake checks after final exits; no hypothetical PnL."""
import json
import sys
from pathlib import Path

import pandas as pd
from bootstrap import boot

HERE=Path(__file__).resolve().parent


def main():
    boot()
    sys.path.append(str(HERE.parents[2]))
    from freqtrade.resolvers import ExchangeResolver
    from freqtrade.enums import RunMode
    from research.crypto.band_trend_native_20261113_v1.analyze import read_native
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    snapshot=json.loads(Path(plan['market_snapshot']['path']).read_text());records=[]
    prices=pd.read_feather(Path(plan['datadir'])/'futures/BTC_USDT_USDT-1h-futures.feather').set_index('date').open
    for candidate in plan['candidates']:
        if not candidate['strategy'].startswith('Rolling'):continue
        for cost in plan['total_cost_bps_per_side']:
            name=f'{candidate["name"]}_{cost}bps';path=out/name
            stats,_,cfg=read_native(path,candidate['strategy'])
            cfg['runmode']=RunMode.BACKTEST
            t=stats['trades'][-1];last=pd.Timestamp(t['close_timestamp'],unit='ms',tz='UTC')
            fields=pd.read_parquet(path/'native_features.parquet').set_index('clock')
            desired=fields.loc[(fields.index>last)&(fields.index<=pd.Timestamp(plan['comparison_end']))&fields.target_weight.ne(0)]
            desired=desired.loc[~desired.index.hour.isin([0,8,16])]
            exchange=ExchangeResolver.load_exchange(cfg,validate=False,load_leverage_tiers=False)
            try:
                exchange._markets=snapshot['markets'];exchange._leverage_tiers=snapshot['leverage_tiers']
                exchange._api.set_markets(snapshot['markets'],snapshot['currencies'])
                exchange._api_async.set_markets(snapshot['markets'],snapshot['currencies'])
                minimum=[exchange.get_min_pair_stake_amount('BTC/USDT:USDT',float(prices.loc[d]),stoploss=-.05,leverage=1.) for d in desired.index]
            finally:exchange.close()
            cash=stats['final_balance'];budget=.99*cash
            gross=fees=0.
            for tr in stats['trades']:
                a,b=tr['orders'];entry=a['amount']*a['safe_price'];exit=b['amount']*b['safe_price']
                gross+=(entry-exit) if tr['is_short'] else (exit-entry)
                fees+=entry*tr['fee_open']+exit*tr['fee_close']
            funding=sum(tr['funding_fees'] for tr in stats['trades'])
            assert abs(gross-fees+funding-stats['profit_total_abs'])<1e-4
            records.append(dict(account=name,last_entry=t['open_date'],last_exit=t['close_date'],last_exit_reason=t['exit_reason'],
                 final_native_cash=cash,remaining_cash_budget=budget,remaining_desired_nonfunding_hours=len(desired),
                 minimum_native_stake_min=min(minimum) if minimum else None,minimum_native_stake_max=max(minimum) if minimum else None,
                 desired_hours_below_native_min=sum(budget<m for m in minimum),
                 entire_remaining_desired_phase_below_min=bool(minimum) and all(budget<m for m in minimum),
                 same_path_terminal_decomposition=dict(price_pnl=gross,fees=fees,funding=funding,native_pnl=stats['profit_total_abs']),
                 scope='Counterfactual-free minimum eligibility and actual terminal fill decomposition; not a lower-fee rerun or actual rejection-log claim'))
    (HERE/'eligibility_diagnostics.json').write_text(json.dumps(dict(records=records,financial_paths_unchanged=True,private_settlement=False),indent=2)+'\n')
    print(json.dumps(records,indent=2))


if __name__=='__main__':main()
