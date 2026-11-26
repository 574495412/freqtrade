"""Standalone research figure from audited native NAVs,not audit-generated NAV."""

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

HERE=Path(__file__).resolve().parent


def main():
    results=json.loads((HERE/'results.json').read_text())
    reference=next(r for r in results['control_references'] if r['name']=='nfi_spot_1000_conservative')
    series=[('Heracles source / 1000',HERE/'heracles_1000_conservative.parquet','#b54b46'),
            ('NFI spot / 1000',Path(reference['path']),'#286b68'),
            ('Heracles 500 + NFI spot 500',HERE/'heracles_nfi_spot_1000_conservative.parquet','#545d8c')]
    fig,axes=plt.subplots(2,1,figsize=(10,6.8),sharex=True,gridspec_kw={'height_ratios':[2,1]},layout='constrained')
    lineage=[]
    for label,path,color in series:
        frame=pd.read_parquet(path).set_index('date')
        values=frame.hourly_mark_equity
        assert len(values)==14592 and values.iloc[0]==1000
        axes[0].plot(values.index,values,color=color,label=label,linewidth=1.25)
        axes[1].plot(values.index,(1-values/values.cummax())*100,color=color,linewidth=1.1)
        lineage.append({'label':label,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    axes[0].axhline(1000,color='#737373',linestyle=':',linewidth=.8)
    axes[0].set_ylabel('Marked net account value (USDT)')
    axes[0].set_title('BTC/ETH spot | same 1000 USDT initial capital | 20 bps per side',loc='left',fontsize=13,pad=12)
    axes[0].legend(loc='upper left',frameon=False,fontsize=9)
    axes[1].set_ylabel('Hourly drawdown (%)')
    axes[1].set_ylim(0,65)
    axes[1].set_xlabel('UTC | 2025-01-01 through 2026-08-31 23:00')
    for ax in axes:
        ax.grid(axis='y',alpha=.2)
        ax.spines[['top','right']].set_visible(False)
    fig.text(.01,-.018,'Historical model only. Actual native fills/fees and remaining exit-fee reserve; no transfers or scaled 1000 curves. Hourly risk is not intrahour worst loss.',fontsize=8,color='#555555')
    fig.savefig(HERE/'matched_spot_nav.png',dpi=170,bbox_inches='tight',facecolor='white')
    (HERE/'figure_sources.json').write_text(json.dumps(lineage,indent=2)+'\n')


if __name__=='__main__':main()
