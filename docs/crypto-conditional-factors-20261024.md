# 永续合约条件因子组合研究

后续已开始[前瞻盘口与成交流研究](crypto-microstructure-20261024.md)，当前进程状态以该轮和统一状态为准。以下历史筛选结论保持不变。

2026-10-24。本轮完成两批、34个不同特征、6组组合、24套方向与仓位层模型；每套同时拟合净盈利概率与单位名义净损益。当前没有组合通过事前筛选，也没有新策略取得实盘资格。

较值得后续检验的线索是 **CrossAssetRotation＋CrowdingAbsorption**：第三季度大额方向20个候选标签中13个扣费后为正，平均单位名义净标签为0.8384%。这些信号只分布在8天，标签可能重叠，不是20笔独立账户交易，更不能将标签相加称作账户收益。它未达到至少30个信号要求，小额部分也没有过筛。

## 目标与本轮边界

继续自主研发同一个永续账户中的“小额频繁净盈利＋较大仓位贡献主要净利润”。Omofun、Damorne是风格参考，不作为必须复制的信号。现货与永续对冲carry保留在历史记录，不属于本轮。

80%小额净盈利概率、55%大额概率及其他收益目标是事前登记的研究假设，不是用户确认的硬性收益承诺。概率模型的输出也不是可兑现胜率。没有因为本轮失败降低门槛、挑选币种或修改旧实验。

上一版自主OHLCV双层策略的完整2024年诊断已结束：净收益−98.61%，完整平仓净胜率41.82%，小时观察回撤98.67%。该策略已淘汰，结果见[原自主策略研究](crypto-autonomous-objective-20261023.md)。本轮扩大因子机制，保留这一失败对照。

## 先冻结，再比较

| 项目 | 固定设定 |
| --- | --- |
| 市场 | Binance USDT永续，BTC、ETH、BNB、SOL、XRP、DOGE，分别研究多头和空头 |
| 特征粒度 | 完成5分钟K线；特征K线开盘后5分钟可用，再留完整一根缓冲，开盘后10分钟入场 |
| OI及账户指标 | 原始时钟＋10分钟可见假设，精确匹配，不前填；该假设不是历史首次到达证明 |
| 资金费特征 | 实际已发生事件，保留5分钟可见延迟；复用TradingAgents冻结的funding计算源码 |
| 拟合 | 2024-01-01至2024-07-01；仅拟合区间估计均值、标准差与权重 |
| 筛选 | 2024-07-01至2024-10-01；读结果前冻结全部模型、预测及候选信号 |
| 保留段 | 2024年第四季度结果未计算、未读取；2024历史仍属于项目回顾式资料，不称全项目独立样本外 |
| 学习器 | 各方向、各仓位层单独池化六币；固定L2逻辑回归＋ridge净标签回归，惩罚0.01，不搜索超参数 |
| 小额标签 | 价格止盈0.6%、止损1.2%、最长2小时 |
| 大额标签 | 价格止盈6%、止损2%、最长1天 |
| 成本 | 每侧5bps手续费＋3bps费用代理；实际资金费、小时mark开盘代理进入标签 |
| 标签执行假设 | 同根同时触及止损和止盈时先止损；不利跳空用更差开盘，有利跳空最多按止盈价 |
| 信号排序 | 小额概率≥80%、大额≥55%，预测净标签≥0.1%；每层每个时点最多一个，按预测净标签排序 |
| 晋级 | 两层各至少30个信号、实际净标签均值正、小额标签正收益率≥80%、大额≥55%；通过才进入三档费用的完整账户 |

小额边界固定剔除末端2小时10分钟，大额剔除1天10分钟；不因未来提前止盈或止损而纳入边界样本。全部组合在各自批次中使用同一套完整特征样本，避免将OI或账户指标的缺失筛选误认为增益。两批的公共样本略有不同，不直接把两批均方误差当作同样本增量。

## 组合与因子入口

第一批协议：[conditional_factor_plan_v001.json](../research/crypto/conditional_factor_plan_v001.json)，SHA256 `6b6644659081be90906034133d5404985a552c09486670c6ac16ddac6dbbf063`。

