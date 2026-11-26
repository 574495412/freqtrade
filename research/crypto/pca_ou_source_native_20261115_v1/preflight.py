"""Actual native DataProvider/IStrategy custody and original-kernel witnesses."""
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import HERE, ROOT, boot

native=boot()
from freqtrade.data.dataprovider import DataProvider
from freqtrade.data.pca_ou import pca_ou_diagnostics
from freqtrade.enums import CandleType, RunMode
from freqtrade.resolvers import StrategyResolver


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,r):(HERE/name).write_text(json.dumps(r,indent=2,default=str,allow_nan=False)+'\n')


def source_models(close):
    # Original pure arithmetic classes only; neither source CSV constructor,
    # strategy, backtester nor project main is ever invoked.
    for r in json.loads((HERE/'source_receipts.json').read_text()):assert sha(r['path'])==r['sha256']
    root=HERE/'sources/src';package=types.ModuleType('src');package.__path__=[str(root)]
    assert 'src'not in sys.modules
    sys.modules['src']=package
    modules={}
    for short in ['data_loader','pca_engine','residual_model','ou_estimator','s_score']:
        name='src.'+short;spec=importlib.util.spec_from_file_location(name,root/(short+'.py'))
        mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);modules[short]=mod
    class FixedObservedLoader:
        def get_universe(self,t):return list(close.columns)
        def get_price_window(self,tokens,start,end):return close.loc[start:end,tokens].copy()
    loader=FixedObservedLoader()
    return (modules['pca_engine'].PCAEngine(loader),modules['residual_model'].ResidualModel(loader),
        modules['ou_estimator'].OUEstimator(),modules['s_score'].SScoreCalculator())


