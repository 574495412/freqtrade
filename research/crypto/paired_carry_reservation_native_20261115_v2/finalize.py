"""Post-execution evidence/report sealing; no financial computation or orders."""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARENT = HERE.with_name('paired_carry_source_native_20261115_v1')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    return {'path': str(Path(path).resolve()), 'sha256': sha(path)}


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')


def main():
    plan = read(HERE/'plan.json')
    out = Path(plan['output_root'])
    results = read(HERE/'results.json')
    assert results['actual_native_leg_wallets'] == 24 and results['native_fills'] == 48
    assert results['leg_hour_points'] == 350208
    assert results['all_frozen_numeric_gates_passed']
    assert read(HERE/'full_parent_parity.json')['complete_native_paths'] == 17
    old_manifest = read(PARENT/'artifact_manifest.json')
    for item in old_manifest['files']:
        assert sha(item['path']) == item['sha256'], item['path']
    assert len(old_manifest['files']) == 505
    checked = []

    def check_refs(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                assert sha(value['path']) == value['sha256'], value['path']
                checked.append(value['path'])
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)

    check_refs(plan)
    check_refs(read(HERE/'protocol.json'))
    check_refs(read(HERE/'protocol_freeze.json'))
    check_refs(read(HERE/'preflight.json')['plan'])
    assert sha(HERE/'native_runner.py') == plan['runner_sha256']
    upstream = read(plan['engine_manifest']['path'])
    for name, digest in upstream['files'].items():
        assert sha(Path(plan['engine_source'])/name) == digest, name
    assert len(upstream['files']) == 595
    extra_py = sorted(str(p.relative_to(plan['engine_source']))
                      for p in Path(plan['engine_source']).rglob('*.py')
                      if str(p.relative_to(plan['engine_source'])) not in upstream['files'])
    assert not extra_py, extra_py
    now = datetime.now(UTC).isoformat()
    previous_social = HERE.with_name('source_reversal_adx_native_20261115_v1')/'source_intake.json'
    source = dict(
        recorded_at=now, recorded_after_financial_runs=True,
        github=[dict(repository='idreesm18/crypto-perp-funding-carry',
                     commit='06d05d08a88c107dc84b69dc21009864dbd9b878',
                     fully_read_selected_files=['README.md', 'src/2_signal.py', 'src/3_portfolio.py', 'src/4_backtest.py'],
                     captured_not_fully_read=['WRITEUP.md'],
                     followup_read_sections={'src/1_data_pull.py': 'funding archive function; calc_time/last_funding_rate mapping'},
                     license_file_in_pinned_tree=False,
                     limitation='Concurrent settled funding sets weight and same-event entitlement; archived data does not certify indicative values known beforehand.'),
                dict(repository='zwmjj/funding-rate-arb',
                     commit='b06dc3ac3453b08edfb430b527a1e0a408d42a63',
                     fully_read_selected_files=['README.md'],
                     selected_sections_read=['src/backtest.py:cash/entry/PnL', 'src/basis_trade.py', 'src/data_fetcher.py'],
                     license_file_in_pinned_tree=False,
                     limitation='Fixed-notional returns, no complete collateral/liquidation wallet; rounded/forward-filled funding history not adopted.')],
        source_blob_receipts=ref(PARENT/'source_receipts.json'),
        rest403_preserved=ref(PARENT/'source_capture_initial.json'),
        source_financial_scripts_executed=False, external_engine_copied_to_product=False,
        source_account_replication=False, new_accepted_independent_alphas=0,
        youtube=dict(metadata=ref(HERE/'youtube_metadata.json'),
                     receipts=ref(HERE/'followup_source_receipts.json'),
                     author='Hummingbot', video_id='Ly8R5g3juxw',
                     actual_page_http_status=200, caption_http_status=200, caption_bytes=0,
                     full_video_watched=False, full_transcript_read=False,
                     rule_admitted=False,
                     scope='Cross-venue perpetual differential; distinct from this spot/perpetual baseline'),
        x_and_douyin=dict(previous_intake=ref(previous_social), explicitly_reused=True,
                          new_complete_original_rules_admitted=False,
                          new_posts_or_videos_claimed_read=False),
        primary_docs=[dict(url='https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History',
                           read_scope='Get Funding Rate History response fields: fundingTime, fundingRate, associated charge markPrice'),
                      dict(url='https://www.freqtrade.io/en/stable/leverage/',
                           read_scope='Spot/futures distinction and separate-account assumption')],
        local_study='Unconditional equal-base spot long/perpetual short with segregated native wallets; not source threshold-rule reproduction',
        market='crypto only; zero new equity factor_analysis, equity accounts or Vibe benches')
    save(HERE/'source_intake.json', source)
    # Add a referenced missing sidecar, preserving every byte in the old seal.
    sidecar = PARENT/'source_intake.json'
    if not sidecar.exists():
        save(sidecar, dict(recorded_at=now, added_after_v1_seal=True,
                           original_505_file_seal_unchanged=True,
                           authoritative_followup=ref(HERE/'source_intake.json'),
                           scope='Post-execution source disclosure, not a pre-result freeze'))
    save(HERE/'verification.json', dict(
        verified_at=now, passed=True, upstream_original_files=595,
        upstream_commit=plan['engine_commit'], extra_upstream_python_files=extra_py,
        frozen_reference_checks=len(checked), native_runner_hash_exact=True,
        preserved_v1_manifest=ref(PARENT/'artifact_manifest.json'), preserved_v1_paths=505,
        post_seal_v1_source_disclosure=ref(sidecar),
        full_parent_parity=ref(HERE/'full_parent_parity.json'),
        pre500_parity=ref(HERE/'pre500_parity.json'),
        actual_native_wallets=24, simulated_fills=48, wallet_hour_points=350208,
        old_successful_wallet_replays=17, new_distinct_completed_wallet_paths=7,
        new_independent_economic_samples=0,
        independent_auditor_imports_candidate_calculator=False,
        max_nav_error_usdt=results['maximum_independent_nav_error_usdt'],
        reservation_tests_distinct=9, actual_preflight_configs=24,
        all_frozen_numeric_carry_gates_passed=True,
        profitable_frozen_500plus500_portfolios=0,
        old_failure_preserved=True, original_parameters_unchanged=True,
        source_replication=False, independent_oos=False,
        orders_enabled=False, services_changed=False))
    rows = []
    for account in results['accounts']:
        p = account['periods']
        label = '双腿carry' if account['role'] == 'carry' else 'BTC/ETH现货持有'
        rows.append(f"|{label}|{account['capital']}|{account['scenario']}|{p[0]['net_return']*100:.5f}%|{p[0]['cagr_365day']*100:.5f}%|{p[1]['net_return']*100:.5f}%|{p[2]['net_return']*100:.5f}%|{p[0]['hourly_mark_drawdown']*100:.5f}%|")
    for portfolio in results['portfolios']:
        p = portfolio['periods']
        rows.append(f"|实际500carry+500现货|1000|{portfolio['scenario']}|{p[0]['net_return']*100:.5f}%|{p[0]['cagr_365day']*100:.5f}%|{p[1]['net_return']*100:.5f}%|{p[2]['net_return']*100:.5f}%|{p[0]['hourly_mark_drawdown']*100:.5f}%|")
    text = '''# 双腿carry原生账户与固定组合：carry小幅正收益，组合亏损

完成24个Freqtrade原生单腿钱包、8个完整资本/成本账户及2个实际500+500固定组合的独立对账。1000 USDT carry净收益2.38%/2.49%，365天年化约1.42%/1.49%；组合亏损8.98%/8.87%。没有找到更高收益的有效多策略组合。此批仅为加密资产，不提供港美股收益证据。

共同窗口2025-01-01 00:00至2026-08-31 23:00 UTC，14592小时；2026只是截至8月的切片，非全年。年界使用同一净值连接，未重启现金。BTC/ETH各半资金，每资产半数现货多头、半数1倍逐仓永续空头；四个隔离钱包无转账/借贷/现金利息。现货基准同资本/币池/窗口但方向和保证金合同不同，不能称等风险alpha比较。原生最后23:55强平为另记诊断，共同收益端点保留持仓并扣剩余退出成本。

conservative成本为现货20bps/永续15bps每侧；lower为13/8bps。包含现货10bps、永续5bps佣金加10或3bps滑点代理，通过原生quote fee实现；资金费另记，不认证真实盘口或佣金币种。小时回撤不代表盘中最坏损失。

|账户|初始USDT|成本情景|全窗净收益|365天CAGR|2025收益|2026截至8月收益|小时回撤|
|---|---:|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows) + '''

