"""Plot complete native NAV observations with equal axes; no fitted account paths."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'user_data/crypto_quant/candle_boundary_native_20261114_v1'

def main():
    assert json.loads((HERE/'verification.json').read_text())['passed_within_scope']
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),sharex=True,sharey=True)
    labels=[('BoundaryStartMinute','Boundary start', '#007f86'),('BoundaryEndMinute','Boundary end','#c47718'),('ConstantMinuteControl','Continuous long','#4556a0')]
    for ax,cost in zip(axes,[15,8],strict=True):
        for name,label,color in labels:
            f=pd.read_parquet(OUT/f'{name}_{cost}bps/hourly_equity.parquet')
            ax.plot(f.date,f.hourly_mark_equity,label=label,color=color,lw=1.3,ls='--'if name=='BoundaryEndMinute'else'-')
        ax.axhline(1000,color='#777',lw=.6,ls=':');ax.set_title(f'{cost} bps per side + native funding');ax.grid(alpha=.17);ax.tick_params(axis='x',rotation=25);ax.set_ylim(0,1600)
    axes[0].set_ylabel('Actual 1,000 USDT account NAV');axes[1].legend(frameon=False,loc='upper right')
    fig.suptitle('15-minute calendar interpretations: complete native account window',fontsize=13)
    fig.text(.5,.015,'2025-01-01 to 2026-08-31 UTC | hourly pre-order MARK OPEN, remaining exit fee reserved | no wallet reset',ha='center',fontsize=8)
    fig.tight_layout(rect=[0,.045,1,.95]);fig.savefig(HERE/'account_nav.png',dpi=160);plt.close(fig)
if __name__=='__main__':main()
