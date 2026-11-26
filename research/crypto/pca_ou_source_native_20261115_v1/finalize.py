"""Freeze a rejected native predictive study; never invent account economics."""
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
VIBE=ROOT.parent/'Vibe-Trading'


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def ref(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))
def save(p,r):Path(p).write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')


def main():
    verification=read(HERE/'verification.json');assert verification['passed']
    result=read(HERE/'predictive_review_results.json');protocol=read(HERE/'protocol.json')
    pre=read(HERE/'native_preflight.json');out=Path(pre['factor_output'])
    assert len(result['admissions'])==2 and not any(r['predictive_stage_admitted']for r in result['admissions'])
    previous=[]
    for name in ['paired_carry_reservation_native_20261115_v2','fip_path_source_native_20261115_v1']:
        path=HERE.with_name(name)/'artifact_manifest.json';manifest=read(path)
        if isinstance(manifest.get('files'),list):
            for r in manifest['files']:assert sha(r['path'])==r['sha256'],r['path']
            count=len(manifest['files'])
        else:
            for p,digest in manifest.items():assert sha(ROOT/p)==digest,p
            count=len(manifest)
        previous.append(dict(study=name,manifest=ref(path),verified_paths=count))
    owners=[VIBE/'AGENTS.md',VIBE/'agent/src/skills/factor-research/SKILL.md',
        VIBE/'agent/src/skills/strategy-dev-manager/SKILL.md',VIBE/'agent/src/factors/registry.py',
        VIBE/'agent/src/factors/factor_analysis_core.py',VIBE/'agent/src/tools/factor_analysis_tool.py',
        VIBE/'agent/src/tools/backtest_tool.py']
    save(HERE/'ownership.json',dict(
        freqtrade_workspace_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        freqtrade_branch=subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),
        vibe_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=VIBE,text=True).strip(),
        native_commit=protocol.get('native_commit','c051e9065c5b677f03817dd1f83cb980af1ed684'),
        current_owner_files=[ref(p)for p in owners],
        local_extension=ref(ROOT/'freqtrade/data/pca_ou.py'),
        actual_native_chain='StrategyResolver/IStrategy/DataProvider feature ownership;isolated actual Registry/factor_analysis',
        no_funded_Backtesting_start_Trade_Order_Wallet_ZIP_executed=True,
        upstream_pca_strategy=False,source_original_engine_used=False,
        preserved_parent_studies=previous))
    cutoff_counts=[]
    for horizon in [1,24]:
        labels=pd.read_parquet(out/f'native_labels_{horizon}h.parquet');days=labels.index
        endpoints=days+pd.Timedelta(hours=horizon+1)
        cutoff_counts.append(dict(horizon_hours=horizon,decision_year2025_labels_ending_in2026=int(((days.year==2025)&(endpoints.year==2026)).sum())))
    save(HERE/'label_cohort_notes.json',dict(
        grouping='Year of decisionD,not separate annual funded wallets',cross_year_labels=cutoff_counts,
        no_claim_of_independent_annual_returns=True,one_hour_and_daily_labels_overlap=True,
        full_and_year_splits_reuse_observations=True,tail_labels_immature_not_zero=True,
        hypothetical_full_account_window_unchanged=[protocol['account_start'],protocol['account_end']]))
    save(HERE/'source_intake.json',dict(
        github=dict(repository=protocol['source_repo'],commit=protocol['source_commit'],
            tree_paths=17,selected_files=10,receipts=ref(HERE/'source_receipts.json'),
            read_complete=['README.txt','main.py','pyproject.toml','src/data_loader.py','src/pca_engine.py',
                'src/residual_model.py','src/ou_estimator.py','src/s_score.py','src/strategy.py','src/backtester.py'],
            whole_repo_audited=False,license_file_present=False,
            native_module_independently_implements_standard_math=True,
            executed_source='Only5reviewed puremath/data class definitions;math methods on fixed native price views,no source CSVconstructor',
            external_downloader_or_financial_engine_executed=False,
            source_dynamic_marketcap_universe_and2021_2022_data_not_distributed=True,
            source_zero_cost_active_gross_normalized_curve_not_wallet=True),
        primary_speaker_sources=dict(
            event='https://www.adml.ai/2022/09/abu-dhabi-machine-learning-season-3-episode-1/',
            blog='https://marti.ai/ml/2022/09/17/hierarchical-pca-crypto-clustering.html',
            read_scope='Speaker abstract;blog method,500day/101coin in-sample setup,cluster/eigen correlation formulas and final privatebook claim',
            limit='No disclosed complete executable alpha/capital/cost/funding account for the private performance image',
            hpca_strategy_executed=False,pca_candidate_not_retuned_to_hpca=True),
        youtube=dict(metadata=ref(HERE/'youtube_metadata.json'),
            page_status=200,author='Abu Dhabi Machine Learning',video='4r6Yts1v9nk',
            full_video_watched=False,full_transcript_read=False,caption_tracks_found=0,
            web_fetch_throttled=True,ordinary_public_page_captured=True,rule_or_return_admitted=False),
        x=dict(directed_search=True,primary_new_post_read=False,
            third_party_index_only_not_admitted=True,no_promotional_returns_used=True),
        douyin=dict(url='https://www.douyin.com/video/7569159733249101107',
            index_topic='Hybrid regime-learning paper;not this crypto PCA-OU strategy',
            web_page_readable_lines=0,direct_http_status=200,bytes=1072,
            actual_body='4XX access risk block',video_watched=False,complete_rules_admitted=False),
        capture_receipts=ref(HERE/'social_source_receipts.json'),
        onchain_followup='Prior HashRibbons completion-clock failures retained;no altered latency/staleness contract or new onchain alpha claimed',
        new_source_math_hypotheses=1,new_accepted_independent_alphas=0,
        source_account_replication=False,private_source_performance_reproduced=False))
    save(HERE/'failure_record.json',dict(
        reason='Both frozen predictive horizons fail stability gates;24h2026also loses to reversal239',
        admissions=result['admissions'],parameters_retuned_after_outcomes=False,
        sign_flipped=False,universe_reduced=False,cost_added_back=False,
        funded_accounts=0,financed_returns=None,combination_accounts=0,new_accepted_alphas=0))
    save(HERE/'technical_failures.json',dict(
        source_and_financial_environment_unchanged=True,
        production_initial_lint='Long line style;formatted before actual input computation',
        initial_launcher='Venv executable symlink resolved to basePython without pyarrow;0FAcalls,initialprocess/emptyfolder retained',
        original_predictive_process='4actualFAcalls,3audited;stopped on auxiliary original-parquet vs postedCSV rank assertion',
        control_csv_ties=ref(HERE/'csv_reversal_control_precision_witnesses.json'),
        pca_candidate_input_check=ref(HERE/'csv_input_precision_witnesses.json'),
        corrective_review='Actual postedCSV input audit,24predeclared measurements;4old artifact sets identical;28actualcalls including4technicalduplicates',
        no_native_tool_parser_or_candidate_value_change=True,
        social_capture='Missing optionalbs4 before anyrequest;stdlibparser used instead,no package installed',
        final_verifier='Initial helper whitespace syntax error before execution,fixed auxiliary only;oldsource and log retained'))
    rows=[]
    for r in result['receipts']:
        if r['factor']!='pca_ou_reversal':continue
        v=r['independent'];label={'all':'全窗决策','calendar2025':'2025决策','calendar2026_through_aug':'2026截至8月决策'}[r['split']]
        rows.append(f"|{r['horizon_hours']}小时|{label}|{r['observations']}|{v['raw_ic_mean']:.6f}|{v['raw_ir']:.4f}|{v['raw_positive_ratio']*100:.2f}%|未通过|")
    text='''# PCA残差/OU均值回归：预测门槛未通过

**新增一项机制不同的本地因子假设，两个冻结预测尺度均被拒绝。没有新增资金账户、组合、已接受独立alpha或Vibe bench；净收益/CAGR/资金回撤未知。** 这是加密研究，不是港美股结果。寻找更高可比收益的整体目标仍未完成。

## 固定范围与来源

固定[sophie-lan/crypto-pca-statarb](https://github.com/sophie-lan/crypto-pca-statarb/tree/cdc7a5a19bb84269ad5b441b9c6319c0d3cdea3b)提交cdc7a5a19bb84269ad5b441b9c6319c0d3cdea3b，树17路径，10份选定文件逐Git blob/SHA核对并按source_intake范围阅读。树没有LICENSE；本地产品模块独立实现标准线性代数，没有复制或执行来源资金引擎。

作者2021-09-26～2022-09-25小时动态市值top40及原价格CSV未公开。本地保留既有固定40个Binance永续，不是作者动态成员、全市场历史存续或独立样本外复现。与旧残差动量不同：这里拟合两PC残差的OU位置，预期回归；旧模型累积共同市场beta残差并顺势。数学不同不证明信息或经济独立。

每个D时点用D−240h至D−1h的240个完整小时收盘，实际239个对数收益。相关矩阵前两PC，特征向量除样本SD后L1归一；带截距ridge全部系数加1e−5惩罚；残差再次去均值后累积，拟合带截距AR1，保留0<b<1及正平衡SD；分数为负的源s-score。原生1h下一行D+1OPEN作为假定执行起点。

特别保留：源残差去均值使窗口末累计残差约0，实际三个源核对时点最大绝对值1.38e−16。没有改成累计最后N小时、去掉截距、增加半衰期筛选或修理后冒称源码一致。原源阈值1.25/.75/1.0滞后持仓规则已记录，但本轮只检验源分数的截面方向，不是该完整阈值策略的资金复现。

## 原生预测结果

计划资金窗口仍为2025-01-01 00:00～2026-08-31 23:00 UTC。预测标签分别为D+1OPEN到D+2OPEN及D+25OPEN；1小时队列有14590个不重叠标签，24小时队列有607个。后者尾部不成熟标签不补0，假定资金窗口没有改短。两尺度相互重叠；全窗和年度队列重复使用观察，不能相加当独立样本。

|预测尺度（非已验证持有期）|决策队列|观察数|IC均值|IC的IR|IC>0比例|冻结准入|
|---|---|---:|---:|---:|---:|---|
'''+ '\n'.join(rows)+'''

门槛在结果前冻结：所有三队列IC>.03、IR>.5、正比例>.55，胜过两个原始反转控制，且 scoped平均绝对Pearson<.99。1h全窗弱；24h虽全窗IC约.0378，IR仅.215，2026决策IC约.02746还低于reversal239的.04618。没有改变窗口、符号、阈值或币池救回。

三个控制为前1小时反转、前239个收益反转、低波动239，使用与OU分数完全相同的有效资产掩码。与它们的全时点平均绝对Pearson分别.17447/.16739/.12889，只是这三个面板的局部去重，不是全Registry独立性证明。低波动24h全窗IC约.07755但IR约.256，同样未过稳定门槛，不新增“赢家”。

年度划分按决策D所属年份，**不是独立年度资金收益**。2025队列有2个1h标签及1个24h标签的终点落在2026，明确记录于label_cohort_notes.json。没有模型用这些未来标签训练权重，也不将队列称作独立样本外。

真实调用Registry和factor_analysis：4个隔离诊断view、24个不同的因子/尺度/队列测量；另4次技术重放，实际共28次工具调用。全局Registry未改。独立秩/分位重建核对121576个IC行和607880个分组NAV单元，包含队列重复，**分组毛曲线不是完整账户、净利润或组合资本**。

## 正确性与失败保管

40份既有原生1h行情SHA/完整小时轴已核对，无新增行情下载。真实StrategyResolver→IStrategy.bot_start→DataProvider计算14592个小时，每个时点39～40个有效OU分数，只有2个无效OU格保留NaN、不填0；两个格均与源数学类的未知状态额外核对。源三个实际时点、120个资产分数核对最大误差7.57e−14；不是声称全14592时点均逐一执行原作者实现。三个真实时点截断/未来扰动完全一致；这不是完整官方lookahead/recursive认证。

5253120个真实原生指标映射单元及607880个完整前瞻标签价格单元核对。16项不同组件检查通过，包括239/240边界、未来值扰动、截断、单位、价格尺度、缺行、非有限/布尔值和不足预热拒绝。生产模块完整Ruff及本地脚本F/E9检查通过。上游Freqtrade c051e9065c5b677f03817dd1f83cb980af1ed684的595原始文件当前逐字节一致，没有额外上游Python。新freqtrade/data/pca_ou.py及诊断壳/Registry桥是本地扩展，不是上游自带策略。

首次启动器把venv的Python软链resolve成base解释器，缺pyarrow，0次FA；错误进程、日志和空目录保留，改为不解析软链的绝对venv路径，没有安装依赖。原流程完成4次FA后在独立审核停止：reversal1控制的极近值经工具默认CSV解析改变19个时点的秩，最大单点IC差.003383。PCA候选本身未见该秩差。审核改为对照工具实际读入的CSV，不改原生工具、分数、标签或门槛；后续四套原始CSV/IC/summary/group结果五文件均逐字节复现。原28次过程和错误保持，不能只报24次调用或把重放计新假设。

首次可选bs4依赖缺失发生在社交HTTP请求前，改用标准库HTMLParser，没有改变金融环境；最终验证器首次辅助语法错误、初稿和日志也保留。所有修订只在输入/审计/启动技术边界，没有修改经济候选。

## 多来源学习边界

跟随[ADML原始活动页](https://www.adml.ai/2022/09/abu-dhabi-machine-learning-season-3-episode-1/)读取Gautier Marti的HPCA摘要，并核对[作者博客](https://marti.ai/ml/2022/09/17/hierarchical-pca-crypto-clustering.html)的500日/101币样本内相关、聚类及HPCA方法。该页末尾交易book的表现图缺完整可执行alpha、现金/成本/资金费工件，没有采用其收益或用HPCA救回此候选。

[ADML官方YouTube](https://www.youtube.com/watch?v=4r6Yts1v9nk)普通HTTP200取得原作者、标题和描述；本次响应没有字幕轨道，未观看或读取完整转录。网页工具被限流与普通页面成功均分别保留。X仅有检索/第三方索引，未取得本轮完整原始规则，不采用宣传收益。新抖音相关索引实际主题为金融状态论文，不是此PCA-OU策略；直接HTTP200的1072字节是4XX风险拦截，网页工具0正文，未观看或读视频规则。

链上估值/矿工收入线索仍受此前HashRibbons已确认的完成时钟缺口制约；未把旧失败重跑成新准入，也不延长陈旧容忍度来取得资金曲线。

## 资金边界

两个预测尺度停止晋级：未运行资金Configuration/Backtesting.start/Trade/Order/Wallet/ZIP链，没有新账户、组合、SDM或bench，也没有CAGR/净收益/资金回撤。原定通过后的1000/500钱包、15/8bps费用、实际分仓组合及源池资金费/历史规则准入仍在protocol.json，未用登记代替执行。旧40池的资金费/历史规格未知没有被本轮价格完整性消除。

既有carry和FIP封存重新核验；所有旧失败保留。未下单、部署、改运行服务、提交或推送。拒绝只针对这项固定币池、窗口与分数方向的本地诊断，不能推广为所有PCA/OU均值回归都无效；更高可比收益和有效多策略组合目标仍在继续。

证据：[冻结协议](protocol.json)、[原生预检](native_preflight.json)、[24测量结果](predictive_review_results.json)、[CSV秩差](csv_reversal_control_precision_witnesses.json)、[技术修订](technical_failures.json)、[独立验证](verification.json)、[所有权](ownership.json)、[来源状态](source_intake.json)、[封存清单](artifact_manifest.json)。
'''
    (HERE/'README.md').write_text(text)
    save(HERE/'goal_audit.json',dict(
        verified_at=datetime.now(UTC).isoformat(),
        saved_goal_market='crypto',previous_goal_turn='PROGRESS',current_goal_turn='PROGRESS',
        progress='New native PCA/OU module,16checks,28actualFAcalls/24unique diagnostics;both rejected;source/audit evidence retained',
        source_original_dynamic_universe_not_reproduced=True,new_mechanism_hypotheses=1,
        new_accepted_independent_alphas=0,funded_accounts=0,combination_accounts=0,
        higher_comparable_return_found=False,independent_oos=False,objective_achieved=False,
        blocked=False,no_financial_child_running=True,orders_enabled=False,services_changed=False))
    files=sorted(p for root in [HERE,out]for p in root.rglob('*')if p.is_file()and'__pycache__'not in p.parts and p!=HERE/'artifact_manifest.json')
    files.append(ROOT/'freqtrade/data/pca_ou.py')
    save(HERE/'artifact_manifest.json',dict(scope='Post-execution rejected native predictive study,not pre-result freeze',
        files=[dict(**ref(p),bytes=p.stat().st_size)for p in files]))
    print('SEALED',len(files),'files;onehypothesis/twohorizonsrejected;0fundedaccounts')


if __name__=='__main__':main()
