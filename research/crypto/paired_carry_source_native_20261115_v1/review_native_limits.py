"""Owning native minimum/stop reserve probe, without changing frozen quantities."""
import hashlib
import inspect
import json
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

HERE=Path(__file__).resolve().parent


def native_minimum(config, markets, pair, rate):
    from freqtrade.exchange import Exchange
    from freqtrade.enums import TradingMode
    shell=SimpleNamespace(markets=markets,_config=config,trading_mode=TradingMode(config['trading_mode']))
    for method in ['_get_stake_amount_limit','_contracts_to_amount','get_contract_size','_get_stake_amount_considering_leverage']:
        setattr(shell,method,MethodType(getattr(Exchange,method),shell))
    return Exchange.get_min_pair_stake_amount(shell,pair,rate,-.99,1)


def main():
    plan=json.loads((HERE/'plan.json').read_text());sys.path.insert(0,plan['engine_source'])
    from freqtrade.exchange import Exchange
    probes=json.loads((HERE/'entry_precision_probes.json').read_text())['checks']
    rates={p['candidate']:p['native_proposed_rate']for p in probes};rows=[]
    for c in plan['candidates']:
        markets=json.loads(Path(c['market_snapshot']['path']).read_text())['markets']
        cfg=json.loads(Path(c['config']['path']).read_text())
        pair=c['pairs'][0];rate=rates[c['name']]
        minimum=native_minimum(cfg,markets,pair,rate)
        requested=c['quantity']*rate
        rows.append(dict(candidate=c['name'],role=c['role'],portfolio_capital=c['portfolio_capital'],
            scenario=c['scenario'],native_minimum_stake=minimum,requested_stake=requested,
            quantity=c['quantity'],wallet_capital=c['capital'],
            admitted=minimum is not None and minimum<=requested<=c['capital']*.99))
    for row in rows:
        row['cohort_admitted']=all(p['admitted']for p in rows if (p['role'],p['portfolio_capital'],p['scenario'])==
                                  (row['role'],row['portfolio_capital'],row['scenario']))
    owner=Path(inspect.getfile(Exchange))
    result=dict(owning_native_method='Exchange.get_min_pair_stake_amount and original helpers',
        owner=str(owner),owner_sha256=hashlib.sha256(owner.read_bytes()).hexdigest(),
        read_only_native_method_probe=True,not_a_backtest=True,rows=rows,
        preflight_correction='Initial preflight used raw market limits,omitted native reserve. Failed actual125wallet confirms exact native refusal;do not upsize or change stop/quantity.',
        source_plan_unchanged=True,source_parameters_unchanged=True)
    (HERE/'native_limit_review.json').write_text(json.dumps(result,indent=2)+'\n')
    print('NATIVE LIMIT REVIEW',[(r['candidate'],r['requested_stake'],r['native_minimum_stake'])for r in rows if not r['admitted']])


if __name__=='__main__':main()
