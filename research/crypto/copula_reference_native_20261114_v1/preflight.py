"""Native source20 admission, causal formation algebra and all-family kernels."""
import importlib.util,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
from .prepare import HERE,ROOT,OUT,SYMBOLS,sha,save

def main():
    parent=json.loads((ROOT/'research/crypto/pairs_cointegration_native_20261114_v1/plan.json').read_text())
    manifest=json.loads(Path(parent['engine_manifest']['path']).read_text());engine=Path(parent['engine_source'])
    assert all(sha(engine/n)==h for n,h in manifest['files'].items())
    assert {str(p.relative_to(engine))for p in engine.rglob('*.py')}=={n for n in manifest['files']if n.endswith('.py')}
    sys.path.insert(0,str(HERE/'dependencies'));sys.path.insert(0,str(engine))
    import pyvinecopulib as pv
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.data.dataprovider import DataProvider
    from freqtrade.enums import RunMode,CandleType
    config=json.loads(Path(parent['candidates'][0]['config']['path']).read_text())
    config.update(strategy='CopulaInputs',strategy_path=str(HERE),user_data_dir=OUT,datadir=OUT/'candles',runmode=RunMode.BACKTEST,candle_type_def=CandleType.FUTURES,research_diagnostic_only=True,research_admission=str(HERE/'input_admission.json'),research_input_owner=str(ROOT/'freqtrade/data/copula_reference.py'))
    config['exchange']['pair_whitelist']=[s+'/USDT:USDT'for s in SYMBOLS]
    owner=StrategyResolver.load_strategy(config);owner.dp=DataProvider(config,None);owner.bot_start();m=owner.module
    blocked=False
    try:owner.ft_bot_start()
    except ValueError as e:blocked='Input-only'in str(e)
    assert blocked,'Input-only view must reject actual native financial startup directly'
    frames=[]
    for s in SYMBOLS:
        f=owner.dp.get_pair_dataframe(s+'/USDT:USDT','1h','futures')
        actual=owner.advise_indicators(f.copy(),{'pair':s+'/USDT:USDT'})
        assert actual.source_tradable.all(),s
        actual[['date','source_tradable']].to_parquet(OUT/f'native_input_{s}.parquet',index=False)
        frames.append({'symbol':s,'rows':len(actual),'first':str(actual.date.iloc[0]),'last':str(actual.date.iloc[-1])})
    assert len(owner.research_prices.columns)==20
    # Structural EOS absence remains NaN; no global inner join dropping dates.
    p=owner.research_prices;assert len(p)==15336
    assert p.loc[p.index>=owner.terminal,'EOS'].isna().all()
    formations=[];max_error=0;scalar_cells=0
    for edge in ['2025-01-03T00:00Z','2025-06-06T00:00Z','2026-08-28T00:00Z']:
        asof=pd.Timestamp(edge);beta,spread,diag=m.formation_spreads(p,asof,owner.terminal)
        for symbol in beta.index:
            f=p.loc[spread.index];b=float(np.linalg.lstsq(f[[symbol]].to_numpy(),f.BTC.to_numpy(),rcond=None)[0][0]);error=abs(b-beta[symbol]);max_error=max(max_error,error);assert error<1e-9*max(1,abs(b))
            expected=np.array([float(row.BTC)-b*float(getattr(row,symbol))for row in f.itertuples()]);assert np.allclose(expected,spread[symbol],rtol=1e-10,atol=1e-7);scalar_cells+=len(expected)
        before=p.index<asof;prefix=p.loc[before];changed=p.copy();changed.loc[~before]*=3
        b1,s1,d1=m.formation_spreads(prefix,asof,owner.terminal);b2,s2,d2=m.formation_spreads(changed,asof,owner.terminal)
        pd.testing.assert_series_equal(beta,b1,check_exact=True);pd.testing.assert_series_equal(beta,b2,check_exact=True);pd.testing.assert_frame_equal(spread,s1,check_exact=True);pd.testing.assert_frame_equal(spread,s2,check_exact=True)
        formations.append({**diag,'beta':beta.to_dict(),'future_perturbation_and_prefix_exact':True})
    uv=np.array([(a,b)for a in [.17,.39,.68,.86]for b in [.21,.47,.73,.88]],order='F');eps=1e-5;kernel_checks=[]
    for family,rotation in m.family_grid(pv):
        if family.startswith('tawn_'):
            c=m.canonical_tawn(pv,.6,2.,family,rotation)
            if rotation==0:
                a,b=(.6,1.)if family=='tawn_alpha'else(1.,.6)
                assert np.allclose(c.cdf(uv),m.tawn_cdf(uv,a,b,2.),atol=1e-12,rtol=0)
        else:
            parameters={'gaussian':[[.4]],'student':[[.4],[5]],'clayton':[[1.4]],'gumbel':[[1.7]],'frank':[[3]],'joe':[[2]],'bb1':[[1],[1.5]],'bb6':[[1.5],[1.4]],'bb7':[[1.5],[1.4]],'bb8':[[2],[.8]]}[family]
            c=pv.Bicop(family=getattr(pv.BicopFamily,family),rotation=rotation,parameters=np.asarray(parameters,dtype=float))
        h12,h21=m.h_probabilities(c,uv);plus=uv.copy();minus=uv.copy();plus[:,1]+=eps;minus[:,1]-=eps
        derivative12=(c.cdf(plus)-c.cdf(minus))/(2*eps);plus=uv.copy();minus=uv.copy();plus[:,0]+=eps;minus[:,0]-=eps
        derivative21=(c.cdf(plus)-c.cdf(minus))/(2*eps);error=max(np.max(abs(h12-derivative12)),np.max(abs(h21-derivative21)));assert error<2e-5,(family,rotation,error)
        bounds=np.array([[.2,1.],[.6,1.],[1.,.3],[1.,.7]])
        assert np.allclose(c.cdf(bounds),[.2,.6,.3,.7],rtol=0,atol=1e-9),(family,rotation)
        kernel_checks.append({'family':family,'rotation':rotation,'probability_cells':len(uv)*2,'max_cdf_derivative_error':float(error),'uniform_margins_passed':True})
    assert len(kernel_checks)==39
    for alpha in [.1,.15,.2]:
        a=np.array([alpha/2,1-alpha/2,.5,1-alpha/2,alpha/2,.5]);b=1-a
        assert np.array_equal(m.pair_states(a,b,alpha),[1,1,0,-1,-1,0])
        assert m.coin_units(1,2,3)=={'BTC':0.,'coin1':-2,'coin2':3}
        assert m.coin_units(-1,2,3)=={'BTC':0.,'coin1':2,'coin2':-3}
    # Demonstrate why literal printed formulas cannot be copied as copulas.
    g=pv.Bicop(family=pv.BicopFamily.gumbel,parameters=np.array([[2.]]));literal_rotation=g.cdf(np.array([[1-.3,1.]]))[0]
    assert abs(literal_rotation-.3)>.1
    save(HERE/'native_preflight.json',{'passed_within_scope':True,'native_strategy_resolver':True,'native_dataprovider':True,'unmodified_upstream_files':len(manifest['files']),'engine_commit':manifest['commit'],'input_rows':sum(r['rows']for r in frames),'input_records':frames,'source_columns':20,'formation_checks':formations,'scalar_formation_residual_cells':scalar_cells,'maximum_OLS_coefficient_difference':max_error,'copula_kernel_checks':kernel_checks,'kernel_probabilities_checked':sum(r['probability_cells']for r in kernel_checks),'source_threshold_state_tests':3,'actual_native_financial_startup_rejected':blocked,'paper_literal_rotation_boundary_error_demonstrated':float(abs(literal_rotation-.3)),'library_version':pv.__version__,'full_rolling_models_fitted':0,'source_exact_replication':False,'native_funded_accounts':0,'factor_analysis_calls':0,'goal_achieved':False})
    print('PASS source20 native inputs;',sum(r['rows']for r in frames),'rows;',scalar_cells,'scalar residuals;39kernels;0accounts')
if __name__=='__main__':main()
