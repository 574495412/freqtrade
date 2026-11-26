"""Evidence-backed repository report and standalone research curves."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import PercentFormatter

from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import save, sha

HERE = Path(__file__).resolve().parent
REPORT = HERE/'v002'
NAMES = {'CovarianceRiskTS':'协方差目标风险','DiagonalRiskTS':'对角风险对照',
         'GrossOneRiskTS':'总敞口1倍对照'}


def pct(value):
    return f'{value:.2%}'


def table_row(name, cost, periods):
    full, first, second = periods
    return f'|{name}|{cost}|{pct(full["net_return"])}|{pct(first["net_return"])}|{pct(second["net_return"])}|{pct(full["maximum_hourly_mark_drawdown"])}|'


def main():
    data = json.loads((REPORT/'results.json').read_text())
    validation = json.loads((REPORT/'native_validation.json').read_text())
    assert validation['passed'] and len(data['accounts']) == 12
    plan = json.loads((REPORT/'plan.json').read_text())
    out = Path(plan['output_root'])
    curves = {}
    sources = []
    for cost in [15,8]:
        curves[cost] = {}
        for strategy in NAMES:
            path = out/f'{strategy}_1000_{cost}bps/hourly_equity.parquet'
            frame = pd.read_parquet(path).set_index('date').hourly_mark_equity
            curves[cost][strategy] = frame
            sources.append({'path':str(path),'sha256':sha(path),'cost':cost,'series':strategy})
        path = REPORT/f'combo_{cost}bps.parquet'
        curves[cost]['Mix'] = pd.read_parquet(path).hourly_mark_equity
        sources.append({'path':str(path),'sha256':sha(path),'cost':cost,'series':'Mix'})
    colors = {'CovarianceRiskTS':'#3268a8','DiagonalRiskTS':'#cb7b27',
              'GrossOneRiskTS':'#7a8339','Mix':'#aa677d'}
    styles = {'CovarianceRiskTS':'-','DiagonalRiskTS':'--','GrossOneRiskTS':':','Mix':'-.'}
    labels = {'CovarianceRiskTS':'Covariance risk','DiagonalRiskTS':'Diagonal risk †',
              'GrossOneRiskTS':'Gross-one risk †','Mix':'Mix: actual 500 + 500'}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.labelcolor':'#222222','text.color':'#222222'})
    fig, axes = plt.subplots(2,2,figsize=(12,7.6),sharex=True,sharey='col')
    endings = []
    for row,cost in enumerate([15,8]):
        for name,nav in curves[cost].items():
            assert len(nav) == 14592 and nav.iloc[0] == 1000 and np.isfinite(nav).all()
            axes[row,0].plot(nav.index,nav,color=colors[name],ls=styles[name],lw=1.5,label=labels[name])
            dd = nav/nav.cummax()-1
            axes[row,1].plot(nav.index,dd,color=colors[name],ls=styles[name],lw=1.5)
            endings.append(dict(cost=cost,series=name,start=float(nav.iloc[0]),end=float(nav.iloc[-1]),
                                net_return=float(nav.iloc[-1]/nav.iloc[0]-1),maximum_drawdown=float(-dd.min())))
        axes[row,0].set_title(f'{cost} bps per side | marked account NAV',loc='left',fontsize=11)
        axes[row,1].set_title(f'{cost} bps per side | drawdown',loc='left',fontsize=11)
        axes[row,0].set_ylabel('USDT')
        axes[row,1].yaxis.set_major_formatter(PercentFormatter(1,decimals=0))
        axes[row,0].axhline(1000,color='#777777',lw=.7,ls=':')
        axes[row,1].axhline(0,color='#777777',lw=.7)
        for ax in axes[row]:
            ax.grid(axis='y',color='#dedede',lw=.5)
            nav = curves[cost]['Mix']
            ticks = pd.DatetimeIndex([nav.index[0],pd.Timestamp('2025-07-01',tz='UTC'),
                                     pd.Timestamp('2026-01-01',tz='UTC'),nav.index[-1]])
            ax.set_xticks(ticks)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.set_xlim(nav.index[0],nav.index[-1])
            ax.tick_params(axis='x',labelsize=9)
    fig.suptitle('Fixed TSMOM signals: native quantity rebalancing and risk scaling',x=.065,ha='left',fontsize=14)
    handles,names = axes[0,0].get_legend_handles_labels()
    fig.legend(handles,names,loc='upper left',bbox_to_anchor=(.055,.945),ncol=4,frameon=False,fontsize=10)
    fig.text(.065,.041,'2025-01-01 00:00 to 2026-08-31 23:00 UTC. Same 1000-USDT start; mix uses actual 500+500 wallets.',fontsize=9,color='#555555')
    fig.text(.065,.018,'† Missing TIA funding-event exposure. Provided-event historical models; no independent out-of-sample evidence.',fontsize=9,color='#555555')
    fig.subplots_adjust(top=.83,bottom=.12,left=.08,right=.98,hspace=.28,wspace=.18)
    fig.savefig(HERE/'account_curves.png',dpi=170,facecolor='white')
    plt.close(fig)
    for row in endings:
        expected = (next(x for x in data['combinations'] if x['cost_bps_per_side'] == row['cost'])
                    if row['series'] == 'Mix' else
                    next(x for x in data['accounts'] if x['account'] == row['series']+'_1000' and x['cost_bps_per_side'] == row['cost']))
        assert abs(row['net_return']-expected['periods'][0]['net_return']) < 1e-12
        assert abs(row['maximum_drawdown']-expected['periods'][0]['maximum_hourly_mark_drawdown']) < 1e-12
    save(HERE/'figure_sources.json',dict(source_series=sources,actual_points_per_curve=14592,
         identical_starting_usdt=1000,endpoints_and_drawdowns= endings,
         table_endpoint_reconciliation=True,rendered_visual_inspection='pending',
         format='Standalone scientific research figure in established repository report'))
    table = ['|账户/组合|每边成本bps|全段模型净收益|2025模型净收益|2026至8月模型净收益|小时净值最大回撤|',
             '|---|---:|---:|---:|---:|---:|']
    for cost in [15,8]:
        for strategy,label in NAMES.items():
            row = next(r for r in data['accounts'] if r['account'] == strategy+'_1000' and r['cost_bps_per_side'] == cost)
            marker = '†' if row['tia_missing_event_actual_exposure'] else ''
            table.append(table_row(label+'1000'+marker,cost,row['periods']))
        row = next(r for r in data['combinations'] if r['cost_bps_per_side'] == cost)
        marker = '†' if row['known_gap_exposed_sleeves'] else ''
        table.append(table_row('真实500+500混合'+marker,cost,row['periods']))
    small = ['|实际500账户|每边成本bps|全段净收益|2025净收益|2026至8月净收益|小时净值最大回撤|',
             '|---|---:|---:|---:|---:|---:|']
    for cost in [15,8]:
        for strategy,label in NAMES.items():
            row = next(r for r in data['accounts'] if r['account'] == strategy+'_500' and r['cost_bps_per_side'] == cost)
            marker = '†' if row['tia_missing_event_actual_exposure'] else ''
            small.append(table_row(label+marker,cost,row['periods']))
    audits = validation['audits']
    transactions = sum(a['marked']['filled_orders'] for a in audits)
    lifecycles = sum(a['policy']['initial_entries'] for a in audits)
    naverror = max(a['marked']['maximum_nav_error_usdt'] for a in audits)
    funderror = max(a['marked']['maximum_funding_error_usdt'] for a in audits)
    exposure = [a['account'] for a in audits if a['marked']['tia_missing_event_actual_exposure']]
    parity = json.loads((REPORT/'full_native_clock_parity.json').read_text())
    numeric = all(r['combined_numeric_gate'] for r in data['gates'])
    verdict = ('冻结的数值条件通过，但没有独立样本外、源全市场复现或独立机制收益流认证。'
               if numeric else '未通过冻结的组合与对照数值条件，尚未获得合格的新组合。')
    text = f'''# 加密原生研究：固定趋势信号、协方差风险缩放与真实数量再平衡

**{verdict}** 本轮新增已接受独立因子0、可部署策略0。全部为离线 Freqtrade 历史模型，没有实盘单、部署或运行服务变更。

## 相同完整账户窗口

统一窗口为 **2025-01-01 00:00～2026-08-31 23:00 UTC**，每账户14,592个小时估值点，包含现金、实际模拟持仓、成交成本及提供的资金费事件。每种方法分别用1000和500 USDT真实初始化，成本每边15/8bps；不是把1000账户缩半得到500路径。年度切片来自同一连续钱包，2026仅至8月，不是全年收益，也没有跨年重置资金。

两种成本情景也分别完整重跑：费用变化会反馈到之后的现金和数量，不能从15bps路径简单加回费用来推算8bps结果。

组合只相加两个实际500账户：协方差风险500 + 总敞口1倍对照500；每个时点严格相同，不补值、不转移资金。它是同一趋势信号的风险配置混合，不是已经证明机制不同的独立alpha组合。

`allocation_geometry.json`核对了608天的实际原生权重：三对配置的平均绝对截面Pearson相关均为1。它们没有提供新的方向信息；实际账户收益可以因敞口、最低金额、手续费和现金约束不同而分化，不能把权重相关直接当成资金收益相关。

本次12个唯一配置全部完成；另有一个原始实现完整账户用于技术对照，和一个明确中断的500尝试。不能把重复的完整技术对照再计为独立收益证据。

本轮冻结数值判据为全段及两个年度连续切片均正、小时净值回撤不超过50%；协方差方法高于两个对照，混合超过最优同本金1000账户，且两费率都成立。这是本轮研究判据，不是独立验证或部署批准。

{chr(10).join(table)}

†表示已知TIA事件缺口存在实际模拟持仓暴露，实际完整结算收益为UNKNOWN。表中仍列提供事件下的条件模型数值，不能把缺失事件当成已知零费率；未标记账户也没有实付认证。

![原生净值与回撤](account_curves.png)

这些收益是提供事件与冻结规则下的模型结果，不是交易所实付认证。原生终点force_exit另列于`v002/results.json`，不代替表中的统一小时MARK OPEN估值。估值预留剩余平仓费；小时回撤不是小时内最坏回撤。不同风险配置也不代表实际波动率或市场beta相同。

## 方法、源码与本地扩展

第三方[GitHub源码](https://github.com/cagriKarakulak/aqr-tsmom-crypto/tree/f067ed1f63377175af7e6adef882ddb6a0417dc8)固定提交`f067ed1f63377175af7e6adef882ddb6a0417dc8`、MIT许可，七份原始文件绑定SHA。它不是AQR官方代码，没有采用README收益。实际实现是30/90/365日方向信号平均、30日年化样本波动率反比，再用`.95样本协方差+.05对角矩阵`缩放至20%目标风险；不是Ledoit–Wolf估计，也不是通过逆协方差重新优化相对权重。对照分别仅用对角风险和总敞口1倍；单标的目标上限0.5、总目标敞口上限3保持冻结。

固定29品种沿用前轮完整输入池，未删BNB或按收益排除TIA等名字；不是源532品种或历史PIT主表。共有观测历史从2023-10-31起，协方差样本实际426～1033天，整个窗口都未满1095天。Top100在本地29池不构成筛选。没有用零值补新币上市前价格或压缩缺失日历。

保留源码额外一根日线的真实实现时钟：交易日T的01:00使用T−2日收盘信号/波动率，协方差只含更早收益。没有把它改成更快的T−1来挽救表现。完整源信号/波动率方法AST一致，52,896个实际权重用独立计算核对，最大误差约3.64×10⁻¹⁴，并完成未来扰动/截断复算。

Freqtrade固定原生树`c051e9065c5b677f03817dd1f83cb980af1ed684`，595个源文件逐一核验。实际Configuration/StrategyResolver/IStrategy/DataProvider/Backtesting/Trade/Order/Wallets负责资金、订单和仓位；没有外置资金模拟器替代它。本轮是固定源策略与分配规则研究，不是新横截面因子筛选；没有新增factor_analysis或Vibe bench，也没有把外部账户注册为Freqtrade结果。

本地策略通过支持的`custom_stake_amount`、`adjust_trade_position`与`bot_loop_start`执行每日数量目标，按实际已成交订单的MARK净值预算99%，全部方法采用3倍cross margin。真实增仓、部分减仓、整笔退出、精度、最低金额和现金限制均保留；小额无法执行的差值保持跟踪误差，没有免费逐日重置权重。原生强制止损/止损调整及清算处理仍生效，源策略kill-switch关闭不表示没有原生订单风险规则。

资金费、滑点与费率合同不能混淆：这里每边15/8bps是固定聚合交易成本情景，资金费另由提供的原生事件计入；不是盘口执行复现。使用源支持的自营`enable_fee_simulation=False`选项，没有额外基金2/20收费，因此也不是源默认基金费后账户复现。

## 实际成交、资金费与预算审计

12个唯一配置共{lifecycles:,}个实际原生交易生命周期、{transactions:,}笔成交，175,104个账户—小时净值点。按导出的实际多次成交数量独立核对资金费及全账户NAV，最大NAV差异{naverror:.3g} USDT，最大逐生命周期资金费差异{funderror:.3g} USDT。未发现同一品种资金费事件被两个生命周期重复拥有。

每账户608个每日预算与下单前实际净值对照，精度和最低金额目标独立复算；实际增仓不得超过目标。500与1000账户可能因最低金额、数量精度、费用与实际现金产生不同持仓，不能视为线性缩放。

**已知TIA2026-06-24 04:00事件仍UNKNOWN。** 实际持仓检查中受该缺口影响的账户为：{', '.join(exposure) if exposure else '本轮未发现持仓暴露'}。未持仓不等于已经证明事件取消或费率为零；有暴露时其实际完整结算保持未知。所有账户仍没有私有交易所结算核对，冻结的公开交易规则快照也不是整个历史规则的独立认证。

缺失费用可能改变后续每日预算和数量，因此不在固定成交路径末尾加一笔估计费用，就宣称完整账户已修复。

## 保留的技术失败与完整对照

首次准备在145份数据生成后，因为筛选80池规则缺BNB而停止；从原研究已引用的完整原始快照补齐合同，原池和行情不变。原始失败记录保留。首轮测试4失败/7通过，分别是源码提取文档字符串缩进和Pandas3只读数组，均在资金回测前修复；随后11项相关测试通过。

原实现每5分钟都进入原生安全包装、深复制含历史订单的交易，但非01小时回调本来只返回None。保留第一个完整账户，明确SIGINT中断第二个500尝试，并保留原生失败与退出码−2；其未完成路径不作收益证据。中断缓存经逐文件SHA核对后无损归档，没有丢弃失败数据。

本地时钟扩展只用策略自己的`position_adjustment_enable`开关跳过非01小时；原生安全包装和5分钟止损/退出流程没有修改。三组864个时钟检查通过；新类实际权重与原类精确一致。首次技术注册的Parquet索引freq元数据断言失败也保留，未涉及数值更改。

完整窗口对照核对了{parity['identical_native_trades']:,}个生命周期、{parity['identical_native_orders']:,}笔成交、14,592个NAV点，以及全部权重、资金费和策略记录，均精确一致。首个账户由约{parity['original_elapsed_seconds']:.0f}秒降至约{parity['clocked_elapsed_seconds']:.0f}秒，之后才允许其余矩阵继续。原始参数、品种、成本、资本和完整窗口均未调整。

## 多来源学习与不同机制的准入边界

本轮[YouTube线索](https://www.youtube.com/watch?v=1ni8FWe_Cxc)只读到标题/描述，未能抓取或观看视频；[另一期原出版方页面](https://www.toptradersunplugged.com/podcast/the-strategy-did-not-fail-the-investors-did-ft-rob-carver/)提供加密组合讨论上下文，不冒充同一期视频逐字稿。[X线索](https://x.com/10xResearch/status/2056179154715127933)直读403，只有检索暴露的CPI/ETF流/均线讨论；没有完整可运行合同，未采用价格喊单或收益。[抖音页面](https://www.douyin.com/shipin/7644368945084237874)提供AI文稿，提示因子组合还须看投资组合风险和真实执行，但不是原始因子矩阵、优化器代码或经核验的账户。详见`source_intake.json`；这些线索不计新alpha。

另读[Hyperliquid钱包身份特征原文v1](https://arxiv.org/html/2611.05218v1)，其机制不同于匿名成交量，但需要带钱包标识和共识顺序的Level4数据，现有BinanceK线/聚合主动成交量不能补造。秒级预测或markout不是完整资金收益；原文也披露了看过结果后的规格选择。尚无本地完整目标窗口、同场所数据及延迟/费用合同，因而不计因子验证或资金账户，未下载钱包地址原始数据。

## 实际500账户与后续边界

{chr(10).join(small)}

`protocol.json`保存源策略与比较冻结，`v002/plan.json`保存可执行原生合同，`v002/full_native_clock_parity.json`保存技术对照，`v002/native_validation.json`与`v002/results.json`保存实际审计及结论。不存在独立样本外验证，也未证明这些同源趋势配置构成机制分散的新组合。继续寻找更高收益的整体目标未完成；不把局部正收益、较低成本、源码注册或一个通过的技术对照当成目标达成。
'''
    (HERE/'README.md').write_text(text)
    print('REPORTED12 unique accounts; native source and failures retained; objective incomplete')


if __name__ == '__main__':
    main()
