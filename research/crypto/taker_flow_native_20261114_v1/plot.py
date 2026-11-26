"""Standalone descriptive IC/IR figure from actual native FA artifacts."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent


def main():
    data=json.loads((HERE/'predictive_diagnostics.json').read_text())
    rows=[r for r in data['receipts'] if r['split']=='all']
    fig,axes=plt.subplots(1,2,figsize=(10.6,4.8))
    labels=['CVD20','Mean imbalance\n20 hours','Trade-size z\n20 hours']
    for ax,field,threshold,bounds,title in zip(axes,['ic_mean','ir'],[.03,.5],[(-.01,.04),(-.06,.60)],['Spearman IC mean','IC mean / sample SD'],strict=True):
        values=[r['result'][field] for r in rows]
        ax.bar(labels,values,color=['#5b7289','#8a9aaa','#b2bcc6'],width=.55)
        ax.axhline(0,color='#555555',linewidth=.8)
        ax.axhline(threshold,color='#8a5c28',linestyle='--',linewidth=1.1,label=f'Declared gate > {threshold}')
        ax.set_ylim(*bounds)
        for i,value in enumerate(values):ax.text(i,value-(bounds[1]-bounds[0])*.015,f'{value:.4f}',ha='center',va='top',fontsize=10)
        ax.set_title(title,fontsize=12);ax.legend(frameon=False,loc='upper right',fontsize=9)
        ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',color='#dddddd',linewidth=.5);ax.set_axisbelow(True)
    fig.suptitle('Hourly aggressor flow: predictive admission',fontsize=15,y=.99)
    fig.text(.07,.035,'29 fixed perps | 3,647 non-overlapping four-hour price labels | Jan 1 2025 - Aug 31 2026\nExact source feature kernel on canonical 5m tape. Gross diagnostics; no funded account return or live validation.',fontsize=9,color='#444444')
    fig.subplots_adjust(left=.08,right=.97,bottom=.28,top=.84,wspace=.24)
    fig.savefig(HERE/'native_flow_diagnostics.png',dpi=150,facecolor='white');plt.close(fig)


if __name__=='__main__':main()
