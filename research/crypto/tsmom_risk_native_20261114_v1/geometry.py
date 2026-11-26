"""Scoped geometry of actual native weights, not simulated portfolio returns."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE/'v002/plan.json').read_text())
    root = Path(plan['output_root'])
    names = ['ClockedCovarianceRiskTS','ClockedDiagonalRiskTS','ClockedGrossOneRiskTS']
    paths = {n:root/f'preflight_weights_{n}.parquet' for n in names}
    weights = {n:pd.read_parquet(p) for n,p in paths.items()}
    rows = []
    for i,left in enumerate(names):
        for right in names[i+1:]:
            a,b = weights[left],weights[right]
            assert a.index.equals(b.index) and a.columns.equals(b.columns)
            correlation = a.corrwith(b,axis=1)
            cosine = (a*b).sum(axis=1)/np.sqrt((a*a).sum(axis=1)*(b*b).sum(axis=1))
            rows.append(dict(left=left,right=right,days=len(a),
                mean_abs_cross_sectional_weight_pearson=float(correlation.abs().mean()),
                minimum_weight_cosine=float(cosine.min()),maximum_weight_cosine=float(cosine.max())))
    save(HERE/'allocation_geometry.json',dict(
        scope='Three frozen risk allocations share one fixed TSMOM signal; not independent alpha factors',
        correlations=rows,inputs={str(p):sha(p) for p in paths.values()},
        not_all_registry_dedup=True,not_funded_return_correlations=True,
        calculation_source={'path':str(Path(__file__).resolve()),'sha256':sha(__file__)}))


if __name__ == '__main__':
    main()
