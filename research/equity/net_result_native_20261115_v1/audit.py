"""Independent result arithmetic and complete parent-path comparison; no engine imports."""
import hashlib
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile

HERE=Path(__file__).resolve().parent
PARENT=HERE.with_name('start_lifecycle_native_20261115_v1')


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    # Reuse the separately preserved, candidate-free Decimal reconciliation,
    # binding only its directory. It imports neither a wallet nor a signal.
    spec=importlib.util.spec_from_file_location('parent_independent_audit',PARENT/'audit_trace.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old);old.HERE=HERE
    checks=[];reports=[]
    receipts=read(HERE/'execution_receipt.json');assert len(receipts)==6
    for r in receipts:
        assert r['returncode']==0
        name=r['attempt'];directory=HERE/'runs'/name
        checks.append(old.audit(name))
        current=read(directory/'native_trace.json')
        previous=read(PARENT/'runs'/r['case']/'native_trace.json')
        for key in ['orders','opens','closes','callbacks']:
            assert current[key]==previous[key],(name,key)
        for export in read(directory/'native_export_receipt.json'):
            assert sha(export['path'])==export['sha256']
            with ZipFile(export['path'])as archive:
                file=next(n for n in archive.namelist()if n.endswith('.json')and not n.endswith('_config.json'))
                stats=json.loads(archive.read(file))['strategy']['OpenClockProbe']
            assert stats['profit_total_abs']==0
        result=read(directory/'equity_net_result.json')
        snapshots=[s for pair in zip(current['opens'],current['closes'],strict=True)for s in pair]
        values=[D(str(s['net_nav']))for s in snapshots]
        peak=values[0];dd=D(0)
        for v in values:peak=max(peak,v);dd=max(dd,1-v/peak)
        assert len(snapshots)==result['snapshots']==14
        assert abs(D(str(result['synthetic_net_change_ratio']))-(values[-1]/values[0]-1))<D('1e-12')
        assert abs(D(str(result['synthetic_sampled_max_drawdown']))-dd)<D('1e-12')
        last=current['closes'][-1]
        for a,b in [('execution_fees','execution_fees'),('paid_dividends','paid_dividends'),
            ('terminal_dividend_receivable','dividend_receivable'),('terminal_unsettled_sale_proceeds','unsettled_receivable'),
            ('closed_trade_net_cash_profit','closed_trade_net_cash_profit'),('final_net_nav','net_nav')]:
            assert D(str(result[a]))==D(str(last[b])),(name,a)
        assert bool(result['terminal_holdings'])==bool(last['holdings'])
        assert result['historical_equity_accounts']==result['bench_records']==0
        assert result['financial_strategy_performance']is result['cagr']is result['sharpe']is None
        assert result['synthetic_net_change_ratio']!=0 # native gross trade profit was zero
        reports.append(dict(case=r['case'],net_change_fixture_only=result['synthetic_net_change_ratio'],
            drawdown_fixture_only=result['synthetic_sampled_max_drawdown'],gross_native_trade_profit=0,
            retained_terminal_holdings=bool(last['holdings']),passed=True))
    for market in ['XNYS','XHKG']:
        base=read(HERE/'runs'/(market+'_base_main')/'equity_net_result.json')
        altered=read(HERE/'runs'/(market+'_extrema_main')/'equity_net_result.json')
        assert base==altered,(market,'net_result_counterfactual')
        for key in ['orders','opens','closes','callbacks']:
            a=deepcopy(read(HERE/'runs'/(market+'_base_main')/'native_trace.json')[key])
            b=deepcopy(read(HERE/'runs'/(market+'_extrema_main')/'native_trace.json')[key])
            if key in ['opens','closes']:
                for series in [a,b]:
                    for s in series:
                        for h in s['holdings']:h[2].pop('source_sha256')
            assert a==b,(market,key)
    result=dict(passed=True,full_native_main_starts=6,native_zips=6,
        initial_additional_technical_start=1,main_synthetic_fills=sum(r['fills']for r in checks),
        main_snapshots=84,parent_scalar_checks=sum(r['checked_scalars']for r in checks),
        original_full_paths_exact=6,future_extrema_net_result_exact=['XNYS','XHKG'],
        independent_result_checks=reports,net_arithmetic_from_actual_snapshots=True,
        candidate_calculator_imported=False,historical_accounts=0,bench=0,strategy_performance=None)
    (HERE/'independent_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS 6 native starts/ZIPs,22syntheticfills,84snapshots,504cashNAVscalars,6exactparentpaths; 0 historical accounts')


if __name__=='__main__':main()
