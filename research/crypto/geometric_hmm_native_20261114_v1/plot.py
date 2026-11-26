"""Static export of actual funded NAV; hourly metrics stay in results.json."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
HERE=Path(__file__).resolve().parent;OUT=HERE.parents[2]/'user_data/crypto_quant/geometric_hmm_native_20261114_v1'
fig,axes=plt.subplots(2,1,figsize=(12,8.5),sharex=True,gridspec_kw={'height_ratios':[2,1]})
colors=['#065f46','#d97706','#7c3aed','#64748b','#0284c7','#db2777']
series={}
for key,label in [('SourceGeoHmmBTC','Joint signal'),('GeoOnlyBTC','Geometric'),('HmmOnlyBTC','HMM'),('ConstantGrossBTC','Constant gross target')]:
 f=pd.read_parquet(OUT/f'{key}_1000_15bps/hourly_equity.parquet').set_index('date').hourly_mark_equity
 series[label]=f
for key,label in [('separate_components','Geo500 + HMM500'),('source_plus_constant','Joint500 + Constant500')]:
 series[label]=pd.read_parquet(HERE/f'combo_{key}_15bps.parquet').hourly_mark_equity
for (label,nav),color in zip(series.items(),colors,strict=True):
 daily=nav.resample('1D').last()
 axes[0].plot(daily.index,daily,color=color,label=label,linewidth=1.6)
 dd=nav/nav.cummax()-1;axes[1].plot(dd.resample('1D').last().index,100*dd.resample('1D').last(),color=color,linewidth=1.2)
axes[0].axhline(1000,color='#94a3b8',linestyle=':',linewidth=1)
axes[1].axhline(-30,color='#475569',linestyle='--',linewidth=1,label='30% gate')
axes[0].set_title('BTC native funded comparison | 15 bps per side + native funding',loc='left',fontsize=14,fontweight='bold',pad=49)
axes[0].set_ylabel('Account NAV (USDT)');axes[1].set_ylabel('Drawdown (%)')
axes[0].legend(ncol=3,frameon=False,fontsize=9,loc='lower left',bbox_to_anchor=(0,1.01))
for a in axes:a.spines[['top','right']].set_visible(False);a.grid(axis='y',alpha=.18)
fig.text(.08,.025,'2025-01-01 00:00 to 2026-08-31 23:00 UTC. Continuous actual wallets; combos sum two actual500 accounts.\nDaily last points shown; gates use all14,592 hourly pre-order mark OPEN NAVs. No terminal liquidation substituted.',fontsize=9,color='#475569')
fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(HERE/'native_nav_15bps.png',dpi=150);plt.close(fig)
