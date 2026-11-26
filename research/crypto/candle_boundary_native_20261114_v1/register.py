"""Freeze timing interpretations, funded controls and costs before financial runs."""
import json
from datetime import UTC,datetime
from pathlib import Path
from .prepare import HERE,ROOT,OUT,save,sha,ref

def main():
    assert not(HERE/'plan.json').exists()
    assert json.loads((HERE/'input_admission.json').read_text())['passed']
    assert json.loads((HERE/'preflight.json').read_text())['passed']
    old=json.loads((ROOT/'research/crypto/session_reversal_native_20261113_v1/plan.json').read_text())
    keys=['engine_source','engine_manifest','engine_commit','environment','market_snapshot','research_support','storage_source','observer_source','start','end','comparison_end','periods']
    plan={k:old[k]for k in keys};pair='BTC/USDT:USDT'
    protocol={'registered_at':datetime.now(UTC).isoformat(),'primary':'Shanaev/Vasenin/Stepanov2023 DOI10.1016/j.heliyon.2023.e14236','mechanism':'Deterministic15-minute calendar boundary, distinct from price trend/mean reversion and positioning.','source_limits':'Paper labels returns at0/15/30/45, but open/close timestamp convention and code unavailable (on request). Original financial sample BitfinexUSDspot2021 with rolling-volume fee tiers; not our Binance perpetual fixed-fee account.','interpretations':{'BoundaryStartMinute':'enter minute0/15/30/45 OPEN, exit exactly1minute later OPEN','BoundaryEndMinute':'enter minute59/14/29/44 OPEN, exit exactly1minute later at boundary OPEN','ConstantMinuteControl':'always long, initial native valid entry; native stoploss/liquidation and terminal forced exit retained'},'inference':'These are frozen local interpretations, not claimed exact source replication. Do not choose a convention after returns.','account':'Each separate1000USDT,1x isolated,99%investable,one position,native lots/minimums,catastrophic99%stop. No external wallet.','cost_bps_per_side':[15,8],'funding':'Unchanged upstream inclusive open<=event<=close hour-bucket settlement. Exact-boundary exposure is conditional; do independent event ownership diagnostic, not posthoc NAV correction.','comparison':'14592 native pre-order hourly marks Jan2025-Aug2026 inclusive; floatingPNL/funding/remainingexitfee; annual continuous slices no reset; terminal liquidation separate.','native_1m_limit':'OHLCV next-open historical fills and aggregate cost proxy; no authenticated minute-level execution/latency or private funding certification.','gate':'Both interpretations must have positive full and each continuous annual slice,full hourlyDD<=30%,full return exceed matched constant at BOTH fixed costs. If not, preserve rejection.','combination_admission':'Do not construct a putative new alpha combination from a rejected calendar leg; any future funded combination needs its own frozen actual500wallet run.','no_rescue':'No selection of timing/hold length/fees/leverage/deposit after economic output. No Bitfinexzero-fee substitution.','orders_enabled':False,'production_eligible':False}
    save(HERE/'protocol.json',protocol)
    candidates=[]
    for strategy in ['BoundaryStartMinute','BoundaryEndMinute','ConstantMinuteControl']:
        config=json.loads(Path(old['candidates'][0]['config']['path']).read_text())
        config.update(strategy=strategy,timeframe='1m',dry_run_wallet=1000,max_open_trades=1)
        config.pop('timeframe_detail',None)
        config['exchange']['pair_whitelist']=[pair]
        path=HERE/f'{strategy}.json';save(path,config)
        candidates.append({'name':strategy,'strategy':strategy,'capital':1000,'pairs':[pair],'config':ref(path),'source_files':{'BoundaryRules.py':ref(HERE/'BoundaryRules.py')}})
    plan.update(schema='freqtrade.native.candle-boundary.v1',registered_at=protocol['registered_at'],output_root=str(OUT),datadir=str(OUT/'candles'),data_receipts=ref(OUT/'data_receipts.json'),pairs=[pair],candidates=candidates,marks={pair:old['marks'][pair]},initial_equity_usdt=1000,allowed_leverage=1.0,total_cost_bps_per_side=[15,8],runner_sha256=sha(HERE/'native_runner.py'),runner_module='research.crypto.candle_boundary_native_20261114_v1.native_runner',memory_guard_bytes=3*1024**3,wall_guard_seconds=3600,additional_source_seals=[ref(p)for p in [HERE/'protocol.json',HERE/'input_admission.json',HERE/'source_intake.json',HERE/'preflight.json',HERE/'prepare.py',HERE/'register.py',HERE/'preflight.py',HERE/'run.py',HERE/'sources/paper.pdf',ROOT/'research/crypto/band_trend_native_20261113_v1/observe_clock.py',ROOT/'research/crypto/skew_vol_native_20261113_v1/clock_fix.py']],orders_enabled=False,production_eligible=False)
    save(HERE/'plan.json',plan)
    print('Frozen6native1000USDTaccounts; no financial results seen')
if __name__=='__main__':main()
