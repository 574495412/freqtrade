"""Descriptive predictive figure; no account return chart is manufactured."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE=Path(__file__).resolve().parent


def main():
    data=json.loads((HERE/'predictive_diagnostics_v3.json').read_text())
    fig,axes=plt.subplots(1,2,figsize=(10.8,4.6))
    labels=['All','2025','2026 to Aug 24']
    for ax,field,threshold,title in zip(axes,['ic_mean','ir'],[.03,.5],['Spearman IC mean','IC mean / sample SD'],strict=True):
        values=[r['result'][field] for r in data['receipts']]
        ax.bar(labels,values,color=['#735d81','#9d8da5','#c1b7c7'],width=.55)
        ax.axhline(threshold,color='#57412b',linewidth=1.1,linestyle='--',label=f'Declared gate > {threshold}')
        ax.set_ylim(0,max(threshold*1.3,max(values)*1.45))
        for i,value in enumerate(values):ax.text(i,value+ax.get_ylim()[1]*.025,f'{value:.4f}',ha='center',fontsize=11)
        ax.set_title(title,fontsize=12);ax.legend(frameon=False,loc='upper right',fontsize=9)
        ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',color='#dddddd',linewidth=.5);ax.set_axisbelow(True)
    fig.suptitle('Funding carry: pre-financial predictive diagnostics',fontsize=15,y=.99)
    fig.text(.07,.035,'29 source-active perps | 120 source decision dates | seven-day forward perp OPEN labels overlap\nRaw native features; TIA missing funding remains UNKNOWN. These are gross diagnostics, not funded account returns.',fontsize=9,color='#444444')
    fig.subplots_adjust(left=.07,right=.97,bottom=.25,top=.84,wspace=.25)
    fig.savefig(HERE/'predictive_diagnostics.png',dpi=150,facecolor='white');plt.close(fig)


if __name__=='__main__':main()
