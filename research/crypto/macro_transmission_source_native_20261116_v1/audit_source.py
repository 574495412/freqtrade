"""Source formula witnesses only; never run an author or local financial engine."""
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).absolute().parent


def main():
    source=HERE/'sources/10_cross_asset_rates_dxy.py'
    text=source.read_text();tree=ast.parse(text)
    assert 'ret["BTC"] = np.log(panel["BTC"]).diff()' in text
    assert 'bh_ret = btc_ret.copy()' in text and 'btc_ret = ret["BTC"]' in text
    perf=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='perf')
    assert '(1 + r).cumprod()' in ast.get_source_segment(text,perf)
    closes=np.array([100.,200.,100.]);logs=np.diff(np.log(closes))
    source_multiple=float(np.prod(1+logs));actual_multiple=float(closes[-1]/closes[0])
    assert abs(source_multiple-(1-np.log(2)**2))<1e-14 and actual_multiple==1
    fixture=pd.read_parquet(HERE/'sources/10_cross_asset.parquet')
    expected=pd.date_range('2025-01-01','2026-08-31',freq='D')
    absent=expected.difference(pd.DatetimeIndex(fixture.index))
    result=dict(passed=True,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        selected_perf_definition_lines=[perf.lineno,perf.end_lineno],
        source_bh_compounds_log_returns_as_simple=True,
        deterministic_price_path=closes.tolist(),true_buy_and_hold_multiple=actual_multiple,
        source_formula_multiple=source_multiple,source_formula_loss_percent=100*(source_multiple-1),
        witness_is_synthetic_not_strategy_profit=True,source_financial_engine_executed=False,
        source_fixture_rows=len(fixture),source_fixture_start=str(fixture.index.min()),
        source_fixture_end=str(fixture.index.max()),absent_target_calendar_dates=len(absent),
        absent_target_start=str(absent.min()),absent_target_end=str(absent.max()),
        source_main_rule='LOOKBACK10,REDUCED.25,yielddiff>0 ANDdxylogdiff>0,onecalendar-daylag',
        source_statistical_grid='full-sample5x20correlation/FDRgrid;not training/live partition;not actually used to select fixedheadwindinputs',
        source_portfolio_contract_missing=['initial/terminalcash','actualunits','dailyrebalancecostwhenweightunchanged','minnotional/precision','snapshotfirstseen'],
        full_source_account_reproduction_admitted=False)
    (HERE/'source_witnesses.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
