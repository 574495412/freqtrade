"""Document verified source/input/estimator progress without inventing economics."""
import json,sys,hashlib,shutil
from datetime import UTC,datetime
from importlib.metadata import distributions
from pathlib import Path
from .prepare import HERE,ROOT,OUT,sha,save,ref

def main():
    admission=json.loads((HERE/'input_admission.json').read_text());native=json.loads((HERE/'native_preflight.json').read_text());fit=json.loads((HERE/'fit_probe.json').read_text())
    assert native['passed_within_scope']and fit['passed_within_scope']
    sources=HERE/'sources'
    save(HERE/'source_intake.json',{'asof':datetime.now(UTC).isoformat(),'primary':{'url':'https://doi.org/10.1186/s40854-024-00702-7','authors':['Masood Tadi','Jiří Witzany'],'published':'2025-01-13','pdf':ref(sources/'paper.pdf'),'relevant_method_sections_read':'Universe,rawpriceOLS,reference-spreads,marginals,CopulaCDF/conditionalprobabilities,signals,rollingformation,weeklyquantities/costs; not claiming every appendix/reference read','visually_reviewed_pages':[7,10,11,12],'source_period':'2021-01-01 through2023-01-19; not our2025-2026 account','source_universe':admission['source_universe'],'source_code':'Author implementation not obtained; public supplementary links are data CSVs, not recovered account code.','source_capital':'20000USDT, about20000 per side; weekly fixed quantities, not same as1000USDTgrossone or daily NAV rebalancing.','signals':'Copula of stationary raw-price BTC-reference spreads; BTC cancels, actual two-altcoin legs follow Table4 sign.','mathematical_adaptation':'Canonical valid rotations and asymmetric-logistic/Tawn kernels, explicitly distinct from literal printed Eq16/24 boundary identities. Exact author-code equivalence UNKNOWN.'},'github':{'secondary_discovery':{'path':'research/crypto/candle_boundary_native_20261114_v1/sources/github_research.md','claim_limit':'Secondary suggestion describes BTCvsALT and is insufficient to implement the reference-spread method.'},'student_reproduction':json.loads((sources/'github_capture.json').read_text()),'license':'MIT; preserved complete LICENSE','read_scope':'Complete core copula density/fitting module and marginal fitting functions; rolling engine sections on formation,signals,sizing and fees; README relevant methods. Not every notebook/GPU variant.','differences':['OKXspotBTC+7alts rather than paper20BinanceUSDTperpetuals','Only5copula families; no BB1/BB6/BB7/BB8/Joe/constrainedTawn/rotations','Optional CMI and same-bar flip modes are local variants','Fixed-notional external PnL loop is not nativeWallets/funding/margin; not executed or used for returns'],'admission':'Source-inspection only; no external account simulator used. No reproduction headline adopted.'},'X':{'query':'site:x.com "copula" "Tadi"','result':'No verified complete primary trading rule retrieved in targeted search. No post viewed.'},'YouTube':{'query':'site:youtube.com "copula" "Tadi"','result':'No verified complete primary video/transcript retrieved in targeted search. No video watched.'},'Douyin':{'status':'Prior paired-research indexed transcript7548096488870628643 remains a general discovery lead, not this full Copula method. No new video viewed or complete exact-rule transcript obtained.','no_rules_or_profit_admitted':True},'EOS':{'official_notice':'https://www.binance.com/en/support/announcement/detail/1e89a9ca957c4b0ca7502e60b993e201','notice_capture':ref(sources/'eos_notice_web_capture.txt'),'published_at_proxy':'2025-05-14T09:00:00Z','new_positions_stop':'2025-05-21T08:30:00Z','automatic_settlement':'2025-05-21T09:00:00Z','source_terminal_observation_retained_in_raw':True,'terminal_zero_volume_bar_quarantined':1,'EOS_to_A_futures_position_rollover_assumed':False,'source_historical_first_seen_certified':False},'XMR':'Official2024spot/Coin-Mnotice explicitly allows USDT-MXMRUSDT to continue; no spot->USDTperpetual delist inference. Current frozen market exists, and hourly archives obtained.','native_owner':ref(ROOT/'freqtrade/data/copula_reference.py'),'official_vs_local':'Upstream pinnedFreqtrade595originalfiles unchanged. Local statistical input module, explicitIStrategyextension,preparation,diagnostics only. Upstream DelistFilter declares supports_backtesting=NO; not claimed historical execution support.','native_funded_accounts':0,'new_accepted_alpha':0})
    save(HERE/'technical_failures.json',{'preserved':[{'evidence':'prepare_initial_failed.log','stage':'before download request plan','reason':'pandas timezone-mixed date_range endpoints','correction':'Normalize request-plan endpoints toUTC; no economic rules changed.'},{'evidence':['prepare_terminal_bucket_failed.log','download_terminal_bucket_failed.json','eos_terminal_raw_rows.json'],'stage':'all132officialtasks returned;1EOSMay grid rejected','reason':'Officialarchive contains terminal09:00zero-volume bar beyond ordinary tradable domain','correction':'Retainrawarchive and response; quarantine1terminal observation explicitly; reverify cachedSHA; no postterminal ffill.'},{'stage':'GitHubsourcepin','reason':'Unauthenticated REST rate limit403; original tool traceback retained in chat, originalHTTPbody not captured','correction':'git ls-remote HEAD, then fetch explicitcommit rawfiles; capturemanifest retains statuses.'},{'stage':'optional scientific package import','reason':'0.7.5Pythonpackage importsMatplotlib; paperenvironment lackedit. DifferentVibePythoncannotloadcp312wheel. Directkernelprobe emittednanobind teardown warnings.','correction':'Install plottingimportrequirements in study-onlytarget; use normal documented0.7.5package inPython3.12. Directkernel/Vibepaths notused for subsequentchecks.','transaction_runtime_environment_modified':False}], 'native_accounts_before_or_after_corrections':0})
    packages=[{'name':d.metadata['Name'],'version':d.version}for d in distributions(path=[str(HERE/'dependencies')])]
    save(HERE/'dependency_manifest.json',{'installation_scope':'Study-only --target; no global/runtimeenv upgrade','python':sys.version,'packages':packages,'files':[ref(p)for p in sorted((HERE/'dependencies').rglob('*'))if p.is_file()and '__pycache__'not in p.parts]})
    shutil.copyfile(ROOT/'freqtrade/data/copula_reference.py',sources/'copula_reference_owner_snapshot.py.txt')
    pending=['Dated EOS retired contract lot/precision/leverage metadata, without copying A/new-symbol specs','Full rolling formation: cointegration/lag choices and weekly phase fixed before returns; current3fixedETH/BCHfits are numerical probes only','Native paired lifecycle: source weekly fixed quantities vs our explicit wallet budget, two-leg failure, closing/rollover and EOS stop semantics','Complete funding/mark inputs, ownership and cash reconciliation for all actual holdings','Frozen full-account matrix and matched actual500wallet combinations; no economics computed yet']
    save(HERE/'status.json',{'status':'SOURCE20_INPUT_AND_COPULA_KERNEL_CHECKPOINT','passed_input_and_numerical_scope':True,'native_price_rows':native['input_rows'],'native_source_members':20,'new_official_archives':len(admission['archives']),'reused_native_series':len(admission['reused']),'kernel_family_rotation_combinations':39,'fitted_diagnostic_models':3,'fitted_candidate_models':fit['fitted_candidate_models'],'financial_returns':'UNKNOWN','native_funded_accounts':0,'new_accepted_alpha':0,'goal_achieved':False,'goal_status':'active','pending':pending,'orders_enabled':False,'services_changed':False,'committed_or_pushed':False})
    text='''# 参考价差 Copula：原生输入与数值核检查点

**本轮尚无新增资金账户，收益为 UNKNOWN。** 完成了原文机制核对、来源完整20成员小时价格、原生输入接口及完整家族数值核。未完成滚动策略和资金费／双腿账户合同，不能称为已复现论文收益或更高收益策略。整体目标继续 active。

[主文献](https://doi.org/10.1186/s40854-024-00702-7)与学生GitHub复现已固定原始文件和版本；原文相关PDF页已渲染核查。主要纠正是：参考BTC构造两个价差，BTC在多空价差之间抵消，实际交易两种山寨币；方向遵循表4。二手摘要的BTC对山寨币描述未采用。原文采用20,000本金、每边约20,000、周内固定数量；这不是当前1,000本金或总毛额1的账户，也不是每日重置权重。原文2021—2023收益没有计入本地结果。

学生仓库固定 `c6f3ab2f262a5528e61c37e2165565278d99c920`，MIT许可已保留。它采用OKX现货与较小币池、五种Copula及可选CMI／反转变体。只审查相关源码，未运行外部账户引擎，也不采用其收益。X／YouTube定向检索未获得完整同机制规则，抖音既有线索也未提供这一完整方法；未宣称观看视频或完整字幕。

## 完整来源池与价格

保留原文表1的BTC、ETH、BCH、XRP、EOS、LTC、TRX、ETC、LINK、XLM、ADA、XMR、DASH、ZEC、XTZ、ATOM、BNB、ONT、IOTA、BAT。复用13条经SHA核对的旧原生系列，新增132份官方月度1h ZIP并逐份重核官方CHECKSUM。共同输入域为2024-12-01至2026-08-31，含训练预热。

原生StrategyResolver／DataProvider实际读取20成员、295,497个价格行。19成员覆盖完整15,336小时；EOS仅覆盖公开合约终止前4,113小时。合并面板保留全部日期和EOS结构性空值，未通过全局inner join剪短窗口、填充终止后价格、改名接A或删除EOS。

[Binance EOS公告](https://www.binance.com/en/support/announcement/detail/1e89a9ca957c4b0ca7502e60b993e201)区分08:30停止新仓与09:00自动结算。EOS五月月档还有一根09:00开盘、零成交量的终止记录；原始档案及行值保留，该根明确隔离，不当作后续可成交小时。XMR现货／币本位的下架不能推广为USDT永续下架，本轮保留其官方小时数据。

## 已验证的本地原生扩展

`freqtrade/data/copula_reference.py`持有统计输入与数值核；`CopulaInputs`通过IStrategy接入。它们是本地扩展，不是上游自带Copula策略。原生Freqtrade固定 `c051e9065c5b677f03817dd1f83cb980af1ed684`，595个原始文件未改且无额外引擎Python源。

- 三个504完整小时形成窗：2025-01-03、2025-06-06、2026-08-28。55个原价无截距OLS系数与独立最小二乘对照；27,720个价差单元以标量公式重建。真实历史截断与未来价格扰动均不改变已完成形成窗。
- 39种家族／旋转组合覆盖Gaussian、Student、Clayton、Gumbel、Frank、Joe、BB1、BB6、BB7、BB8及两个约束Tawn方向。逐核检查均匀边际与CDF数值导数，避免将库的hfunc1／hfunc2方向误配成论文h1|2／h2|1。
- 三个事前固定ETH/BCH数值探针，拟合正态／t／Cauchy边缘，再检查全部39候选MLE／AIC，共117个候选拟合。它们不是协整筛选出的交易对、未产生策略收益；保留全部候选和优化尝试。
- Tawn保持一个非对称参数等于1，仅估计另两个自由参数，AIC按2参数计。未用三参数Tawn冒充论文两参数族，也未退回只有五族的学生简化版本。
- 论文印刷式16／24的部分字面边界身份不满足Copula边际条件，已用PDF视觉检查和数值反例保留。此处使用有效的规范旋转／非对称logistic核，属于明确本地修正；作者实际代码是否同样处理仍未知。
- 输入类在原生`ft_bot_start`入口直接拒绝金融运行，防止安全回调吞掉错误后生成全现金账户假成果。

科学依赖仅安装到本研究的`dependencies/`，固定pyvinecopulib0.7.5及完整文件SHA。没有升级交易运行环境。数据日期夹具、EOS终止桶及依赖导入问题均保留；未修改经济信号、费用或旧失败记录。

## 尚未完成

上游DelistFilter明确不支持历史回测；当前冻结市场快照也没有已退市EOS的合约规格。仅有价格和终止时间不足以认证原生数量、保证金及终止成交。

'''
    text+='\n'.join(f'{i+1}. {item}'for i,item in enumerate(pending))
    text+='''

后续必须在原生支持接口补齐这些合同，再冻结完整共同窗口、本金、毛额、费用和组合规则，运行实际ZIP并对账。不能把本检查点登记成Vibe bench或Freqtrade资金账户。当前新增factor_analysis、资金账户、接受alpha和组合收益证明均为0，没有订单、模拟服务启动、部署、提交或推送。

证据入口：`input_admission.json`、`native_preflight.json`、`fit_probe.json`、`source_intake.json`、`technical_failures.json`、`dependency_manifest.json`、`status.json`。`verify.py`重核来源与输入SHA、原生检查记录、39候选集合和资金启动拒绝；不将检查通过写成盈利通过。
'''
    (HERE/'README.md').write_text(text)
    print('Documented source20nativeinput checkpoint; financial return UNKNOWN; goal active')
if __name__=='__main__':main()
