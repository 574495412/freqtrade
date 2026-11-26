"""Read audited native equity curves; presentation only, never a strategy input."""

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


HERE = Path(__file__).resolve().parent


def main():
    result = json.loads((HERE/'results_v2.json').read_text())
    plan = json.loads((HERE/'plan_v2.json').read_text())
    curves = {}
    for c in plan['candidates']:
        if c['capital'] != 1000:
            continue
        path = Path(plan['output_root'])/(c['name']+f"_{c['cost_bps']}bps")/'hourly_equity.parquet'
        curves[c['name']] = pd.read_parquet(path).set_index('date').hourly_mark_equity
    for parent_name,prefix in [('trendcore_source_native_20261117_v1','trendcore'),
                               ('nfi_carry_matched_native_20261115_v1','nfi_spot')]:
        parent = json.loads((HERE.with_name(parent_name)/'plan.json').read_text())
        for c in parent['candidates']:
            if c['capital'] == 1000 and c['name'].startswith(prefix+'_'):
                path=Path(parent['output_root'])/(c['name']+f"_{c['cost_bps']}bps")/'hourly_equity.parquet'
                curves[c['name']]=pd.read_parquet(path).set_index('date').hourly_mark_equity
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(2,2,figsize=(12,7),sharex=True,layout='constrained',height_ratios=[1.5,1])
    for column,(scenario,cost) in enumerate([('conservative',20),('lower',13)]):
        for prefix,label,color in [('entropytrend','Entropy gate','#2166ac'),
                                   ('trendcore','Sealed trend','#d87024'),
                                   ('nfi_spot','Sealed NFI spot','#747c86')]:
            name=f'{prefix}_1000_{scenario}';nav=curves[name]
            report=next(r for r in result['accounts']if r['account']==name)['periods'][0]
            axes[0,column].plot(nav.index,nav,color=color,lw=1.25,
                                label=f"{label}: {report['net_return']:+.2%}")
            axes[1,column].plot(nav.index,100*(nav/nav.cummax()-1),color=color,lw=1.1,
                                label=f"{label}: {report['hourly_mark_drawdown']:.2%}")
        axes[0,column].set_title(f'{cost}bp per side: fee + execution-cost proxy')
        axes[0,column].legend(frameon=False,fontsize=9)
        axes[1,column].axhline(-15,color='#b83f48',ls='--',lw=.9,label='Frozen 15% gate')
        axes[1,column].legend(frameon=False,fontsize=8,loc='lower left')
        for ax in axes[:,column]:
            ax.grid(alpha=.18)
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        axes[1,column].set_ylim(-27, 1)
        axes[1,column].set_xlabel('UTC; full continuous window, no annual reset')
    axes[0,0].set_ylabel('Native hourly marked NAV (USDT; initial 1000)')
    axes[1,0].set_ylabel('Drawdown from running NAV peak (%)')
    fig.suptitle('Entropy gate: lower drawdown, cost-dependent return improvement\n'
                 'Both costs fail the frozen 15% drawdown gate',fontsize=14)
    fig.savefig(HERE/'account_curves.png',dpi=160)
    plt.close(fig)


if __name__=='__main__':
    main()
