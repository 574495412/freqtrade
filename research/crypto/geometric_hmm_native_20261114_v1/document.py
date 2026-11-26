"""Source boundaries and report derived from inspected actual native results."""
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(name,value):(h/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
r=json.loads((h/'results.json').read_text());v=json.loads((h/'native_validation.json').read_text())
old=h.parent/'residual_momentum_source_native_20261114_v1/source_intake.json'
save('source_intake.json',{'main':{'repo':'benedictprimmer-web/regime_ensemble','commit':'da7f8c47eb682b6f103a4021664c48d934413be2',
 'scope_read':'README; geometric/markov/ensemble/expanding source methods; source backtest execution lag; source financial engine never executed',
 'licence':'LICENSE request404; no license claimed','original_market':'SPY; source price-return report is not crypto replication',
 'local_extension':'BTC native rolling statistical features, native quantity callbacks and actual wallets; not upstream Freqtrade HMM feature'},
 'private_source_rejected':{'path':str(h.parent/'walkforward_ensemble_native_20261114_v1/source_manifest.json'),
 'sha256':sha(h.parent/'walkforward_ensemble_native_20261114_v1/source_manifest.json'),'reason':'exact24configs/parameters/tiebreak private;0publicPythonstrategyfiles;no guessed reproduction'},
 'x':{'url':'https://x.com/RuujSs/status/2074503360208884204','direct_status':200,'web_status':403,
 'actual_displayed_author':'Ruuj @RuOnChain1','read_scope':'intro and selected HMM fitting/decoding/BIC/restart passages, not whole duplicated1988line export',
 'lesson':'separate historical decoding and causal filtering; restart train likelihood is not a portfolio return gate',
 'no_full_strategy_or_performance_adopted':True},
 'youtube':{'url':'https://www.youtube.com/watch?v=NQpAqaJrm7U','author':'Algovibes',
 'title':'I Built the Viral Claude Code Trading Strategy Properly — Watch What Happens',
 'description_and_chapters_read':True,'caption_http_status':200,'caption_bytes':0,'video_watched':False,'full_transcript_read':False,
 'membership_notebook_not_obtained':True,'not_main_source_strategy':True,'advertised_results_not_adopted':True},
 'douyin':{'url':'https://www.douyin.com/video/7603647454390603035','prior_lead_rechecked':True,'direct_status':200,
 'actual_body':'4XX risk block; not video rules','targeted_HMM_search_no_complete_verified_rule':True,'video_watched':False,'strategy_or_returns_adopted':False},
 'prior_social_receipt_binding':{'path':str(old),'sha256':sha(old),'not_new_watching':True},
 'owning_financial_engine':'pinned Freqtrade c051e9065c5b677f03817dd1f83cb980af1ed684 original595files',
 'financial_accounts':16,'new_cross_sectional_factor_definitions':0,'factor_analysis_calls':0,
 'source_learning_is_not_performance_evidence':True})
save('failure_record.json',{'preserved':[
 {'stage':'first model preflight','error':'threadpoolctl imported before activating private dependency paths','artifact':'preflight_initial.log','resolution':'activate source dependency paths before importing; first attempt had no model/account output'},
 {'stage':'cache fixture checks','error':'raw Feather timestamp representation differs from actual native history loader cache identity;3fixtures failed before intended tamper','artifact':'tests_cache_fixture_failure.log','resolution':'use actual native load_pair_history; no model/strategy/economic rule changed'},
 {'stage':'initial financial registration','error':'registration did not require successful test log','artifact':'plan_failed_test_gate.json','resolution':'preserve initial plan; added explicit12passed gate; froze replacement before any financial worker'},
 {'stage':'callback-error guard','error':'initial handler only caughtERROR; native ValueError isWARNING','resolution':'before financial registration addedWARNINGcapture and tested actual suppressed ValueError/RuntimeError'},
 {'stage':'source access','error':'pinnedGitHubwebURLrestricted; Xweb403; YouTubewebthrottled; captions200empty;Douyin4XX','resolution':'saved directsourcebytes/statuses; no viewing/fullrules or claimedreturns inferred'}],
 'financial_worker_failures':0,'economic_gate_failed':True,'no_sign_period_pool_cost_weight_parameter_rescue':True})
rows=[]
labels={'SourceGeoHmmBTC':'联合信号','GeoOnlyBTC':'几何信号','HmmOnlyBTC':'HMM信号','ConstantGrossBTC':'恒定敞口目标'}
for strategy,label in labels.items():
 cases=[a for a in r['accounts']if a['strategy']==strategy and a['capital']==1000]
 a,b=cases;f=a['periods'][0]
 rows.append(f"|{label}|{f['net_return']*100:.2f}%|{b['periods'][0]['net_return']*100:.2f}%|{f['maximum_hourly_mark_drawdown']*100:.2f}%|{a['periods'][1]['net_return']*100:.2f}%|{a['periods'][2]['net_return']*100:.2f}%|{f['natural_completed_campaigns']}|")
for name,label in [('separate_components','几何500＋HMM500'),('source_plus_constant','联合500＋恒定500')]:
 a,b=[c for c in r['combinations']if c['name']==name];f=a['periods'][0]
 rows.append(f"|{label}|{f['net_return']*100:.2f}%|{b['periods'][0]['net_return']*100:.2f}%|{f['maximum_hourly_mark_drawdown']*100:.2f}%|{a['periods'][1]['net_return']*100:.2f}%|{a['periods'][2]['net_return']*100:.2f}%|{f['natural_completed_campaigns']}|")
(h/'results_table.md').write_text('\n'.join(rows)+'\n')
print('Documented source/economic failures and complete results table')
