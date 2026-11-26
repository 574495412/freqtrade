"""Actual native lifecycle nominations; zero PnL/fee/wallet computation."""
import json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OLD=ROOT/'research/crypto/copula_reference_native_20261114_v1'
OUT=ROOT/'user_data/crypto_quant/copula_account_native_20261115_v2'

def main():
    OUT.mkdir(exist_ok=True,parents=True)
    plan=json.loads((ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/plan.json').read_text());sys.path.insert(0,plan['engine_source']);sys.path.insert(0,str(ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/dependencies'))
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode,CandleType
    cfg=json.loads(Path(plan['candidates'][0]['config']['path']).read_text());a=json.loads((OLD/'input_admission.json').read_text());cfg.update(strategy='FormationInputs',strategy_path=str(HERE),user_data_dir=OUT,datadir=Path(a['data_receipts']['path']).parent/'candles',research_root=str(ROOT),research_diagnostic_only=True,runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES)
    cfg['exchange']['pair_whitelist']=[s+'/USDT:USDT'for s in a['source_universe']]
    owner=StrategyResolver.load_strategy(cfg);owner.dp=DataProvider(cfg,None);owner.bot_start()
    models=owner.models;union=sorted({s for r in models for s in r['selected']});eos=[r['formation']['asof']for r in models if 'EOS'in r['selected']]
    p=OUT/'nominations.json';p.write_text(json.dumps({'source_members':a['source_universe'],'models':models,'nominated_union':union,'EOS_nominated_weeks':eos,'native_strategy_resolver':True,'native_dataprovider':True,'native_accounts':0,'financial_returns':'UNKNOWN'},indent=2)+'\n');print('NATIVE',len(models),'weekly source nominations;EOSweeks',eos,'union',union)
if __name__=='__main__':main()
