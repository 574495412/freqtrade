"""Audit only terminal successful accounts while the serial matrix continues."""
import json
from pathlib import Path

import pandas as pd

from research.crypto.tsmom_risk_native_20261114_v1.analyze import check_account, inputs
from research.crypto.band_trend_native_20261113_v1.analyze import read_native
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    report = HERE/'v002'
    plan = json.loads((report/'plan.json').read_text())
    grid = pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns')
    data = inputs(plan,grid)
    records = []
    for candidate in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            key = f'{candidate["name"]}_{cost}bps'
            path = Path(plan['output_root'])/key
            if not (path/'process_receipt.json').exists():
                continue
            receipt = json.loads((path/'process_receipt.json').read_text())
            if receipt['returncode'] != 0:
                raise ValueError('Failed native account: '+key)
            assert sha(path/'result.json') == receipt['result_sha256']
            stats,archive,cfg = read_native(path,candidate['strategy'])
            assert cfg['fee'] == cost/10000 and cfg['dry_run_wallet'] == candidate['capital']
            _, marked, rules = check_account(plan,candidate,path,stats,archive,
                                             pd.read_parquet(path/'hourly_equity.parquet'),data)
            records.append(dict(account=key,maximum_nav_error=marked['maximum_nav_error_usdt'],
                                filled_orders=marked['filled_orders'],
                                tia_gap_exposure=marked['tia_missing_event_actual_exposure'],
                                budget_error=rules['maximum_marked_budget_error_usdt']))
            print('AUDITED READY',key,'NAV error',marked['maximum_nav_error_usdt'],flush=True)
    save(report/'audit_progress.json',dict(successfully_audited=len(records),planned_unique_accounts=12,
                                          overall_completion_claim=False,records=records))


if __name__ == '__main__':
    main()