| 组合 | 特征数 | 新增机制 |
| --- | ---: | --- |
| FlowRegime | 14 | 20分钟／1小时／24小时收益、ATR、K线收盘位置、振幅、成交量变化、均价偏离、BTC方向；真实主动买卖流、价格冲击残差、路径效率、成交衰竭 |
| FundingContext | 18 | 加入已知24小时资金费、变化，以及与收益和主动成交的交互 |
| DeleveragingContext | 22 | 加入正向截断的OI收缩，以及与流向、收益、价格冲击残差的交互 |

第二批协议：[conditional_context_plan_v001.json](../research/crypto/conditional_context_plan_v001.json)，SHA256 `4392ba039c726f3fefdb0c5eb17dc87cbeddc2623427a2bf1f9f70fef1529acf`。第一批失败后登记新机制，标签、成本、学习器、时间段和门槛保持原设定。第三季度评估继续标为回顾式开发。

| 组合 | 特征数 | 新增机制 |
| --- | ---: | --- |
| CrossAssetRotation | 20 | 第一批14个量价与流特征，加BTC回归残差、相对其他五币收益、其他五币市场收益、六币离散度、涨跌广度、1小时／此前24小时波动压缩 |
| CrowdingAbsorption | 24 | 加精确延迟的全市场账户多空比对数、60分钟变化，以及与冲击残差和主动成交的交互 |
| CrowdingFundingStress | 28 | 加已知资金费、资金费×拥挤度、OI收缩、收缩×拥挤度 |

计算在 [conditional_factors.py](../research/crypto/conditional_factors.py) 和 [conditional_context.py](../research/crypto/conditional_context.py)。协议保存全部公式。重点区别如下：

- OI收缩为 `max(-log(OI_now/OI_60min_ago),0)`，两端必须为已知正数。没有复活已失败的“H03正向OI扩张×成交方向”。
- 全市场账户比使用原始`count_long_short_ratio`，没有把已失败的“H02大户仓位／账户差”换名重跑。它表示账户分布，不能解释为资金净流入或精确持仓规模。
- 冲击响应拟合窗在当前20分钟之前结束；BTC回归拟合窗在当前1小时之前结束。BTC对自身的回归残差固定为数学上的零，避免放大浮点误差。
- 跨币相对收益剔除自身，使用另外五币均值；需要六币同刻完整数据。波动压缩使用过去数据，不引入未来波动。

## 实际筛选结果

以下全部是第三季度的**单位名义标签代理**，已经扣除登记费用与资金费。尚未进行这六组的完整账户回测，不能据此报告账户收益、回撤或实盘胜率。

| 组合 | 小额候选数 | 大额候选数 | 大额标签正收益率 | 大额平均单位净标签 | 大额分布天数 | 晋级 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| FlowRegime | 0 | 1 | 100% | +1.6241% | 1 | 否 |
| FundingContext | 0 | 2 | 100% | +3.1945% | 2 | 否 |
| DeleveragingContext | 0 | 4 | 50% | +0.0704% | 3 | 否 |
| CrossAssetRotation | 0 | 19 | 42.11% | +0.1122% | 5 | 否 |
| CrowdingAbsorption | 0 | 20 | 65% | +0.8384% | 8 | 否 |
| CrowdingFundingStress | 0 | 26 | 30.77% | −0.6884% | 9 | 否 |

1或2个标签的100%不能证明高胜率，零信号也没有可测的胜率。两批均输出cash对照，cash没有交易，不符合用户交易目标。

第一批在相同公共样本上，加入资金费令大额多／空的Brier误差分别减少约0.000363、0.000034，但小额两个方向反而增加。再加OI收缩也没有一致改善；没有宣称统计显著或可交易alpha。完整配对指标保存在各批`scores.json`。

第二批的全市场账户与价格吸收值得保留为后续机制线索；本轮不能将20个、只覆盖8天的候选晋级。继续叠加资金费和OI收缩后，候选正收益率与平均净标签都恶化，当前不优先这个更复杂的组合。

## 原始数据质量与独立复核

