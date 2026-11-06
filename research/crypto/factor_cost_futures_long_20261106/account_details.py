"""Complete saved-account exposure, holding-age and fixed-fill attribution metrics."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def describe(registered, audit):
    directory = ROOT / 'user_data/crypto_quant/factor_cost_scenario_20261106' / registered['id']
    assert sha(directory / 'result.json') == audit['result_sha256']
    assert sha(directory / 'artifact_hashes.json') == audit['artifact_manifest_sha256']
    for name, digest in read(directory / 'artifact_hashes.json').items():
        assert sha(directory / name) == digest, name
    curve = pd.read_parquet(directory / 'model_equity.parquet').set_index('date')
    endpoint = curve.index[-1]
    archives = list((directory / 'native_results').glob('*.zip'))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        names = [n for n in archive.namelist() if n.endswith('.json') and not n.endswith('_config.json')]
        assert len(names) == 1
        trades = json.loads(archive.read(names[0]))['strategy'][registered['strategy']]['trades']
    campaigns = []
    cash_without_costs = terminal_gross = reserve = fees = 0.
    marks = {}
    for trade in trades:
        assert not trade['is_short'] and trade['leverage'] == 1
        orders = trade['orders'][:-1] if trade['exit_reason'] == 'force_exit' else trade['orders']
        # The saved curve observes each clock before events at that same clock.
        orders = [o for o in orders if pd.Timestamp(o['order_filled_timestamp'], unit='ms', tz='UTC') < endpoint]
        if not orders:
            continue
        quantity = 0.
        for order in orders:
            entry = order['ft_is_entry']
            amount = float(order['amount'])
            notional = amount * float(order['safe_price'])
            quantity += amount * (1 if entry else -1)
            cash_without_costs += notional * (-1 if entry else 1)
            fees += notional * float(trade['fee_open'] if entry else trade['fee_close'])
        if abs(quantity) < 1e-9:
            quantity = 0.
        assert quantity >= 0
        entry_fills = sum(o['ft_is_entry'] for o in orders)
        assert entry_fills == 1, 'Frozen no-DCA account violated'
        opened = pd.Timestamp(orders[0]['order_filled_timestamp'], unit='ms', tz='UTC')
        closed = pd.Timestamp(orders[-1]['order_filled_timestamp'], unit='ms', tz='UTC') if quantity == 0 else None
        age = ((closed if closed is not None else endpoint) - opened).total_seconds() / 3600
        assert age >= 0
        if quantity:
            pair = trade['pair']
            if pair not in marks:
                marks[pair] = pd.read_feather(ROOT / registered['mark_paths'][pair]).set_index('date').open.loc[endpoint]
            value = quantity * float(marks[pair])
            terminal_gross += value
            reserve += value * float(trade['fee_close'])
        campaigns.append(dict(pair=trade['pair'], opened_at=opened.isoformat(),
            natural_closed_at=closed.isoformat() if closed is not None else None,
            holding_hours_or_endpoint_age=age, endpoint_open=quantity > 0,
            endpoint_quantity=quantity, entry_fills=entry_fills, DCA_orders=entry_fills-1,
            partial_exit_fills=sum(not o['ft_is_entry'] for o in orders)-(closed is not None),
            exit_reason=trade['exit_reason']))
    funding = audit['audit']['terminal_projected_funding']
    net_pnl = float(curve.equity.iloc[-1] - 1000.)
    fixed_fill_price_pnl = cash_without_costs + terminal_gross
    error = abs(fixed_fill_price_pnl-fees+funding-reserve-net_pnl)
    assert error < 1e-4
    assert abs(terminal_gross-float(curve.gross.iloc[-1])) < 1e-4
    assert abs(fees-audit['audit']['fees_before_endpoint']) < 1e-4
    ages = [c['holding_hours_or_endpoint_age'] for c in campaigns if not c['endpoint_open']]
    fractions = curve.gross / curve.equity
    warning = curve.warning.astype(bool)
    result = dict(run_id=registered['id'], result_sha256=audit['result_sha256'],
        detail_source_sha256=sha(Path(__file__)), curve_sha256=sha(directory / 'model_equity.parquet'),
        exposure=dict(direction='long_only', short_exposure_usdt=0., net_equals_gross=True,
            maximum_gross_usdt=float(curve.gross.max()), maximum_gross_over_equity=float(fractions.max()),
            mean_gross_over_equity=float(fractions.mean()),
            exposed_snapshot_fraction=float((curve.gross > 1e-9).mean()),
            maximum_open_campaigns=int(curve.open_trades.max()),
            snapshot_observations=len(curve), snapshot_resolution_minutes=5,
            intrabar_exposure_certified=False),
        risk_state=dict(first_warning_at=str(curve.index[warning][0]) if warning.any() else None,
            warning_snapshot_count=int(warning.sum()),
            warned_and_flat_snapshots=int((warning & (curve.gross <= 1e-9)).sum()),
            day_halt_snapshot_count=int(curve.day_halt.sum()), hard_halt_snapshot_count=int(curve.hard_halt.sum())),
        holding=dict(natural_completed_campaigns=len(ages), terminal_open_campaigns=sum(c['endpoint_open'] for c in campaigns),
            completed_mean_hours=float(np.mean(ages)) if ages else None,
            completed_median_hours=float(np.median(ages)) if ages else None,
            completed_maximum_hours=max(ages) if ages else None,
            oldest_terminal_open_hours=max((c['holding_hours_or_endpoint_age'] for c in campaigns if c['endpoint_open']), default=None),
            total_DCA_orders=sum(c['DCA_orders'] for c in campaigns),
            native_exit_reason_counts=dict(Counter(c['exit_reason'] for c in campaigns)), campaigns=campaigns),
        fixed_fill_attribution=dict(price_pnl_before_costs_and_funding_usdt=fixed_fill_price_pnl,
            paid_modeled_cost_usdt=fees, modeled_funding_pnl_usdt=funding,
            remaining_exit_cost_reserve_usdt=reserve, net_marked_pnl_usdt=net_pnl,
            accounting_error_usdt=error,
            scope='Decomposition of these frozen fills only; not a zero-cost rerun or executable gross-profit claim'),
        RD1_applied=False, production_eligible=False, new_financial_runs=0)
    return result


def main():
    plan = read(HERE / 'plan.json')
    for name, digest in plan['input_hashes'].items():
        assert sha(ROOT / name) == digest, name
    (HERE / 'details').mkdir(exist_ok=True)
    for registered in plan['runs']:
        source = HERE / 'audits' / (registered['id'] + '.json')
        if not source.exists():
            print('PENDING_AUDIT', registered['id'])
            continue
        audit = read(source)
        target = HERE / 'details' / (registered['id'] + '.json')
        if target.exists():
            saved = read(target)
            assert saved['result_sha256'] == audit['result_sha256']
            assert saved['detail_source_sha256'] == sha(Path(__file__))
            print('ALREADY_DESCRIBED', registered['id'])
            continue
        result = describe(registered, audit)
        result['audit_sha256'] = sha(source)
        with target.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print('DESCRIBED', registered['id'], result['fixed_fill_attribution'])


if __name__ == '__main__':
    main()
