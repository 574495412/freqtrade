"""Plot actual native funded accounts; no rescaled sleeve approximation."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
HERE=Path(__file__).resolve().parent;OUT=HERE.parents[2]/'user_data/crypto_quant/positioning_month_native_20261114_v1'
fig,axes=plt.subplots(2,1,figsize=(12,8.5),sharex=True,gridspec_kw={'height_ratios':[2,1]})
views={}
for name,label in [('LowCrowdMonth','Low account-ratio month'),('LowPositionMonth','Low position-ratio control'),('ConstantPositioningControl','Constant gross control')]:
 views[label]=pd.read_parquet(OUT/f'{name}_1000_15bps/hourly_equity.parquet').set_index('date').hourly_mark_equity
views['Crowd500 + Position500']=pd.read_parquet(HERE/'combo_15bps.parquet').hourly_mark_equity
for (label,nav),color in zip(views.items(),['#047857','#7c3aed','#64748b','#0284c7'],strict=True):
 daily=nav.resample('1D').last();dd=(nav/nav.cummax()-1)*100
 axes[0].plot(daily.index,daily,color=color,label=label,linewidth=1.7)
 axes[1].plot(dd.resample('1D').last().index,dd.resample('1D').last(),color=color,linewidth=1.3)
for ax in axes:
 ax.axvspan(pd.Timestamp('2025-01-01',tz='UTC'),pd.Timestamp('2025-02-17',tz='UTC'),color='#f59e0b',alpha=.1)
 ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.18)
axes[0].axhline(1000,color='#94a3b8',linestyle=':',linewidth=1)
axes[1].axhline(-30,color='#475569',linestyle='--',linewidth=1)
axes[0].set_title('BTC / ETH / SOL | native funded comparison, 15 bps per side + funding',loc='left',fontsize=13,fontweight='bold',pad=48)
axes[0].legend(ncol=2,frameon=False,loc='lower left',bbox_to_anchor=(0,1.01),fontsize=9)
axes[0].set_ylabel('NAV (USDT)');axes[1].set_ylabel('Drawdown (%)')
fig.text(.08,.025,'Actual 1000 wallets; combination sums two actual 500 wallets. Daily last points shown; gates use 14,592 hourly NAVs.\nAmber: 47 days of unknown ranks from missing 2024-02-16 snapshot. Unknown-input policy results are not a clean alpha claim.',fontsize=9,color='#475569')
fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(HERE/'native_nav_15bps.png',dpi=150);plt.close(fig)