补齐六币2023-12-01至2024-12-31的2382个官方日档及对应官方SHA校验文件。原文核对有245个文件时间乱序，**没有重复原始时钟或冲突行**；修复仅稳定排序，不改数值。第一版失败证据保留，第二版重新验证并准备规范时钟。

686,016个规范5分钟时点中，OI正值684,751个、原始零值486个、未知779个。零OI不作为有效持仓，不补零、不插值。每个字段保留已知、缺失、异常和缺行状态。档案发布时间不等于历史首次可见时间。

第一批拟合区间总314,496行，共同完整样本313,674行；筛选区间总158,976行，共同完整158,067行，另按标签最长持有期剔除末端。第二批另外核对账户指标覆盖，详细数据见`population_coverage.json`。

独立审计 [audit_conditional_screen.py](../research/crypto/audit_conditional_screen.py) 不导入向量标签或预测计算器：用Decimal按真实OHLC与事件重建960个抽样标签，最大差约`2.45e-16`；每批另复核480个模型预测，最大差低于`1.7e-16`。两批重用同一组标签样本，不将其宣称为1920个独立样本。

研究测试106项通过，包含未来价格／成交／OI／资金费不改变过去特征、十分钟可见延迟、跨币时钟、零值未知、同根止损优先、不利跳空、资金费暴露区间、标签边界及不利用结果排序。Ruff通过。旧模拟包对应的341个原生源码文件保持不变。

这些检查验证实现和约定的代理计算，不证明盘口成交、限价排队、完整盘中清算路径、前瞻收益或部署运行。

## 可复算结果与下一步

所有数据、模型和权重均在Git忽略的本机`user_data`保存：

- [官方指标规范数据](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/conditional_metrics_v002/receipts.json)
- [第一批结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/conditional_factor_screen_v001/screen.json)及[独立审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/conditional_factor_screen_v001_audit.json)
- [第二批结果](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/conditional_context_screen_v001/screen.json)及[独立审计](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/conditional_context_screen_v001_audit.json)
- [统一状态](/Users/chrise/GitHub/freqtrade/user_data/crypto_quant/current_status.json)

拟合与筛选入口是 [screen_conditional_factors.py](../research/crypto/screen_conditional_factors.py)，固定学习器是 [conditional_learning.py](../research/crypto/conditional_learning.py)。输入回执、源码、模型和预测均有SHA与阶段冻结记录；所有新运行必须使用新输出目录。

```sh
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 \
  .venv/bin/python -m research.crypto.screen_conditional_factors \
  --plan research/crypto/conditional_context_plan_v001.json \
  --panel user_data/crypto_quant/conditional_context_panel_v001 \
  --output user_data/crypto_quant/新的筛选目录
```

下一轮需要解决的是小额层没有足够高净盈利概率的可交易机会，而不是继续增加大额仓位掩盖问题。优先研究可前瞻取得的盘口价差、微价格／订单簿失衡、实际成交吸收和短时流向持续性；先登记采集与事件可见性，不拿5分钟OHLCV伪造盘口因子。大额层保留账户拥挤度与吸收机制，在新协议及足够时间覆盖中检验，不调整本轮失败结论。

本轮筛选结束时没有后台研究或模拟交易进程，没有实盘放行；后续采集状态见上方的新研究记录。未调用TradingAgents让模型为失败候选背书；其已有失败H02/H03/H04结论均保留。上线仍需要同账户完整损益、实际费用与浮亏风险、独立前瞻、当前执行和重启证据。

## 一手来源

- [Binance公共档案与校验方式](https://github.com/binance/binance-public-data)：原始价格、主动成交、指标档案来源。
- [Binance官方账户多空比接口](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Long-Short-Ratio)及[官方SDK说明](https://binance.github.io/binance-connector-js/classes/_binance_derivatives-trading-usds-futures.DerivativesTradingUsdsFuturesRestAPI.MarketDataApi.html)：字段语义与公开API最近30天限制；长历史使用官方档案。
- [2026年加密反转研究](https://arxiv.org/abs/2608.21888)：只用于发现线索；文中毛边际低于其费用基准，不构成本项目盈利证据。
