"""Research figure from audited native wallets; source cash account not replicated."""

import hashlib
import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    plan=json.loads((HERE/'plan.json').read_text());r=json.loads((HERE/'results.json').read_text())
    out=Path(plan['output_root']);fig,axes=plt.subplots(2,1,figsize=(11,9),sharex=True,sharey=True)
    sources=[]
    for ax,cost in zip(axes,[15,8]):
        for strategy,label,color in [('SourceDualBTC','Dual Thrust','#c64c3b'),('SmaPriceBTC','SMA10/20','#267a70'),('HoldBTC','Hold BTC','#8b8790')]:
            path=out/f'{strategy}_1000_{cost}bps/hourly_equity.parquet';f=pd.read_parquet(path)
            nav=f.hourly_mark_equity;value=nav.iloc[-1]/nav.iloc[0]-1
            ax.plot(f.date,nav,color=color,lw=1.2,label=f'{label}: {value:+.2%}')
            sources.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        path=HERE/f'combo_{cost}bps.parquet';f=pd.read_parquet(path);nav=f.hourly_mark_equity
        ax.plot(f.date,nav,color='#405db2',lw=1.4,ls='--',label=f'Actual 500 + 500 mix: {nav.iloc[-1]/nav.iloc[0]-1:+.2%}')
        sources.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        ax.axhline(1000,color='#adb0b4',lw=.7,ls=':');ax.set_title(f'{cost} bps per side + native funding',loc='left',fontsize=11,y=1.25,pad=0)
        ax.set_ylabel('Wallet NAV (USDT)');ax.grid(alpha=.18);ax.legend(loc='lower left',bbox_to_anchor=(0,1.015),fontsize=9,ncol=2,frameon=False,borderaxespad=0)
        ax.spines[['top','right']].set_visible(False)
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    axes[-1].set_xlim(pd.Timestamp(plan['start']),pd.Timestamp(plan['comparison_end']))
    fig.suptitle('BTC complete native wallet comparison — no qualifying improvement',fontsize=15,x=.08,ha='left')
    fig.text(.08,.035,'2025-01-01 00:00 to 2026-08-31 23:00 UTC · 1× isolated Binance perpetual model\nHourly mark OPEN before orders; remaining exit fee reserved. Forced terminal exits are separate. Not source GDAX spot replication.',fontsize=9,color='#555')
    fig.subplots_adjust(left=.08,right=.98,top=.84,bottom=.13,hspace=.6)
    fig.savefig(HERE/'account_curves.png',dpi=160)
    (HERE/'figure_sources.json').write_text(json.dumps(dict(audited_results_sha256=hashlib.sha256((HERE/'results.json').read_bytes()).hexdigest(),sources=sources),indent=2)+'\n')


if __name__=='__main__':main()
