"""Record native rolling-model progress with unresolved financial admission."""
import json,hashlib
from pathlib import Path
from datetime import UTC,datetime
from .nominate import HERE,ROOT,OLD,OUT
from .fit_native import sha

def main():
    audit=json.loads((HERE/'model_verification.json').read_text());capsule=json.loads((OUT/'model_capsule.json').read_text());assert audit['passed_within_model_scope']
    tiers=json.loads((HERE/'sources/freqtrade_tiers_pinned.json').read_text());eos=tiers['EOS/USDT:USDT']
    assert len(eos)==10 and eos[0]['maintenanceMarginRate']==.005 and eos[0]['maxNotional']==10000
    assert sha(HERE/'sources/freqtrade_tiers_pinned.json')==sha(HERE/'sources/freqtrade_tiers_2025_4.json')
    metadata={'freqtrade_reference':{'repo':'freqtrade/freqtrade','tag':'2025.4','commit':'b11119dbe52451c6821981ba32b5df90aa166e68','resource':'freqtrade/exchange/binance_leverage_tiers.json','sha256':sha(HERE/'sources/freqtrade_tiers_pinned.json'),'EOS_brackets':eos,'scope':'Pinned upstream historical cached leverage reference, not certified per-day privateBinancespecification. Native researchengine remainsc051e9065c5b677f03817dd1f83cb980af1ed684; old core not replaced.'},'ccxt':{'repo':'ccxt/ccxt','tag':'v4.4.75','tag_object':'2012582def808fe01b96395264bbfef400bb739b','peeled_commit':'e14a08febaf02f1a072bc1b21b12b49d5cee9fd5','commit_time':'2025-04-13T13:25:58Z','actual_response_fixture':str(HERE/'sources/ccxt_response_binance_peeled.json'),'EOS_in_fixture':False,'correction':'Raw paths using annotated tag object returned404; git-tree and peeled-commit blob exist. Do not call those files absent. Captured fixture has noEOSentry.'},'other_paths':{'python_binance_tag':'v1.0.28','python_binance_commit':'ea4006f6e143b155cbbd9d1141bcaa2aae00d50e','source_tree_scope':'Inspected tagged test filenames and currenttree; no EOS exchangeInfo fixture obtained. Not proof no other source exists.','grep_app':'HTTP429 response retained; no verifiedcode result','gh_cli':'Not installed; no account/auth changed','archive_index':'PublicCDX2024-2025 query returnedHTTP200 emptyarray','other_market_leads':'Spot and other-exchange rules not admitted as BinanceUSDTperpetual specifications','github_api':'Someunauthenticatedmetadatarequests403ratelimited; originalresponses or traceback scope preserved'},'unresolved':['EOS quantity tick/lot and minimum order rules for actualnominatedJan2025week','Account-wide price/mark/funding preparation and nativepaired execution','Initialcapital/grossbudget/weeklyquantitypolicy/costmatrix/combination freeze before any fundedresults'],'EOS_not_deleted':True,'EOS_nonzero_hourly_decisions':audit['EOS_nonzero_hourly_decisions'],'financial_admission':False,'native_funded_accounts':0}
    (HERE/'metadata_admission.json').write_text(json.dumps(metadata,indent=2)+'\n')
    failures={'economic_parameters_changed_after_results':False,'funded_accounts':0,'records':[{'evidence':'model_audit_initial_failed.log','failure':'PandasindexfrequencyNone versus Hour in exactreplay assertion','cause':'Parquetdoesnotretainfreqmetadata; complete explicitdategrid already checked','correction':'Ignorefreqmetadata only; keep exactdatevalues/modelvalues and fullcalendar checks; no signal/model/accountchange.'},{'evidence':'sources/ccxt_fixture_probe.json','failure':'404rawURLs usedannotatedtagobject asifcommit','correction':'Peeltagwithgit and retrieve actualpinnedblob. NoEOSin retrievedfixture.'},{'failure':'GitHubcommit.patch publicrequestProxyError','scope':'Originaltooltraceback retainedinchat; noHTTPresponsebody wasreceived. Do not claimHTTP404or tagabsence.'}]}
    (HERE/'technical_failures.json').write_text(json.dumps(failures,indent=2)+'\n')
    status={'status':'COMPLETE_NATIVE_ROLLING_MODELS_FINANCIAL_ADMISSION_UNRESOLVED','recorded_at':datetime.now(UTC).isoformat(),'source_pool':20,'native_weekly_records':88,'fitted_weeks':61,'source_no_trading_weeks':27,'copula_candidate_fits':2379,'full_hourly_decisions':14592,'threshold_views':[.10,.15,.20],'same_mechanism_not_three_independent_factors':True,'EOS_nonzero_hourly_decisions':audit['EOS_nonzero_hourly_decisions'],'native_funded_accounts':0,'new_accepted_alpha':0,'returns':'UNKNOWN','goal_achieved':False,'goal_status':'active','orders_enabled':False,'services_changed':False,'committed_or_pushed':False}
    (HERE/'status.json').write_text(json.dumps(status,indent=2)+'\n')
    (HERE/'README.md').write_text('''# 参考价差 Copula：完整滚动模型准备

**已经完成滚动模型，尚未完成资金账户。收益、净利润及组合改善仍为 UNKNOWN，整体目标继续 active。** 本轮没有订单、模拟服务启动、部署、提交或推送。

本目录延续已封存的 `copula_reference_native_20261114_v1` 来源与输入检查点。研究对应[参考价差 Copula 论文](https://doi.org/10.1186/s40854-024-00702-7)，采用完整20来源成员，保留EOS的历史存在与公开终止边界，不改名接A、不删除EOS。旧失败和未知未修改。

## 已完成的原生滚动模型

通过固定Freqtrade的StrategyResolver和DataProvider构造真实IStrategy实例，并在本地`FormationInputs.bot_start`生命周期内完成88个周形成窗。它不是Backtesting资金运行，没有生成ZIP或Vibe bench。

形成期固定504个已完成小时，交易周168小时，沿用2021-01-22原始周锚点；首个覆盖周为2024-12-27。完整目标决策域为2025-01-01 00:00至2026-08-31 23:00 UTC，共14,592小时。初始账户前只训练，不继承此前未资助的持仓状态。

原价BTC对每种山寨币做无截距OLS；残差ADF使用10%门槛、含常数/AIC选滞后。按通过成员与BTC原价的Kendall相关排序，取前两种山寨币，平局按原文币序稳定处理。不足两个则整周不交易。ADF的具体滞后约定是本地数值实现，不等于已解决残差多重检验或调整EG临界值。

- 61周完成边缘分布与Copula估计；27周按来源规则无交易。
- 正态、Student-t和Cauchy边缘分布在形成窗内MLE/AIC选择；Copula使用封存v1的39种家族/旋转候选，含约束两参数Tawn。共2,379个Copula候选、366个边缘候选。
- 三档来源入场阈值10%/15%/20%全部保留，退出阈值均10%；它们是同一机制的参数视图，不计作三类新因子。
- 每小时T仅使用已完成、标签为T−1小时的收盘价。周内保持已冻结的模型/系数，不在交易期重估；状态只向前递推，不累计成不同的CMI规则，也不在同根直接反手。

独立核查1,605组OLS/Kendall结果、全部保存的候选似然/AIC及30,168个非空模型状态单元；空仓周的其余状态也逐项检查。三个关键边界分别做真实历史截断和未来价格扰动后的提名与估计重算，共6个审计重算；不重复计为6个新策略。最大Kendall差为2.22e−16，保存似然复核差为0。精确结果在`model_verification.json`。

27个空仓周属于明确的来源规则，不是缺数据后补现金。已发表版本日期为2025-01-13，较早ArXiv版本与全部实现的逐字一致性未证明；本窗口也已被项目多次研究使用，不宣称独立未见样本外或2025年初已经知道全部方法。

## EOS确实进入非零信号

EOS在2025-01-10周被提名。三档阈值分别产生103、107、154个含EOS的非零小时决策。它们是模型状态，**不是实际持仓小时、成交或利润**。因此无法通过省略EOS执行规格把本结果写成完整账户。

已取得并钉住[Freqtrade2025.4上游缓存](https://github.com/freqtrade/freqtrade/blob/b11119dbe52451c6821981ba32b5df90aa166e68/freqtrade/exchange/binance_leverage_tiers.json)中的EOS十档保证金分层，并核对版本标签与提交路径文件相同。这补齐了一部分历史参考，但不是逐日交易所规格或私有结算认证。

CCXT2025年4月固定提交的静态响应样本没有EOS条目；另查Python-Binance测试目录、公开代码索引和历史API存档，未取得所需EOS数量步长/最小订单规则。旧Tardis无凭证401记录继续保留。上述是已查路径的结果，不能推断所有公共来源都没有资料。没有用现货、其他交易所、A合约或猜测数字代替。

## 仍需完成的账户合同

1. EOS实际需要的数量步长、最小订单及历史规格参考。
2. 完整执行价格、mark与资金费输入；实际事件归属和缺失核查。
3. 原生双腿执行、周内固定数量、周界平仓/再开及最小金额导致的双腿失败处理。
4. 在收益前明确本金、总毛额、保证金、费用、数量归一化和完整窗口，冻结实际钱包矩阵与组合对照。
5. 运行真正的Backtesting/Trade/Order/Wallets归档，逐笔现金及共同小时NAV对账，再评估收益与回撤。

来源每侧约20,000、总毛额约2的规则不能直接当成1,000本金/总毛额1账户；此处没有根据模型状态或旧结果偷偷选定更高杠杆。当前仍直接拒绝资金启动，防止全现金回测冒充策略验证。

本地`freqtrade/data/copula_formation.py`和v1统计模块是通过IStrategy调用的扩展；原始595个Freqtrade文件不变，执行引擎仍固定`c051e9065c5b677f03817dd1f83cb980af1ed684`，没有改用2025.4旧引擎。模型数值对齐不能替代账户回报或实盘证据。

证据：`formation_protocol.json`、user_data下`model_capsule.json`/88份周模型/`native_hourly_forecasts.parquet`、`model_verification.json`、`metadata_admission.json`、`technical_failures.json`、`status.json`。原始研究输入和失败均保留。
''')
    print('Recorded88-week native model checkpoint;0funded accounts;EOScontract unresolved')
if __name__=='__main__':main()