组合来自实际500carry四个钱包加实际500现货两个钱包的净值之和，既不缩放1000曲线，也不转账/动态再平衡。两档组合均失败，未更改权重、阈值或删去亏损切片。四个carry资本/成本账户满足原已冻结的正收益、年度切片、胜过同资本现货及零息现金、回撤≤10%、全窗等基础币对冲条件；这些数值条件不代表独立验证或生产晋级。

## 唯一技术变更与失败保管

原v1共17成功、1失败、6未启动；500BTC永续的0.001BTC实际名义93.6483虽然满足原始市场50最低成本，但低于原生含5%数量安全余量的98.330715请求门槛。原v1的fatal拒绝、预检缺陷及孤腿均保留，505条旧封存文件未改一字节。

v2通过本地freqtrade.data.carry_reservation在123.75预算内预留98.330715，并要求原生数量转换仍严格产生预定0.001BTC。原始市场最低金额、原生最低请求额、实际数量、信号/00:05成交时钟、资本、费用、风险控制和原始共同窗口均未改变。独立审计再次核对预算含费用及请求位于实际数量格区间。不是降低最低仓位或追加资金。

在任何新增500钱包运行前，12条1000原生控制完整成交/费/资金费/小时NAV逐项完全相同；最终所有17条旧成功路径完全复现。这17条是回放控制，不是17份新经济样本。v2实际24钱包、48笔模拟成交、350208个钱包小时点，独立NAV重建最大差1.14e-13 USDT；另7条为原先未成功完整路径，不是独立样本外。9项新预留量检查及24配置真实原生预检通过，原v1的14项检查归其自身版本，不重复计数。

