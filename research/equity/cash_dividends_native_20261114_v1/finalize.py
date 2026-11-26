"""Record versioned native extension changes and bounded source/input status."""
import hashlib,json
from datetime import UTC,datetime
from pathlib import Path
h=Path(__file__).resolve().parent;root=h.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(name,obj):(h/name).write_text(json.dumps(obj,indent=2)+'\n')
modules=['freqtrade/data/equity_dividends.py','freqtrade/equity_dividend_wallets.py',
 'freqtrade/equity_wallets.py','freqtrade/equity_fee_wallets.py','freqtrade/optimize/equity_nav.py']
write('implementation.json',{str(root/p):sha(root/p)for p in modules})
write('protocol.json',{'recorded_utc':datetime.now(UTC).isoformat(),
 'scope':'HK/US only: same-currency cash dividend native component plus dividend-seasonality source/input admission',
 'original_universe':{'US':503,'HK':11},'account_window':['2025-01-01','2026-08-31'],
 'currency':'separate USD and HKD; no FX or curve scaling',
 'native_commit':'c051e9065c5b677f03817dd1f83cb980af1ed684',
 'native_owner':'Configuration, IStrategy, DataProvider, Backtesting, Trade/Order, Wallets and native results; full equity lifecycle remains incomplete',
 'local_extensions':modules,'component_test_contract':'fictional ABC, DPS10, withholding10%, synthetic dated fees/calendars; not investor tax or historical broker validation',
 'account_admission':'not enabled; no historical strategy return claimed',
 'research_gates_inherited':{'IC_gt':.03,'IR_gt':.5,'positive_ratio_gt':.55,'minimum_months':24,'dedup_mean_abs_pearson_gt':.99},
 'frozen_economic_candidate_parameters':None,'financial_results_observed':False,
 'dividend_method':'actual native fills strictly before explicit ex-open; net claim accrual; supplied broker credit clock; cumulative settled cash only after payment; fee-exclusive native trade statistics remain separate',
 'source_candidate':'OpenSourceAP DivSeason; source code read, original distribution inputs unavailable in audited514CSVs; no proxy',
 'parent_receipt':str(h.parent/'net_nav_native_20261114_v1/verification.json'),
 'parent_receipt_sha256':sha(h.parent/'net_nav_native_20261114_v1/verification.json'),
 'historical_accounts':0,'combination_accounts':0,'bench_records':0,'factor_analysis_calls':0,
 'accepted_new_factors':0,'performance':None,'deployment':False,'orders':False,'services_changed':False})
write('technical_amendment.json',{'scope':'default-zero hooks in existing LOCAL equity components; no upstream native file edits',
 'reason':'paid dividends must be present before settled-cash and dated-fee validation; unpaid net claims must enter NAV once',
 'prior_bytes_preserved':{str(p):sha(p)for p in(h/'owner_before').glob('*.py')},
 'current_bytes':{str(root/p):sha(root/p)for p in modules},
 'old_reports_and_receipts_unchanged':True,'old_hash_receipts_do_not_certify_current_revised_local_modules':True,
 'no_economic_parameter_rescue':True,'current_regressions':197,'new_dividend_checks':40,'old_component_checks':157})
write('source_intake.json',{'github':{'repo':'OpenSourceAP/CrossSection','commit':'9f053398c4995651d822e90d81526d0644c6f3e5','license':'GPL-2.0',
 'files_read':['DivSeason.py','save_standardized.py'],'helpers_read':'asrol wrapper and stata_multi_lag wrapper; not every helper function',
 'source_strategy_engine_executed':False,'source_factor_executed':False,
 'rule':'regular cash distributions cd1=1,cd2=2; monthly aggregation by frequency; first sorted code if ambiguous; forward-fill frequency only on source master; monthly code2 excluded, retain codes<6; current source month paid sum>0 permits zero; prediction uses calendar lags2/5/8/11 for codes0/1/3,5/11 for code4,11 for code5; output month labels not shifted by save function',
 'timing':'source month t state predicts next holding month; never relabel lags as same-month3/6/9/12 or trade using a not-yet-completed month',
 'missingness':'source zero fill assumes complete distributions/master input; absence of local input is not proof of zero dividend'},
 'x':{'url':'https://x.com/TheIdeaFarm/status/2048024226947649871?s=20','post_body_read':True,
 'direct_http_status':200,'web_status':403,'evidence':'behavioral dividend-demand discussion, no complete implementable account rules or local return evidence'},
 'youtube':{'url':'https://www.youtube.com/watch?v=PXaE8Bo06Ps','author':'The Meb Faber Show',
 'metadata_and_author_show_notes_read':True,'caption_http_status':200,'caption_bytes':0,'video_watched':False,'full_transcript_read':False,
 'show_notes':'https://www.themebfabershow.com/episodes/E2rpGXk3LrY','adoption':'source lead only; no performance imported'},
 'douyin':{'url':'https://www.douyin.com/video/7562173571624127803','direct_http_status':200,'actual_body':'JavaScript challenge;0visible non-script text lines',
 'search_index_summary_only':True,'full_video_rules_read':False,'performance_adopted':False},
 'native_source':'Freqtrade pinned backtesting docs; current LEAN docs are an external comparison, not native implementation',
 'lean_difference':'LEAN documents raw/split-adjusted backtest dividend cashbook credit on ex-date; this local contract keeps an unpaid claim until supplied payment cash availability',
 'sec_source':'web tool read current Investor.gov ordinary/special dividend rules; direct downloaded response403 preserved, not claimed as successful local document',
 'new_registry':0,'factor_analysis':0,'financial_accounts':0})
write('failure_record.json',{'retained':[
 {'stage':'source helper capture','error':'bs4 missing in private interpreter','resolution':'used Python standard-library HTMLParser; no package install'},
 {'stage':'source requests','error':'direct Investor.gov403 and guessed host episode403','resolution':'retained failed bytes; actual author-linked episode resolved and read with web/native requests'},
 {'stage':'video source','error':'web throttled; timedtext200with0bytes','resolution':'metadata/show notes only; no transcript claim'},
 {'stage':'lint','error':'5initial line length/complexity findings','resolution':'line wrapping and extraction of dividend NAV validation; no economic changes'}],
 'first34checks_passed':True,'financial_failures_count':0,'historical_runs_attempted':0})
print('Recorded current component, source and input boundaries')
