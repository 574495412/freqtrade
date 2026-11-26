"""Audited full-account research figure; matched models and contextual controls."""
import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    fig,axes=plt.subplots(2,1,figsize=(12,9),sharex=True,sharey=True)
    for ax,cost in zip(axes,[15,8]):
        for strategy,label,color in [('RollingRF','RF','#bc443a'),('RollingPrior','Training prior','#d98c25'),('SmaPriceBTC','SMA10/20','#24796b'),('HoldBTC','Hold BTC','#85818c')]:
            f=pd.read_parquet(out/f'{strategy}_1000_{cost}bps/hourly_equity.parquet')
            nav=f.hourly_mark_equity
            ax.plot(f.date,nav,color=color,lw=1.1,label=f'{label}: {nav.iloc[-1]/nav.iloc[0]-1:+.2%}')
        for strategy,label,color,style in [('RollingRF','Actual RF500 + SMA500','#3253a3','--'),('RollingPrior','Actual Prior500 + SMA500','#8c4c96',':')]:
            f=pd.read_parquet(HERE/f'combo_{strategy}_{cost}bps.parquet');nav=f.hourly_mark_equity
            ax.plot(f.date,nav,color=color,ls=style,lw=1.4,label=f'{label}: {nav.iloc[-1]/nav.iloc[0]-1:+.2%}')
        ax.set_title(f'{cost} bps per side + native funding',loc='left',fontsize=11,y=1.25,pad=0)
        ax.legend(loc='lower left',bbox_to_anchor=(0,1.015),ncol=3,fontsize=8.3,frameon=False,borderaxespad=0)
        ax.axhline(1000,color='#adb1b6',ls=':',lw=.7);ax.grid(alpha=.18);ax.spines[['top','right']].set_visible(False)
        ax.set_ylabel('Native wallet NAV (USDT)')
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    axes[-1].set_xlim(pd.Timestamp(plan['start']),pd.Timestamp(plan['comparison_end']))
    fig.suptitle('Native rolling FreqAI — no qualifying return improvement',x=.075,ha='left',fontsize=15)
    fig.text(.075,.036,'2025-01-01 00:00 to 2026-08-31 23:00 UTC · 1× isolated BTC perpetual model · hourly mark OPEN before orders\nRF/Prior share the 5% stop and 4h timeout; SMA/Hold are contextual strategy benchmarks. Native terminal forced exits remain separate.',fontsize=9,color='#555')
    fig.subplots_adjust(left=.075,right=.98,top=.84,bottom=.13,hspace=.60)
    fig.savefig(HERE/'account_curves.png',dpi=160)


if __name__=='__main__':main()