上游固定Freqtrade c051e9065c5b677f03817dd1f83cb980af1ed684的595个原始文件当前哈希一致、无额外上游Python。实际链为Configuration→StrategyResolver/IStrategy→Backtesting.start→Trade/Order/Wallet→实际ZIP。共同数量、固定规格、隔离腿策略和预留量适配器均为本地扩展，不是上游官方双市场共享抵押引擎，也不是Vibe SignalEngine/bench。新factor_analysis=0、Vibe bench=0。后置verification/manifest是结果封存；真正事前的producer/config/protocol哈希在plan及preflight中，不把后置清单称事前冻结。

## 收益来源与限制

1000 conservative账户：共同端点之前的原生资金费26.39906843 USDT，基差损益0.06195，入场成本1.48555663，端点剩余退出成本1.16358609，净增23.81187571。收益主要来自所提供的历史资金费，不是预测下一次资金费或价格。固定BTC/ETH来源池与旧缺资金费的广泛币池不同，旧TRB/TIA等缺口没有被认证。

固定市场规格是历史研究情景，未认证全期真实规则。现货OPEN/永续MARK OPEN、同一5分钟开盘两腿成交、历史资金费归属、无现金利息及独立钱包均为显式模型假设。单腿串行执行对固定数量、无转账路径成立，不能推广成动态组合或共享保证金清算。尚无真实订单、交易所双腿同步/私有资金费对账、纸盘、独立样本外或生产证明。

## 来源学习范围

[idrees固定源码](https://github.com/idreesm18/crypto-perp-funding-carry/tree/06d05d08a88c107dc84b69dc21009864dbd9b878)及[zwmjj固定源码](https://github.com/zwmjj/funding-rate-arb/tree/b06dc3ac3453b08edfb430b527a1e0a408d42a63)的选定文件已按source_intake记录范围阅读；两固定树无LICENSE，未执行或移植其资金引擎。idrees同次结算费率用于同次权重/收益的历史时钟、zwmjj固定名义分母及保证金缺失均保留。本研究是独立实现的无条件等数量基准，未复现作者阈值或借用作者收益。

[Hummingbot官方YouTube](https://www.youtube.com/watch?v=Ly8R5g3juxw)真实取得作者/描述/章节元数据，主题为跨交易所永续资金费差；字幕请求200但0字节，未声称观看或读完转录。X/抖音复用既有准入状态，没有新增完整规则或收益证据。来源关注不能代替可执行规则与完整账户。

所有本轮文件/完整ZIP及单腿审计保留。原v1 README引用缺失的来源说明已以新后置sidecar补充，旧505项manifest不变；该新增说明由本版本verification绑定。未下单、部署、改运行服务、提交或推送。整体寻找更高收益与有效多策略组合的目标仍未达到；也没有完成港美股/Freqtrade原生执行合同的迁移。

证据：[结果](results.json)、[冻结协议](protocol.json)、[原生执行完成](execution_complete.json)、[17条完整控制](full_parent_parity.json)、[独立核对](verification.json)、[来源准入](source_intake.json)、[最终文件清单](artifact_manifest.json)。
'''
    (HERE/'README.md').write_text(text)
    save(HERE/'goal_audit.json', dict(
        objective_achieved=False, status='PROGRESS',
        crypto_evidence_complete_for_this_contract=True,
        carry_numeric_gate=True, profitable_multi_strategy_portfolio=False,
        independent_new_alpha=False, independent_oos=False,
        higher_return_goal_satisfied=False,
        current_user_market='HK/US equities; not satisfied by this crypto result',
        saved_goal_market='crypto', clarification_pending=True,
        factor_analysis_calls=0, vibe_benches=0, native_wallets=24,
        no_remaining_financial_child=True, active_process_json_is_historical=True,
        orders_enabled=False, deployment_changed=False))
    files = sorted(set(p for root in [HERE, out] for p in root.rglob('*')
                       if p.is_file() and '__pycache__' not in p.parts
                       and p != HERE/'artifact_manifest.json'))
    files += [Path(plan['reservation_source']['path']), sidecar]
    save(HERE/'artifact_manifest.json', dict(
        scope='Post-execution v2 complete native carry and failed static mixtures; not a pre-result freeze',
        files=[dict(**ref(p), bytes=p.stat().st_size) for p in sorted(set(files))]))
    print('SEALED', len(set(files)), 'paths; old505unchanged; 24nativewallets; 17exactcontrols')


if __name__ == '__main__':
    main()
