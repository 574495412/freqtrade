"""Reproducible figure from audited actual native accounts only."""
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    here = Path(__file__).resolve().parent / 'v002'
    result = json.loads((here / 'comparison.json').read_text())
    figure, axis = plt.subplots(figsize=(9.5, 5), layout='constrained')
    categories, width = np.array([0, 1]), .24
    series = [('AnnualSkew_1000', 'Annual skewness', '#3772A6'),
              ('LowVolControl_1000', 'Low-vol control', '#3C956E'),
              ('two_actual_500_wallets', 'Two native500 sleeves', '#8758A8')]
    for index, (key, label, color) in enumerate(series):
        values = [next(row['net_return'] for row in result['accounts'] + result['combinations']
                       if row['account'] == key and row['cost_bps_per_side'] == cost) * 100
                  for cost in [15, 8]]
        bars = axis.bar(categories + (index - 1) * width, values, width, label=label, color=color)
        for bar, value in zip(bars, values, strict=True):
            axis.text(bar.get_x() + bar.get_width() / 2, value + (1 if value > 0 else -1),
                      f'{value:.2f}%', ha='center', va='bottom' if value > 0 else 'top', fontsize=10)
    axis.set_xticks(categories, ['15 bps / side', '8 bps / side'])
    axis.set_ylim(-28, 31)
    axis.set_ylabel('Conditional model cumulative net return (%)')
    axis.set_title('Low correlation did not improve return over the low-volatility control',
                   loc='left', fontsize=12, pad=46)
    axis.text(0, 1.07, '2025-01-01 00:00 to 2026-08-31 23:00 UTC | fixed40 | 1x | initial1,000 USDT',
              transform=axis.transAxes, fontsize=9, color='#555555')
    axis.text(0, 1.025, 'Unresolved AXS/TRB funding slots: complete actual settlement economics UNKNOWN',
              transform=axis.transAxes, fontsize=9, color='#555555')
    axis.axhline(0, color='#777777', linewidth=.7)
    axis.grid(axis='y', alpha=.2)
    axis.set_axisbelow(True)
    axis.spines[['top', 'right']].set_visible(False)
    axis.legend(loc='upper center', bbox_to_anchor=(.5, -.12), frameon=False, ncol=3, fontsize=9)
    figure.savefig(here / 'account-comparison.png', dpi=160)


if __name__ == '__main__':
    main()