def main():
    protocol=json.loads((HERE/'protocol.json').read_text());datadir=Path(protocol['datadir'])
    assert not(HERE/'native_preflight.json').exists()
    records=[];raw={}
    for pair in protocol['pairs']:
        stem=pair.replace('/','_').replace(':','_')
        path=datadir/'futures'/(stem+'-1h-futures.feather')
        frame=pd.read_feather(path);index=pd.DatetimeIndex(frame.date)
        assert index.as_unit('ns').equals(pd.date_range(index[0],index[-1],freq='h').as_unit('ns'))
        assert index.is_unique and str(index.tz)=='UTC'
        assert np.isfinite(frame[['open','high','low','close','volume']].to_numpy()).all()
        assert(frame[['open','high','low','close']].to_numpy()>0).all()
        records.append(dict(pair=pair,path=str(path),sha256=sha(path),rows=len(frame),first=str(index[0]),last=str(index[-1])))
        raw[pair]=frame.set_index('date').close
    admission=dict(protocol_sha256=sha(HERE/'protocol.json'),pairs=protocol['pairs'],native_inputs=records,
        decision_start=protocol['account_start'],decision_end=protocol['account_end'],financial_stage_admitted=False)
    save('input_admission.json',admission)
    cfg=json.loads((HERE.with_name('fip_path_source_native_20261115_v1')/'input_config.json').read_text())
    cfg.update(strategy='PcaInputs',strategy_path=str(HERE),runmode=RunMode.BACKTEST,
        dry_run=True,timeframe='1h',startup_candle_count=0,timerange=None,datadir=datadir,
        user_data_dir=ROOT/'user_data',candle_type_def=CandleType.FUTURES,
        research_admission=str(HERE/'input_admission.json'),research_admission_sha256=sha(HERE/'input_admission.json'),
        research_diagnostic_only=True)
    cfg['exchange']['pair_whitelist']=protocol['pairs'];save('input_config.json',cfg)
    owner=StrategyResolver.load_strategy(cfg);owner.dp=DataProvider(cfg,None)
    owner.bot_start();close,parts=owner.research_close,owner.research_components
    reference=pd.DataFrame(raw);reference.index=reference.index.as_unit(close.index.unit)
    pd.testing.assert_frame_equal(close,reference,check_exact=True)
    pca,residual,ou,ss=source_models(close)
    source_rows=[];maximum=0.;terminal_max=0.
    boundaries=['2025-01-01T00:00Z','2026-01-01T00:00Z','2026-08-01T00:00Z']
    for text in boundaries:
        at=pd.Timestamp(text);pc=pca.compute_at_time(at);res=residual.compute_at_time(at,pc)
        orn=ou.estimate_from_residuals(res);scores=ss.compute_at_time(res,orn)
        expected=pd.Series({pair:-scores.get_s(pair)if scores.get_s(pair)is not None else np.nan for pair in close})
        actual=parts['pca_ou_reversal'].loc[at]
        np.testing.assert_allclose(actual,expected,rtol=1e-7,atol=1e-7,equal_nan=True)
        mask=np.isfinite(expected);err=float(np.max(np.abs(actual[mask]-expected[mask])))
        maximum=max(maximum,err)
        terminal_max=max(terminal_max,float(np.nanmax(np.abs(parts['terminal_cumulative_residual'].loc[at]))))
        past=close.loc[close.index<at];changed=close.copy();changed.loc[changed.index>=at]*=2.71828
        for sample in [past,changed]:
            other=pca_ou_diagnostics(sample,[at])
            for key in parts:
                np.testing.assert_allclose(parts[key].loc[at],other[key].loc[at],rtol=0,atol=0,equal_nan=True)
        source_rows.append(dict(at=str(at),source_valid=int(mask.sum()),source_assets=len(expected),
            max_score_error=err,prefix_and_future_perturbation_exact=True))
    mapped={name:pd.DataFrame(index=parts[name].index,columns=close.columns,dtype=float)for name in parts}
    opens={};mapping=0
    for pair in close:
        frame=owner.dp.get_pair_dataframe(pair,'1h','futures')
        result=owner.populate_indicators(frame.copy(),{'pair':pair}).set_index('date')
        pd.testing.assert_frame_equal(result.loc[:,frame.columns.drop('date')],frame.set_index('date'),check_exact=True)
        for name in parts:
            mapped[name][pair]=result[name].reindex(parts[name].index).to_numpy()
            np.testing.assert_array_equal(mapped[name][pair],parts[name][pair])
            mapping+=len(mapped[name])
        opens[pair]=result.open
    open_panel=pd.DataFrame(opens);out=ROOT/'user_data/crypto_quant/pca_ou_source_native_20261115_v1'
    out.mkdir(parents=True,exist_ok=False)
    for name,frame in mapped.items():frame.to_parquet(out/('native_'+name+'.parquet'))
    close.to_parquet(out/'native_close.parquet');open_panel.to_parquet(out/'native_open.parquet')
    coverage=parts['pca_ou_reversal'].notna().sum(axis=1)
    save('coverage.json',dict(min_valid_assets=int(coverage.min()),max_valid_assets=int(coverage.max()),
        decisions=len(coverage),invalid_ou_cells=int(parts['pca_ou_reversal'].isna().to_numpy().sum()),
        every_decision_at_least20=bool(coverage.ge(20).all()),unknown_to_zero=False))
    assert coverage.ge(20).all(),'Frozen per-decision coverage failed;no prediction or financial admission'
    labels={}
    for horizon in [1,24]:
        days=pd.date_range(protocol['account_start'],protocol['account_end'],freq=f'{horizon}h')
        days=days[days+pd.Timedelta(hours=horizon+1)<=pd.Timestamp(protocol['account_end'])]
        y=pd.DataFrame(open_panel.reindex(days+pd.Timedelta(hours=horizon+1)).to_numpy()/
            open_panel.reindex(days+pd.Timedelta(hours=1)).to_numpy()-1,index=days,columns=close.columns)
        assert np.isfinite(y.to_numpy()).all();y.to_parquet(out/f'native_labels_{horizon}h.parquet')
        labels[str(horizon)]=dict(observations=len(y),first=str(days[0]),last=str(days[-1]),
            final_label_endpoint=str(days[-1]+pd.Timedelta(hours=horizon+1)))
    frozen=[ROOT/'freqtrade/data/pca_ou.py',*HERE.glob('*.py'),HERE/'protocol.json',HERE/'input_admission.json',HERE/'input_config.json']
    save('implementation_freeze.json',{str(p):sha(p)for p in frozen})
    save('native_preflight.json',dict(passed=True,native_commit=native['commit'],native_files=len(native['files']),
        input_files=40,assets=40,actual_native_score_hours=len(parts['pca_ou_reversal']),
        indicator_mapping_cells=mapping,original_source_score_witnesses=source_rows,
        max_original_score_error=maximum,max_sample_terminal_cumulative_residual=terminal_max,
        reference_financial_engine_executed=False,labels=labels,factor_output=str(out),
        factor_analysis_calls=0,financial_accounts=0,new_accepted_alphas=0))
    print('NATIVE PREFLIGHT',len(parts['pca_ou_reversal']),'hours;',mapping,'mappingcells;',int(coverage.min()),'minvalidassets;sourceerror',maximum,flush=True)


if __name__=='__main__':main()
