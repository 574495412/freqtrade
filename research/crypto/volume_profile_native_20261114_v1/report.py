"""Standalone audited account figure; actual1000 and500+500 wallets only."""
import json
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
HERE=Path(__file__).resolve().parent

def main():
    p=json.loads((HERE/'plan.json').read_text());out=Path(p['output_root']);old=json.loads((HERE.parent/'freqai_rf_native_20261114_v1/v002/plan.json').read_text())
    fig,axes=plt.subplots(2,1,figsize=(12,9),sharex=True,sharey=True)
    for ax,cost in zip(axes,[15,8],strict=True):
        curves=[]
        for name,label,color in [('ProfileBreakout','Breakout 3x cap','#a04435'),('ProfileFade','Fade 3x cap','#aa7a24'),('ProfileNoVolume','No-volume control 3x cap','#916ba7')]:
            f=pd.read_parquet(out/f'{name}_1000_{cost}bps/hourly_equity.parquet');curves.append((f,label,color,'-'))
        f=pd.read_parquet(Path(old['output_root'])/f'SmaPriceBTC_1000_{cost}bps/hourly_equity.parquet');curves.append((f,'SMA contextual 1x','#31806d','-'))
        for a,b,label,color,style in [('ProfileBreakout','ProfileFade','Actual Breakout500 + Fade500','#344e95','--'),('ProfileBreakout','SmaPriceBTC','Actual Breakout500 + SMA500','#4e9ba8',':')]:
            f=pd.read_parquet(HERE/f'combo_{a}_500_plus_{b}_500_{cost}bps.parquet');curves.append((f,label,color,style))
        for f,label,color,style in curves:
            nav=f.hourly_mark_equity;ax.plot(f.date,nav,color=color,ls=style,lw=1.2,label=f'{label}: {nav.iloc[-1]/nav.iloc[0]-1:+.2%}')
        ax.axhline(1000,color='#a6abb0',ls=':',lw=.8);ax.grid(alpha=.17);ax.spines[['top','right']].set_visible(False);ax.set_ylabel('Native mark NAV (USDT)')
        ax.set_title(f'{cost} bps per side + native funding',loc='left',fontsize=11,y=1.25,pad=0)
        ax.legend(loc='lower left',bbox_to_anchor=(0,1.015),ncol=3,fontsize=8.1,frameon=False,borderaxespad=0)
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'));axes[-1].set_xlim(pd.Timestamp(p['start']),pd.Timestamp(p['comparison_end']))
    fig.suptitle('Native volume-profile policies — no qualifying return improvement',x=.075,ha='left',fontsize=15)
    fig.text(.075,.036,'2025-01-01 00:00 to 2026-08-31 23:00 UTC · complete continuous funded wallets · pre-order hourly MARK OPEN\nProfile strategies: planned 1% price risk, 3x notional cap. SMA: contextual 1x. Source candle histogram is not tick/order-flow volume.',fontsize=9,color='#555')
    fig.subplots_adjust(left=.075,right=.98,top=.84,bottom=.13,hspace=.60);fig.savefig(HERE/'account_curves.png',dpi=160)
if __name__=='__main__':main()
