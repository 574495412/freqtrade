"""Audit only workers authoritatively finished with successful receipts."""
import json
from pathlib import Path

import pandas as pd

from research.crypto.pairs_cointegration_native_20261114_v1.analyze import audit_one,inputs

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text())
    grid=pd.date_range(plan['start'],plan['comparison_end'],freq='h').as_unit('ns');data=inputs(plan,grid)
    for candidate in plan['candidates']:
        for cost in plan['total_cost_bps_per_side']:
            path=Path(plan['output_root'])/f'{candidate["name"]}_{cost}bps'
            receipt=path/'process_receipt.json'
            if not receipt.exists():continue
            if json.loads(receipt.read_text())['returncode']!=0:continue
            if (path/'pair_source_audit.json').exists():continue
            audit_one(plan,candidate,cost,data,grid)


if __name__=='__main__':main()
