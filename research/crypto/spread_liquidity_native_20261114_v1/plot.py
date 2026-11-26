"""Standalone research figure from verified native account/sleeve NAV only."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    if not json.loads((HERE/'native_validation.json').read_text())['passed']:
        raise ValueError('Verified native accounts required')
    palette={'EdgeLiquidity':'#b26721','AmihudLiquidity':'#8a7252','LowVolBaseline':'#497294','Edge500+LowVol500':'#573b71'}
    fig,axes=plt.subplots(1,2,figsize=(13.6,5.5),sharey=True)
    for ax,cost in zip(axes,[15,8],strict=True):
        for strategy in ['EdgeLiquidity','AmihudLiquidity','LowVolBaseline']:
            frame=pd.read_parquet(out/f'{strategy}_1000_{cost}bps/hourly_equity.parquet')
            # Daily display preserves the actual first and final hourly observations.
            series=frame.set_index('date').hourly_mark_equity
            display=pd.concat([series.iloc[[0]],series.resample('D').last(),series.iloc[[-1]]])
            display=display.loc[~display.index.duplicated()].sort_index()
            ax.plot(display.index,display,label=strategy,color=palette[strategy],linewidth=1.5)
        combo=pd.read_parquet(HERE/f'EdgeLowVol_{cost}bps.parquet').equity
        display=pd.concat([combo.iloc[[0]],combo.resample('D').last(),combo.iloc[[-1]]])
        display=display.loc[~display.index.duplicated()].sort_index()
        ax.plot(display.index,display,label='Edge500+LowVol500',color=palette['Edge500+LowVol500'],linewidth=2.3,linestyle='--')
        ax.axhline(1000,color='#5b5b5b',linewidth=.8,linestyle=':')
        ax.set_title(f'{cost} bps per side',fontsize=12)
        ticks=pd.to_datetime(['2025-01-01','2025-07-01','2026-01-01','2026-08-31'],utc=True)
        ax.set_xticks(ticks,labels=['Jan 1\n2025','Jul 1\n2025','Jan 1\n2026','Aug 31\n2026'])
        ax.get_xticklabels()[0].set_horizontalalignment('left')
        ax.get_xticklabels()[-1].set_horizontalalignment('right')
        ax.set_xlim(pd.Timestamp(plan['start']),pd.Timestamp(plan['comparison_end']))
        ax.set_ylim(bottom=0);ax.grid(axis='y',color='#dddddd',linewidth=.5)
        ax.spines[['top','right']].set_visible(False)
        ax.tick_params(labelsize=10)
    axes[0].set_ylabel('Full-account marked NAV (USDT)',fontsize=11)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.91),ncol=4,frameon=False,fontsize=10)
    fig.suptitle('Liquidity transfer: native continuous accounts',fontsize=16,y=.99)
    fig.text(.06,.025,'Jan 1 2025 00:00 - Aug 31 2026 23:00 UTC | 1,000 USDT initial | six fixed perpetuals\nActual hourly mark OPEN before orders; close fee reserved. Combo uses two actual 500-USDT wallets. No OOS or live validation.',fontsize=9,color='#444444')
    fig.subplots_adjust(top=.80,bottom=.22,left=.07,right=.98,wspace=.10)
    fig.savefig(HERE/'native_account_comparison.png',dpi=150,facecolor='white')
    plt.close(fig)


if __name__=='__main__':main()
