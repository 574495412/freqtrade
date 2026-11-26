"""Render audited native curves and the established repository research record."""
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def pct(x):return f'{x*100:+.2f}%'


def main():
    result=json.loads((HERE/'results.json').read_text());audit=json.loads((HERE/'native_validation.json').read_text())
    assert audit['passed'];plan=json.loads((HERE/'plan.json').read_text());out=Path(plan['output_root'])
    labels=[('SourceNaivePairs','Source ADF pairs','#28649c','-'),
            ('AdjustedEGPairs','Adjusted EG pairs','#c57922','-'),
            ('TrendGrossOne','Trend control','#444b54','-'),
            ('combo_SourceNaivePairs','Source + trend (500+500)','#28649c','--'),
            ('combo_AdjustedEGPairs','Adjusted + trend (500+500)','#c57922','--')]
    fig,axes=plt.subplots(1,2,figsize=(15,7),sharey=True);series=[];minimum=np.inf;maximum=-np.inf
    for ax,cost in zip(axes,[15,8],strict=True):
        for key,label,color,style in labels:
            if key.startswith('combo_'):
                path=HERE/f'{key}_{cost}bps.parquet';nav=pd.read_parquet(path).hourly_mark_equity
            else:
                path=out/f'{key}_1000_{cost}bps/hourly_equity.parquet'
                nav=pd.read_parquet(path).set_index('date').hourly_mark_equity
            nav=nav.loc[plan['start']:plan['comparison_end']]
            assert len(nav)==14592 and nav.iloc[0]==1000
            displayed=pd.concat([nav.iloc[:1],nav.groupby(nav.index.floor('D')).tail(1)])
            displayed=displayed[~displayed.index.duplicated()]
            ax.plot(displayed.index,displayed.to_numpy(),label=label,color=color,ls=style,lw=1.7)
            minimum=min(minimum,float(nav.min()));maximum=max(maximum,float(nav.max()))
            series.append(dict(series=key,cost=cost,path=str(path),sha256=sha(path),hourly_points=len(nav),
                               displayed_points=len(displayed),first=float(nav.iloc[0]),last=float(nav.iloc[-1])))
        ax.set_title(f'{cost} bps per side',fontsize=13,pad=12)
        ticks=pd.to_datetime(['2025-01-01','2025-07-01','2026-01-01','2026-08-31'],utc=True)
        ax.set_xticks(ticks);ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.get_xticklabels()[0].set_ha('left')
        ax.get_xticklabels()[-1].set_ha('right')
        ax.set_xlim(pd.Timestamp(plan['start']),pd.Timestamp(plan['comparison_end']))
        ax.axhline(1000,color='#777777',ls=':',lw=.8);ax.grid(axis='y',alpha=.2)
        ax.spines[['top','right']].set_visible(False);ax.tick_params(labelsize=10)
        ax.set_xlabel('UTC date',fontsize=11)
    padding=(maximum-minimum)*.08;axes[0].set_ylim(minimum-padding,maximum+padding)
    axes[0].set_ylabel('Marked NAV (USDT; initial 1,000)',fontsize=11)
    handles,names=axes[0].get_legend_handles_labels()
    fig.legend(handles,names,loc='upper center',bbox_to_anchor=(.5,.88),ncol=3,frameon=False,fontsize=10)
    fig.suptitle('Pair portfolios and matched trend control',fontsize=17,y=.98)
    fig.text(.5,.925,'2025-01-01 00:00 to 2026-08-31 23:00 UTC | Native simulated fills, model fees and supplied funding',ha='center',fontsize=11)
    fig.text(.055,.105,'Daily display samples; returns and drawdowns use all 14,592 hourly marks per account. Terminal forced exits are separate.',fontsize=10)
    fig.text(.055,.070,'Mixes sum actual 500+500 wallets. Minimum-order rules make 500-USDT and 1,000-USDT component paths different.',fontsize=10)
    fig.text(.055,.035,'Retrospective source-selected universe; no independent OOS or authenticated exchange settlement.',fontsize=10,color='#555555')
    fig.subplots_adjust(top=.73,bottom=.20,left=.075,right=.98,wspace=.09)
    figure=HERE/'account_curves.png';fig.savefig(figure,dpi=160,facecolor='white');plt.close(fig)
    (HERE/'figure_sources.json').write_text(json.dumps(dict(figure_sha256=sha(figure),series=series,matplotlib=matplotlib.__version__,
        common_axis=True,units='USDT',sampling='Actual last hourly observation of each UTC day plus true first point',visual_review='PENDING'),indent=2))
    lines=['# 加密配对均值回归：完整 Freqtrade 账户与真实分仓组合','',
        '**本轮没有证明有合格的更高收益策略。** 以下为已审计的原生历史模型账户；不是实盘成交、独立样本外或交易所结算认证。研究目标仍未完成。没有订单、部署、服务修改、提交或推送。','',
        '## 完整可比窗口','',
        '统一使用 **2025-01-01 00:00～2026-08-31 23:00 UTC**，608 天、每账户 14,592 个小时估值。单策略本金 1,000 USDT，另跑真实 500 USDT 账户；组合只相加同一时钟的两个 500 钱包，不缩放 1,000 曲线、不重置年初资金、不在子账户间转账。2026 列为截至八月的连续切片。','',
        '|账户|单边成本 bps|全窗净收益|2025 连续切片|2026 至八月|小时标记最大回撤|','|---|---:|---:|---:|---:|---:|']
    names={'SourceNaivePairs':'源 ADF 配对','AdjustedEGPairs':'修正 EG 配对','TrendGrossOne':'同池趋势对照'}
    for cost in [15,8]:
        for row in result['accounts']:
            if row['cost_bps_per_side']!=cost or not row['account'].endswith('_1000'):continue
            p=row['periods'];lines.append(f"|{names[row['strategy']]} 1000|{cost}|{pct(p[0]['net_return'])}|{pct(p[1]['net_return'])}|{pct(p[2]['net_return'])}|{p[0]['maximum_hourly_mark_drawdown']*100:.2f}%|")
        for row in result['combinations']:
            if row['cost_bps_per_side']!=cost:continue
            p=row['periods'];lines.append(f"|{names[row['selector']]}500＋趋势500|{cost}|{pct(p[0]['net_return'])}|{pct(p[1]['net_return'])}|{pct(p[2]['net_return'])}|{p[0]['maximum_hourly_mark_drawdown']*100:.2f}%|")
    lines+=['','![经审计的原生模型账户曲线](account_curves.png)','',
        '主比较按小时处理当前订单之前的 mark OPEN，未平仓计浮盈亏并预留退出费。原生期末强制退出收益另列于结果，不替代共同最后估值。15／8 bps 为实际订单每边固定合并成本，资金费另按原生数量计入；并非逐笔盘口冲击或实际经纪商收费认证。','',
        '## 本金与最小订单约束','',
        '500 与 1,000 账户的持仓和收益可能明显不同。下表的“约束归零”只统计事前权重非零、但在数量步长及最小金额后目标数量为零的资产日；不是缺失价格补零，也不是主动按收益删币。保留比为逐日实际可下目标名义金额之和／原始目标名义金额之和。','',
        '|账户|成本 bps|净收益|非零权重资产日|约束归零比例|目标名义金额保留比|','|---|---:|---:|---:|---:|---:|']
    for row in result['accounts']:
        c=row['capacity_diagnostic'];lines.append(f"|{row['account']}|{row['cost_bps_per_side']}|{pct(row['periods'][0]['net_return'])}|{c['nonzero_weight_asset_days']}|{c['zero_fraction_of_active']*100:.2f}%|{c['target_notional_retained_ratio']*100:.2f}%|")
    low={r['account']:r for r in result['accounts'] if r['cost_bps_per_side']==8}
    corr={r['selector']:r['daily_return_correlation'] for r in result['combinations'] if r['cost_bps_per_side']==8}
    lines+=['',f"以 8 bps 修正组合为例，配对500本身为 {pct(low['AdjustedEGPairs_500']['periods'][0]['net_return'])}，趋势500为 {pct(low['TrendGrossOne_500']['periods'][0]['net_return'])}；不能将混合的正收益说成配对已获利。源／修正配对500与趋势500的 607 个日变动相关约 {corr['SourceNaivePairs']:.3f}／{corr['AdjustedEGPairs']:.3f}，只描述本窗口的回报关系，不证明普遍独立 alpha。"]
    lines+=['','因此组合相对 1,000 单账户的变化也受资本规模与最小订单影响，不能全归因于分散化或新 alpha。两个配对筛选视图属于同一机制，配对共享标的，不能将 13＋4 算成 17 个独立因子。','',
        '## 来源、固定规则与本地迁移','',
        '[源 GitHub 项目](https://github.com/filip-filipovski/pairs-trading/tree/7b6aba1319d26820335f34f399635d463f3bb640)固定提交 `7b6aba1319d26820335f34f399635d463f3bb640`。使用其原始十币 BTC、ETH、BNB、SOL、ADA、XRP、LTC、DOT、LINK、TRX，以及原文 2020-08-18～2024-01-16 的 1,247 日训练窗。45 对、两个回归方向均检查；静态对数价 OLS 系数不在评价期更新。残差 ADF 的源筛选复现 13 对；`statsmodels.coint` 对照保留 4 对。后者未控制全部配对和方向的多重检验，不是经济均衡证明。','',
        '价差的 30 日样本标准差 z 分数使用已完成日线；固定入场 |z|>2、退出 |z|<0.5、持仓止出 |z|>4，平仓后不在同根反向。源代码在空仓且 |z|>4 时仍入场，本地保留该行为。每对从账户前最后一个完整信号日开始为空仓，之前日线只预热统计量；没有删去资金窗口头 30 天。T−1 收盘信号映射 T 日 00:00，原生 1h 延迟到 01:00，使用 5m detail。','',
        '本地为每个已选配对固定 1/K 预算，包括暂时空仓的配对；以 `(1,−beta)/(1+abs(beta))` 分配两腿，并按资产净额下单，不把空闲预算再分配给活跃配对。每天按实际原生已成交持仓和标记 NAV 调整数量。所有账户用同一 3× CROSS 保证金配置；配对目标总毛额≤1，实际毛／净敞口仍会漂移。这不是市场中性或 beta 中性认证，也不是源项目无资本约束、单位名义金额累加收益的复现。','',
        '趋势对照复用已固定的 GrossOneRiskTS 数学，在相同十币及数据合同重跑，保留其额外 T−2 信号时滞；不是新发现的因子。此处比较的是机制对照，不证明超过既有最优 NFI；不同价格口径、币池或窗口的旧收益未混入。','',
        '## 原生执行与核验','',
        f"Freqtrade 固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`，核对 595 个上游文件。完整 `Configuration → StrategyResolver/IStrategy/DataProvider → Backtesting → Trade/Order/Wallets → 原生归档` 运行 12 次，全部成功结束；共 {audit['total_native_fills']:,} 笔模型订单、175,104 个账户小时点。本地策略、日调仓接口、存储及只读观察器是扩展，不称为上游自带配对策略。",
        '',f"独立从原生实际订单重建持仓、费用、可变数量资金费及逐小时标记 NAV，最大误差 {max(a['marked']['maximum_nav_error_usdt'] for a in audit['audits']):.3g} USDT；逐日目标数量、标记预算和入场上限逐项核对。直接核对源函数的 45 组训练结果、独立标量 OLS／z／状态与净额权重；3 个真实原生策略预检、8 次真实未来扰动／截断重跑及 9 项测试通过。",
        '',
        '这是完整策略分配研究，不是横截面因子筛选：新增 Registry 因子、factor_analysis 调用和 Vibe bench 均为 0，未将其他项目的记录注册成 Freqtrade 收益。源码份额与原生日调仓回调沿用既有测试证据；没有把旧测试重新计为新增。','',
        '## 数据、局限与失败保留','',
        '730 份 Binance 官方现货日线月档逐一核对官方 SHA，十币共同日历完整覆盖 2020-08-18～2026-08-31；原生 Feather 写入后再经历史 API 精确回读。2025 起现货时间戳的微秒单位单独检查。期货 1h／5m、mark、资金费及趋势日线共享旧研究的 50 份字节完全相同输入，旧价格或失败未重写。源十币池在结果前固定，不是为了规避 TIA 缺失事件而事后删币；旧 TIA 未知和 29 币失败保留。','',
        '本币池的理论八小时资金费时点均有提供记录，逐事件所有权和现金流经核对；这仍不证明私有账户结算。冻结市场规则快照未经逐日历史认证；存续偏差、成交深度、历史首见与实际数据到达延迟未消除。源项目发布和筛选历史与本评价期重叠，所以本研究不算独立样本外。','',
        '初次原生预检因 StrategyResolver 的执行命名空间未登记在 sys.modules 而失败；修正为对实际方法全局命名空间注入诊断输入。首轮两项测试存在时区夹具错误，修复后重跑。错误日志和原版包装均保留；没有改变信号、训练窗、阈值、费用、币池、权重或评价窗。统计依赖安装在本研究专用目录，未安装进交易运行环境。为资源准入仅将历史缓存中逐字节相同的文件改为硬链接，所有路径和内容保留，记录见资源 receipt。','',
        '## 多平台学习的证据边界','',
        '[Hummingbot 发布者的节目文稿](https://hummingbot.substack.com/p/the-bot-pod-episode-7-deep-dive-into)涉及偏离网格代理、订单簿深度和多机器人报告；已读文稿相关片段，链接的 YouTube 视频获取被限流，未观看。它不是本研究的日频 OLS 模型，也没有提供可直接采用的完整账户证明。[抖音智能文稿](https://www.douyin.com/video/7548096488870628643?source=Googlebot-sdc)提供均值回归与模型检验线索，但没有可复核交易合同；协整不能单独证明经济均衡或保证收敛。X 的本轮定向搜索未取得可用精确来源。上述内容未生成交易规则或收益。','',
        '[Cornell／Hummingbot 配对论文](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6188418)本轮只读摘要与书目信息，发现其采用聚类和稳定性筛选的不同流程；没有读完整论文、复现其小时模型或采用收益及市场中性宣称。后续需先取得完整方法和输入合同，不能用改动本次失败参数来冒充该方法。','',
        '## 结论与证据入口','',
        f"预先冻结的 {len(result['gates'])} 个“筛选视图×成本”联合门槛中，通过 {sum(g['combined_numeric_gate'] for g in result['gates'])} 个。门槛同时要求全窗与两段连续切片正收益、回撤限制、优于趋势对照及真实分仓组合优于其组件。部分曲线亏损较少，不等于达到更高可用收益。没有新策略取得独立验证、纸面观察或实盘资格。",
        '',
        '`protocol.json`、`protocol_freeze.json`、`source_manifest.json`、`plan.json` 固定条件；`input_admission.json`／原始月档提供输入；`preflight.json`／`tests.log` 给出原生与因果性检查；`native_validation.json`、`results.json` 及 user_data 下 12 份原生归档给出账户证据；`source_intake.json` 区分各平台确实读到的内容与未取得证据。'
    ]
    (HERE/'README.md').write_text('\n'.join(lines)+'\n')
    print('Native curves and repository report generated; visual review pending')


if __name__=='__main__':main()
