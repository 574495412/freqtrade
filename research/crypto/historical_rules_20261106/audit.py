"""Audit saved model orders against published rule transitions without rerunning accounts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HELPER = HERE.parent/'rd_program_20261105'/'data_extension'
sys.path.insert(0, str(HELPER))
from probe_event_sources import fetch, decode, now, sha, write

FUND = HERE.parent/'historical_funding_clock_20261106'
LONG = HERE.parent/'factor_cost_futures_long_20261106'
META = HERE.parent/'strategy_comparison_wide_20261105'/'public-market-rules-40.json'
NOTICE = HERE/'notice_facts.json'
PLAN = HERE/'registration.json'
OUT = ROOT/'user_data/crypto_quant/rd_program_20261105_v1/historical_rule_probe_v001'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return sha(path.read_bytes())


def accounts():
    completed = read(LONG/'completion.json')['records']
    registered = {r['id']:r for r in read(LONG/'plan.json')['runs']}
    for record in completed:
        run = registered[record['id']]
        directory = ROOT/'user_data/crypto_quant/factor_cost_scenario_20261106'/record['id']
        manifest = directory/'artifact_hashes.json'
        result = directory/'result.json'
        if digest(manifest)!=record['artifact_manifest_sha256'] or digest(result)!=record['result_sha256']:
            raise ValueError('Prior account receipt changed')
        paths = [name for name in read(manifest) if name.startswith('native_results/') and name.endswith('.zip')]
        if len(paths)!=1:
            raise ValueError('Ambiguous archived orders')
        archive = directory/paths[0]
        if digest(archive)!=read(manifest)[paths[0]]:
            raise ValueError('Native order archive changed')
        yield dict(id=record['id'],strategy=run['strategy'],cost=run['cost'],archive=str(archive.relative_to(ROOT)),
                   input_paths=[str(p.relative_to(ROOT)) for p in (manifest,result,archive)])


def register():
    cases = list(accounts())
    facts = read(NOTICE)
    requests = [{"id":key,"url":url,"kind":"notice"} for key,url in facts['sources'].items()
                if key in ('sol_original','sol_postponement','btc_notional')]
    requests += [dict(id=f'{exchange}_{symbol}',kind='vendor_metadata',
        url=f'https://api.tardis.dev/v1/instruments/{exchange}/{symbol}')
        for exchange,symbol in [('binance','BTCUSDT'),('binance-futures','BTCUSDT'),('binance-futures','SOLUSDT')]]
    bbo = read(FUND/'audit_result.json')['book_samples'][0]['normalized_bbo']
    paths = [Path(__file__),NOTICE,META,LONG/'plan.json',LONG/'completion.json',FUND/'audit_result.json',ROOT/bbo,
             HELPER/'probe_event_sources.py',ROOT/'freqtrade/optimize/backtesting.py',ROOT/'freqtrade/persistence/trade_model.py',
             *(ROOT/p for c in cases for p in c['input_paths'])]
    write(PLAN,dict(registered_at_utc=now(),cases=cases,requests=requests,workers=2,retries=0,maximum_requests=len(requests),
        input_hashes={str(p.relative_to(ROOT)):digest(p) for p in paths},
        narrow_screen='opening and simulated fill timestamps both inside the conservative pre-transition notice window',
        broad_screen='pre-transition sensitivity only; not assumed rule coverage',new_financial_accounts=0))


def capture(case):
    record = dict(case, started_at_utc=now())
    try:
        status,headers,body,state = fetch(case['url'])
        record.update(http_status=status,headers=headers,response_state=state)
        if body is not None:
            path = OUT/(case['id']+'.raw')
            with path.open('xb') as handle:
                handle.write(body)
            record.update(path=str(path.relative_to(ROOT)),sha256=sha(body),bytes=len(body))
        if status==200 and state=='body_complete' and case['kind']=='vendor_metadata':
            data=json.loads(decode(body,headers))
            record['rule_schema']={k:data.get(k) for k in ('id','priceIncrement','amountIncrement','minTradeAmount','changes')}
    except Exception as exc:
        record['error']=f'{type(exc).__name__}: {exc}'
    record['finished_at_utc']=now()
    return record


def screen(case, rules):
    with zipfile.ZipFile(ROOT/case['archive']) as archive:
        names = [n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
        if len(names)!=1:
            raise ValueError('Ambiguous native trade export')
        trades = json.loads(archive.read(names[0]), parse_float=D)['strategy'][case['strategy']]['trades']
    records = []
    for rule in rules:
        start = datetime.fromisoformat(rule['announced_old_rule_screen_start'])
        end = datetime.fromisoformat(rule['scheduled_at'])
        eligible = pre_transition = 0
        narrow,broad = [],[]
        for trade in trades:
            if trade['pair']!=rule['pair']:
                continue
            entries = [o for o in trade['orders'] if o['ft_is_entry']]
            if trade['is_short'] or trade['leverage']!=1 or len(entries)!=1 or entries[0]['ft_order_side']!='buy':
                raise ValueError('Unexpected saved initial-entry contract')
            order = entries[0]
            filled = datetime.fromtimestamp(order['order_filled_timestamp']/1000, timezone.utc)
            opened = datetime.fromisoformat(trade['open_date'])
            if opened > filled:
                raise ValueError('Native opening clock follows simulated fill')
            if filled >= end:
                continue
            pre_transition += 1
            near_notice = start <= opened <= filled < end
            eligible += int(near_notice)
            amount,price = D(order['amount']),D(order['safe_price'])
            value = amount if rule['field']=='minimum_quantity' else amount*price
            if value<D(rule['old']):
                row=dict(opened_at=opened.isoformat(),simulated_filled_at=filled.isoformat(),amount=str(amount),
                         price=str(price),notional_without_fee=str(amount*price),compared_value=str(value))
                broad.append(row)
                if near_notice:
                    narrow.append(row)
        records.append(dict(pair=rule['pair'],field=rule['field'],announced_old=rule['old'],eligible_in_notice_window=eligible,
            below_old_in_notice_window=narrow,pre_transition_fills=pre_transition,below_old_pre_transition_sensitivity=broad))
    return dict(id=case['id'],strategy=case['strategy'],cost=case['cost'],rules=records)


def compare_quote_grid():
    market = read(META)
    path = ROOT/read(FUND/'audit_result.json')['book_samples'][0]['normalized_bbo']
    result = {}
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            symbol = row['symbol']
            filters = {r['filterType']:r for r in market['markets'][symbol[:-4]+'/USDT:USDT']['info']['filters']}
            tick,minimum = D(filters['PRICE_FILTER']['tickSize']),D(filters['PRICE_FILTER']['minPrice'])
            step = D(filters['LOT_SIZE']['stepSize'])
            status = result.setdefault(symbol,dict(rows=0,tick_size=str(tick),quantity_step=str(step),off_snapshot_tick=0,off_snapshot_step=0))
            status['rows']+=1
            status['off_snapshot_tick']+=int(any((D(row[k])-minimum)%tick for k in ('bid','ask')))
            status['off_snapshot_step']+=int(any(D(row[k])%step for k in ('bid_quantity','ask_quantity')))
    return dict(snapshot_observed_at=market['observed_at'],samples=result,scope='arithmetic consistency with later snapshot only')


def run():
    plan = read(PLAN)
    for path,expected in plan['input_hashes'].items():
        if digest(ROOT/path)!=expected:
            raise ValueError('Registered input changed: '+path)
    OUT.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=plan['workers']) as pool:
        receipts=list(pool.map(capture,plan['requests']))
    facts = read(NOTICE)
    result=[screen(case,facts['rules']) for case in plan['cases']]
    for path,expected in plan['input_hashes'].items():
        if digest(ROOT/path)!=expected:
            raise ValueError('Post-audit input drift: '+path)
    write(HERE/'result.json',dict(registration_sha256=digest(PLAN),receipts=receipts,rule_facts=facts['rules'],
        simulated_entry_screen=result,quote_grid=compare_quote_grid(),historical_rule_admission=False,financial_accounts=0))


if __name__=='__main__':
    if sys.argv[1:]==['register']:
        register()
    elif sys.argv[1:]==['run']:
        run()
    else:
        raise SystemExit('Use register or run')
