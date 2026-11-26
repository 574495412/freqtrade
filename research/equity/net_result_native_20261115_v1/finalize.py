"""Seal technical equity evidence without producing historical strategy returns."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PARENT=HERE.with_name('start_lifecycle_native_20261115_v1')
VIBE=Path('/Users/chrise/GitHub/Vibe-Trading')


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))
def save(p,r):Path(p).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')


def main():
    protocol=read(HERE/'protocol_freeze.json')
    assert sha(protocol['path'])==protocol['sha256']
    for r in read(HERE/'implementation_freeze.json'):assert sha(r['path'])==r['sha256']
    previous=read(PARENT/'artifact_manifest.json')
    for p,digest in previous.items():assert sha(ROOT/p)==digest,p
    assert len(previous)==229
    source_manifest=ROOT.parent/'CZ-Agent/vendor/FREQTRADE_SOURCE.json'
    upstream=read(source_manifest);vendor=ROOT.parent/'CZ-Agent/vendor/freqtrade'
    for p,digest in upstream['files'].items():assert sha(vendor/p)==digest,p
    assert len(upstream['files'])==595
    audit=read(HERE/'independent_audit.json')
    assert audit['passed'] and audit['original_full_paths_exact']==6
    assert audit['main_synthetic_fills']==22 and audit['parent_scalar_checks']==504
    assert read(HERE/'source_witnesses.json')['passed']
    assert read(HERE/'input_admission.json')['verified_files']==514
    for r in read(HERE/'source_receipts.json'):assert sha(r['path'])==r['sha256']
    envelopes=[]
    for r in read(HERE/'execution_receipt.json'):
        d=HERE/'runs'/r['attempt']
        exports=read(d/'native_export_receipt.json')
        for e in exports:assert sha(e['path'])==e['sha256']
        envelope=dict(scope='Post-execution binding of synthetic native result,not an independent backtest',
            native_zip_diagnostic=exports,net_report=ref(d/'equity_net_result.json'),
            actual_native_trace=ref(d/'native_trace.json'),config=ref(d/'config.json'),
            synthetic_quote_input=ref(d/'synthetic_ohlcv.feather'),
            pre_execution_implementation=ref(d/'implementation_freeze.json'),
            protocol=protocol,native_commit=upstream['commit'],
            historical_accounts=0,bench_records=0,financial_strategy_performance=None)
        save(d/'net_result_envelope.json',envelope);envelopes.append(ref(d/'net_result_envelope.json'))
    owners=[VIBE/'agent/src/skills/factor-research/SKILL.md',
            VIBE/'agent/src/skills/strategy-dev-manager/SKILL.md',
            VIBE/'agent/src/factors/registry.py',
            VIBE/'agent/src/tools/factor_analysis_tool.py',
            VIBE/'agent/src/tools/backtest_tool.py',
            VIBE/'agent/src/tools/autopilot_tool.py']
    save(HERE/'ownership.json',dict(
        vibe_head='915f2713e2d5476a26663c69f32c5a9a1d4bea64',
        working_tree_not_pure_head=True,current_owner_files=[ref(p)for p in owners],
        freqtrade_original_manifest=ref(source_manifest),upstream_files=595,
        local_extension=ref(ROOT/'freqtrade/optimize/equity_net_result.py'),
        lifecycle_owner_unchanged=ref(ROOT/'freqtrade/optimize/equity_fixture_lifecycle.py'),
        historical_entry_unchanged=ref(ROOT/'freqtrade/optimize/equity_backtesting.py'),
        equity_upstream_support=False,
        new_registry_definitions=0,factor_analysis_calls=0,sdm_registrations=0,bench=0))
    save(HERE/'source_intake.json',dict(
        github=dict(repository='WilliePim/edge-lab-public',commit='0310d6c52368ec72bcb4074adb1dcc45fbb4941d',
            git_tree_paths=472,license='MIT;original LICENSE retained',receipts=ref(HERE/'source_receipts.json'),
            fully_read=['LICENSE','form4_scanner/parse.py','form4_scanner/cluster.py','backtest/rematch_50_300m/STUDY.md'],
            read_sections={'README.md':'method,data quality,Form4filter,dependencies,known limits'},
            whole_repository_audited=False,source_financial_engine_executed=False,
            source_modules_executed='Only reviewed pure parse/cluster on synthetic XML; offline subprocess',
            source_witnesses=ref(HERE/'source_witnesses.json'),
            selected_source_environment='Vibe venv Python3.11.14/lxml6.1.3;financial environment unchanged',
            original_source_audit_failure='Freqtrade paper venv lacked lxml;no packages installed',
            mechanism='Insider purchase and grouped buyers,same existing family;not a new independent alpha',
            original_retest_summary='INCONCLUSIVE;no local replication of source returns',
            historical_data_not_redistributed=True),
        sec=dict(url='https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html',
            web_tool_read='25lineHTMLcode table;P includes both open-market and private purchases',
            direct_http_status=403,response_is_not_code_evidence=True,raw_receipts=ref(HERE/'web_source_receipts.json')),
        x=dict(directed_search=True,new_original_post_read=False,new_complete_rules_admitted=False),
        youtube=dict(directed_search=True,new_video_watched=False,new_transcript_read=False,new_complete_rules_admitted=False,
            previous_equity_intake_explicitly_reused=ref(HERE.with_name('earnings_consensus_admission_native_20261115_v1')/'source_intake.json')),
        douyin=dict(url='https://www.douyin.com/video/7690234530678889715',search_title='美股半导体高管谁在买谁在卖？',
            direct_http_status=200,bytes=1072,body='4XX risk block,not video content',
            web_tool_readable_lines=0,video_watched=False,rules_admitted=False),
        source_replication=False,new_accepted_independent_alphas=0,account_returns_admitted=False))
    save(HERE/'technical_notes.json',dict(
        new_checks=26,parent_lifecycle_checks=14,total_distinct_checks=40,
        initial_23_checks_repeated_not_added=True,
        dividend_boundary_unit_examples=3,
        main_native_scenarios_have_zero_actual_dividends=True,
        zero_dividend_fixtures_not_historical_dividend_validation=True,
        first_lint_failure='Complexity17andline102;split validation helper and format before native execution',
        source_audit_initial_failure='No lxml in paper environment;unchanged source succeeded in existing Vibe venv',
        research_semantic_review='F,E9 with only inherited native_probe.py unused EquityMark/capture_equity_nav imports ignored',
        inherited_unused_imports_preserved_under_frozen_native_producer=True,
        production_full_ruff_pass=True,
        no_financial_parameters_optimized=True))
    save(HERE/'verification.json',dict(
        verified_at=datetime.now(UTC).isoformat(),passed=True,native_original_files=595,
        preserved_parent_manifest=ref(PARENT/'artifact_manifest.json'),preserved_parent_paths=229,
        pre_execution_hashes_exact=True,main_native_start_runs=6,main_native_zips=6,
        initial_extra_technical_start=1,main_native_synthetic_fills=22,main_snapshots=84,
        independent_cash_nav_scalar_checks=504,exact_original_full_paths=6,
        exact_extrema_counterfactuals=2,new_checks=26,parent_checks=14,
        net_result_envelopes=envelopes,source_witnesses=ref(HERE/'source_witnesses.json'),
        input_admission=ref(HERE/'input_admission.json'),historical_entry_still_closed=True,
        historical_stock_accounts=0,combination_accounts=0,new_accepted_alphas=0,
        factor_analysis_calls=0,bench=0,financial_performance=None,higher_comparable_return_found=False,
        objective_achieved=False,orders_enabled=False,services_changed=False,commit_or_push=False))
    (HERE/'README.md').write_text('''# 港美股Freqtrade：原生净结果接线，尚无收益晋级

**本轮新增完整历史股票账户、组合账户、独立alpha、factor_analysis与bench均为0；未找到更高可比净收益。** 完成了原生净结果模块和内部人增持来源核对。加密carry的结果另行封存，不能计入港美股证据。

真实目标仍为原始503美股、11港股，2025-01-01～2026-08-31，USD/HKD独立账户。没有删证券、缩窗口、改旧方向或权重。此次ABC合成七交易日夹具不是目标历史窗口。

## 原生结果模块

新增本地`freqtrade/optimize/equity_net_result.py`，在既有合成生命周期之前组合。继承的Configuration→StrategyResolver/IStrategy→Backtesting.start→原生撮合/Trade/Order/Wallet→实际ZIP路径保持不变。模块只在实际钱包当前时钟读回费用/股息净值，逐项证明与原边界完全相同，再生成单独净结果及绑定实际ZIP、原始报价、配置、协议和事前实现哈希的结果封套。没有另建资金模拟器。

净结果包含原始初始现金、期末NAV、实际已付费用、未收股息、已收股息、待结算卖款、期末持股、净变化和开收盘采样回撤；保留持股及应收，没有虚构期末平仓。现金分区、持股市值、NAV、时钟/币种、费用和股息历史消失等均拒绝。它不会扣除假想的未成交退出费用，因此期末合同必须与其他策略一致后才可比较。

原生ZIP的交易统计仍是未含股票overlay的诊断，完全不用于净指标。普通合成场景中原生交易利润0，真实费用6.40使1000现金净值变为993.60，净变化−0.64%；开收盘采样回撤1.02%。持股夹具期末净值1001.80，保留股票；这些数值只证明会计接线，**不是股票策略收益**。结果显式令financial_strategy_performance、CAGR、Sharpe为null，历史账户/bench为0。

六个主合成场景为XNYS/XHKG各普通、未来高低价扰动、期末持股，完成6次真实start、6份ZIP、22笔合成成交、84个净值边界和42个回调。另一次初始美股技术运行保留，不计为第七主场景或新经济样本。六条完整成交、订单、现金、NAV和回调与原版本逐项完全一致；两市场高低价反事实的净结果也相同。独立审核从实际订单/费用/结算标签重建504个标量并以Decimal独立核对净变化和最大回撤，不导入候选或资金组件。

26项新检查及14项原生命周期回归通过，共40项不同检查；初始23项重跑不加计。三个新增的股息单元边界分别验证未付应收、应收转现金不重复收入及应收消失拒绝；六个主生命周期本身没有实际股息，不能称其认证了历史股票股息。生产模块完整Ruff通过。研究脚本F/E9检查只豁免复制自父运行器且已冻结的两条未使用导入；初始lint记录保留。首次复杂度/长行问题在所有原生运行前拆分格式化；源探针因paper环境无lxml失败后改用已有Vibe环境，没有安装或改变金融环境。

固定上游Freqtrade c051e9065c5b677f03817dd1f83cb980af1ed684的595个文件及父轮229个封存路径当前哈希一致。当前Vibe HEAD 915f2713e2d5476a26663c69f32c5a9a1d4bea64加已有本地扩展，Registry、factor_analysis、hypothesis/config/SignalEngine/backtest/bench所有者分列ownership.json。普通EquityBacktesting历史入口仍在构造前拒绝。这是本地合成结果扩展，不是上游官方股票支持或完整真实账户准入。

## 新固定来源：内部人买入和联名分组

固定[edge-lab-public](https://github.com/WilliePim/edge-lab-public/tree/0310d6c52368ec72bcb4074adb1dcc45fbb4941d)，树472路径，5份选定文件逐Git blob核对，MIT许可原文保留。完整阅读parser、cluster、许可及研究摘要，README按记录的相关章节阅读，不声称审计全仓。仅在断网子进程执行两个已阅读纯解析/分组模块和合成XML，没有运行源下载器或资金引擎。

源探针核对：一个申报交易的两位联名人产生两条归属记录，但经济金额只算一次、买方组只计一个；重叠联名组可传递合并。该分组不是对实际独立经济决策的认证。缺数值变0、含否定语境的10b5-1文本仍被正则标记，这些是具体合成输入的字面行为，不是历史错误率。仅P/A、非衍生、非计划标记及金额筛选仍会接纳正文写明私人购买的样本；[SEC原始代码表](https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html)明确P同时包括公开市场或私人购买，不能仅凭P称“纯公开市场买入”。SEC网页工具正文可读，直接HTTP403原响应另留，不把拦截页当规则证据。

源公开重测摘要结论INCONCLUSIVE；没有复制其价格/存续/匹配样本或接受作者超额收益。原文还说明价格资料未分发，事件超额不是本地完整资金账户。该机制与已有内部人增持同族，不计为新独立因子，未按负结果反向调阈值。

真实复核514个原始/暂存价格特征文件SHA及表头，0份包含完整内部人事件、权益身份和观察时钟。本机已有Vibe原生SEC insider暂存，不能说“没有任何SEC数据”；其联名/修订、金额精度/币种、证券类别、例行交易历史及公开时钟仍需完整准入后接入。没有用缺事件列推定零交易，也没有将公司回购混同高管买入。输入未准入，故Registry新定义0、预测0、账户0、bench0。

X和YouTube定向检索未取得本轮可准入完整原始规则；未新观看视频或读取完整转录，旧股市来源状态显式绑定复用。新抖音半导体高管线索网页工具0正文，直接HTTP200的1072字节实际为4XX风险拦截页，未读取视频规则。社交线索不代替可执行规则和完整账户。

## 下一段账户门槛

真实原始池的证券存续、名义报价、历史整手/价位、经纪费税适用、现金结算、股息权利/付款、其他公司行动/退市、观察发布时钟及期末合同仍需完成；做空还需借券/召回/空头股息/双腿资金。仅补净报表不解除这些约束。

输入通过后才可Registry和实际factor_analysis，再经支持的信号接口及完整Freqtrade账户、结果和bench读回。比较必须同市场/币种/原始资本/完整窗口/费用税/资金与期末合同；组合使用实际分配钱包。当前没有收益冠军或独立样本外、纸盘、实盘证明。未下单、部署、改运行服务、提交或推送；整体目标未完成。

证据：[独立对账](independent_audit.json)、[完整进程](execution_receipt.json)、[源方法探针](source_witnesses.json)、[输入准入](input_admission.json)、[所有权](ownership.json)、[来源状态](source_intake.json)、[验证](verification.json)、[文件清单](artifact_manifest.json)。
''')
    files=sorted(p for p in HERE.rglob('*')if p.is_file()and'__pycache__'not in p.parts and p.name!='artifact_manifest.json')
    files.append(ROOT/'freqtrade/optimize/equity_net_result.py')
    save(HERE/'artifact_manifest.json',dict(scope='Post-execution technical checkpoint,not pre-result financial freeze',
        files=[dict(**ref(p),bytes=p.stat().st_size)for p in files]))
    print('SEALED',len(files),'paths;old229exact;6nativefixturestarts;0historicalstockaccounts')


if __name__=='__main__':main()
