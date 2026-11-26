"""Original quantities plus native reserve, price ticks and signal parity."""
import copy
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    plan=json.loads((HERE/'plan.json').read_text());sys.path[:0]=[plan['engine_source'],str(HERE)]
    source=Path(plan['reservation_source']['path']);assert sha(source)==plan['reservation_source']['sha256']
    spec=importlib.util.spec_from_file_location('freqtrade.data.carry_reservation',source)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode
    from freqtrade.exchange.exchange_utils import amount_to_contract_precision
    from review_native_limits import native_minimum
    from CaptureRules import signal_receipt
    parent=HERE.with_name('paired_carry_source_native_20261115_v1')
    previous=json.loads((parent/'plan.json').read_text());by_name={c['name']:c for c in previous['candidates']}
    expected=json.loads((HERE/'preflight_signal_receipts.json').read_text())
    rates={r['candidate']:r['native_proposed_rate']for r in json.loads((HERE/'entry_precision_probes.json').read_text())['checks']}
    manifest=json.loads(Path(plan['engine_manifest']['path']).read_text())
    for name,digest in manifest['files'].items():assert sha(Path(plan['engine_source'])/name)==digest
    records=[]
    for c in plan['candidates']:
        cfg=json.loads(Path(c['config']['path']).read_text());old=json.loads(Path(by_name[c['name']]['config']['path']).read_text())
        assert cfg==old,'No economic config change permitted'
        pair=c['pairs'][0];market=json.loads(Path(c['market_snapshot']['path']).read_text())['markets']
        test=copy.deepcopy(cfg);test.update(runmode=RunMode.BACKTEST,fee=c['cost_bps']/10000,
            datadir=Path(plan['datadir']),user_data_dir=HERE.parents[2]/'user_data',strategy_path=str(HERE))
        owner=StrategyResolver.load_strategy(test)
        owner.dp=DataProvider(test,SimpleNamespace(markets=market));owner.bot_start()
        raw=owner.dp.get_pair_dataframe(pair,'5m','futures'if c['mode']=='futures'else'spot')
        actual=owner.ft_advise_signals(owner.advise_indicators(raw.copy(),{'pair':pair}),{'pair':pair})
        assert signal_receipt(actual,c['strategy'])==expected[c['name']]
        minimum=native_minimum(cfg,market,pair,rates[c['name']]);maximum=c['capital']*.99
        stake=module.reserve_for_quantity(c['quantity'],rates[c['name']],minimum,maximum,market[pair],c['cost_bps']/10000)
        callback=owner.custom_stake_amount(pair,datetime(2025,1,1,0,5,tzinfo=UTC),rates[c['name']],
            maximum,minimum,maximum,1,None,'short'if c['mode']=='futures'else'long')
        assert callback==stake and owner._fatal_error is None
        amount=amount_to_contract_precision(stake/rates[c['name']],market[pair]['precision']['amount'],4,1)
        assert abs(amount-c['quantity'])<1e-12
        assert owner.confirm_trade_entry(pair,'market',amount,rates[c['name']],'GTC',
            datetime(2025,1,1,0,5,tzinfo=UTC),None,'short'if c['mode']=='futures'else'long')
        records.append(dict(candidate=c['name'],native_minimum=minimum,reservation=stake,
            actual_notional=amount*rates[c['name']],quantity=amount,maximum_stake=maximum,
            fee_in_reservation_budget=True,source_signals_exact=True,passed=True))
    assert len(records)==24
    (HERE/'preflight.json').write_text(json.dumps(dict(passed=True,native_original_files=595,
        records=records,plan=dict(path=str(HERE/'plan.json'),sha256=sha(HERE/'plan.json')),
        all_economic_configs_and_quantities_exact=True,financial_accounts_started=False),indent=2)+'\n')
    print('24native reserve/quantity/fee budget and source signal checks passed;0v2financial accounts')


if __name__=='__main__':main()
