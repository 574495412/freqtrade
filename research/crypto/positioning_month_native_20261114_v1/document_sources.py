"""Record source and failure boundaries; do not adopt gross event returns."""
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent;out=h.parents[2]/'user_data/crypto_quant/positioning_month_native_20261114_v1'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(n,x):(h/n).write_text(json.dumps(x,indent=2,ensure_ascii=False)+'\n')
receipts=json.loads((out/'raw_receipts.json').read_text())
save('source_intake.json',{'source_article':{'url':'https://tradewize.io/blog/how-to-read-the-long-short-ratio',
 'author':'Pavel Penev, TradeWize','method_and_limit_sections_read':True,'source_scope':'BTC/ETH/SOL; last5minute snapshot; trailing365days/bottom10%/future30day gross event outcomes',
 'not_a_funded_strategy':'source explicitly says no fees/funding/slippage and no attempt to trade it',
 'local_contract':'current excluded365prior-days midrank; threshold<=.10; next-dayD00signal/D01nativefill;30dayopportunityunion/no pyramiding;fixed1/3coin slots',
 'not_source_performance_reproduction':True},
 'github_example':{'repo':'Coinversaa/coinversaa-freqtrade-example','commit':'c6d9845330bf98ea9ba414a968faadbd9804735f',
 'read_scope':'strategy core and helper/source current-signal path; README backtesting and source input sections; source client captured not claimed fully read',
 'rejected_as_historical_strategy':True,'reason':'live cohort/whale APIs in vectorized indicator function; no admitted historical asof cohort membership/positions; Binance ratios are not a replacement for Hyperliquid PnL cohorts',
 'documentation_code_difference':'whale short notional computed but not referenced by actual three entry conditions; do not count claimed whale filter as implemented',
 'api_client_executed':False,'keys_read_or_used':False,'paper_workers_started':False},
 'official_data':{'source':'Binance public archive with official SHA256; current REST definitions read through web tool',
 'direct_document_status':'HTTP202empty, not a successful saved documentation body','field_distinction':'all-account headcount, top-account headcount, top-position size and taker buy/sell ratio kept separate; no daily ratio averaging or cross-venue aggregate',
 'publication':'archive checksums do not certify original release/first-seen; create_time+5minutes is local availability proxy'},
 'counterevidence':{'url':'https://janorium.com/articles/long-short-ratio-open-interest-backtest','read_scope':'setup, scan and funded-fade sections',
 'lesson':'event scan can have retrospective thresholds and overlapping observations; its90day/7day funded rule differs from the source365day/30day observation',
 'seen_after_local_protocol_written':True,'no_parameter_change':True,'published_results_not_adopted_as_own':True},
 'youtube':{'url':'https://www.youtube.com/watch?v=qrBeuJm0fD4','author':'Bitcoin Trading Challenge',
 'title':'Binance Long/Short Ratio! (Best Indicator of all time?)','metadata_and_description_read':True,
 'caption_request':next((x.get('caption')for x in json.loads((h/'social_receipts.json').read_text())if 'youtube'in x['url']),None),
 'video_watched':False,'full_transcript_read':False,'complete_trade_rules_or_returns_adopted':False},
 'x':{'url':'https://x.com/RuujSs/status/2074503360208884204','reused_regime_method_lead':True,
 'not_new_positioning_strategy':True,'targeted_ratio_search_did_not_supply_complete_verified_new_X_rule':True,
 'no_performance_adopted':True},
 'douyin':{'url':'https://www.douyin.com/video/7603647454390603035','rechecked_prior_lead':True,
 'direct_response':'HTTP2004XXriskblock','video_watched':False,'complete_rules_read':False,'returns_adopted':False},
 'native_owning_implementation':'Freqtrade pinned c051e9065c5b677f03817dd1f83cb980af1ed684; original595files; local positioning loader plus nativeIStrategycallbacks',
 'new_registry_definitions':0,'factor_analysis_calls':0,'source_or_external_account_engine_executed':False})
save('download_reconciliation.json',{'official_verified_archives':len(receipts),'reused_old_verified_BTC_archives':sum(r.get('reused_prior_verified_archive',False)for r in receipts),
 'recovered_orphans_with_independent_checksum_verification':sum(r.get('recovered_verified_orphan',False)for r in receipts),
 'unresolved_failures':json.loads((out/'download_failures.json').read_text()),
 'new_request_failures_resolved':sum(len(r.get('request_failures',[]))for r in receipts),
 'initial_collector_failure_record':str(h/'download_control_failure.json'),'initial_process_terminal_exit':143,
 'initial_exception_trace_not_obtained':'original executor waited for its queued jobs before surfacing first exception; it was stopped for the verified collector defect, not an observation timeout',
 'old_source_bytes_and_success_receipts_preserved':True,'financial_accounts_started_during_download_repair':0})
print('Recorded source limits and verified input recovery')
