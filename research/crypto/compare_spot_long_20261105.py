"""Build a scope-limited comparison from four completed audited spot wallets."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ACCOUNTS=ROOT/"user_data/crypto_quant/spot_baseline_long_run_20261105_v001"
BENCHMARK=ROOT/"user_data/crypto_quant/spot_btc_hold_long_20261105_v001"
OUT=ROOT/"user_data/crypto_quant/spot_baseline_long_analysis_20261105_v001"

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_text())

def main():
    plan=load(ACCOUNTS/"registered_plan.json")
    if not (ACCOUNTS/"completion.json").is_file() or not (ACCOUNTS/"additional-audits-complete.json").is_file():
        raise ValueError("Not all accounts and audits complete")
    rows=[]
    for candidate in plan["candidates"]:
        name=candidate["name"]
        for cost in plan["total_cost_bps_per_side"]:
            d=ACCOUNTS/f"{name}-cost{cost}"
            r=load(d/"result.json"); a=load(d/"independent-account-audit.json"); v=load(d/"independent-5m-order-equity-v3.json"); event=load(d/"extreme-event-realized-attribution-v2.json"); fills=load(d/"fill-candle-evidence-v2.json"); op=load(d/"open-fill-statistics.json")
            if a["plan_sha256"]!=sha(ACCOUNTS/"registered_plan.json") or a["result_sha256"]!=sha(d/"result.json") or a["curve_sha256"]!=sha(d/"hourly_equity.parquet") or v["account_audit_sha256"]!=sha(d/"independent-account-audit.json") or v["saved_5m_reference_sha256"]!=sha(d/"independent-5m-order-equity-v3.parquet") or v["native_archive_sha256"]!=a["archive_sha256"] or event["native_archive_sha256"]!=a["archive_sha256"] or fills["native_archive_sha256"]!=a["archive_sha256"]:
                raise ValueError(f"Independent lineage mismatch: {name}/{cost}")
            if op["input_sha256"]!=sha(d/"fill-candle-evidence-v2.parquet") or op["native_archive_sha256"]!=a["archive_sha256"]:
                raise ValueError("Open-fill evidence mismatch")
            periods={p["name"]:p for p in r["periods"]}; five={p["period"]:p for p in v["intervals"]}
            row={"candidate":name,"cost_bps_per_side":cost,"continuous_return":periods["continuous"]["net_return"],
                 "hourly_open_drawdown":periods["continuous"]["hourly_mark_drawdown"],
                 "five_minute_open_drawdown":five["continuous"]["max_5m_open_reference_drawdown"],
                 "five_minute_peak_gross_to_equity":five["continuous"]["max_5m_open_gross_to_equity"],
                 "minimum_total_cash_reference_usdt":five["continuous"]["minimum_total_cash_reference_usdt"],
                 "completed_campaigns":periods["continuous"]["completed_campaigns"],
                 "ending_open_campaigns":periods["continuous"]["open_campaigns_at_end"],
                 "return_2025":periods["calendar-2025"]["net_return"],
                 "return_2026_through_august":periods["calendar-2026-through-august"]["net_return"],
                 "event_20251010_20_24_trade_count":event["completed_trades"],
                 "event_20251010_20_24_pnl_usdt":event["campaign_pnl_usdt"],
                 "event_pnl_fraction_of_total_nav_change":event["fraction_of_full_period_nav_change"],
                 "orders_in_5m_bars_range_over_20pct":fills["bars_range_over_20pct"],
                 "native_fills_including_terminal_forced_exit":op["fills"],"fills_at_bar_open":op["fills_at_open_with_float_tolerance"],
                 "account_audit":str(d/"independent-account-audit.json"),"five_minute_audit":str(d/"independent-5m-order-equity-v3.json")}
            delay=d/"extreme-event-5m-delay-fixed-quantity-sensitivity-v2.json"
            if delay.exists():
                sensitivity=load(delay)
                if sensitivity["native_archive_sha256"]!=a["archive_sha256"]: raise ValueError("Sensitivity source mismatch")
                row["posthoc_delay_fixed_quantity_event_pnl_usdt"]=sensitivity["fixed_quantity_repriced_pnl_usdt"]
            rows.append(row)
    btc=load(BENCHMARK/"result.json")
    if btc["registration_sha256"]!=sha(BENCHMARK/"registration.json") or load(BENCHMARK/"registration.json")["strategy_plan_sha256"]!=sha(ACCOUNTS/"registered_plan.json"):
        raise ValueError("BTC reference registration mismatch")
    btc5=load(BENCHMARK/"result-5m-reference.json")
    if btc5["registration_sha256"]!=sha(BENCHMARK/"registration.json"):
        raise ValueError("BTC 5m registration mismatch")
    outcomes=[]
    names=[c["name"] for c in plan["candidates"]]
    for cost in plan["total_cost_bps_per_side"]:
        subset=[r for r in rows if r["cost_bps_per_side"]==cost]
        max_return=max(r["continuous_return"] for r in subset)
        min_dd=min(r["five_minute_open_drawdown"] for r in subset)
        outcomes.append({"cost_bps_per_side":cost,"highest_continuous_return_candidates":[r["candidate"] for r in subset if r["continuous_return"]==max_return],
                         "lowest_5m_reference_drawdown_candidates":[r["candidate"] for r in subset if r["five_minute_open_drawdown"]==min_dd]})
    dominance=[]
    for left in names:
        for right in names:
            if left==right: continue
            L={r["cost_bps_per_side"]:r for r in rows if r["candidate"]==left}
            R={r["cost_bps_per_side"]:r for r in rows if r["candidate"]==right}
            per_cost=[]
            for c in L:
                better_returns=all(L[c][k]>=R[c][k] for k in ("continuous_return","return_2025","return_2026_through_august"))
                better_risk=L[c]["five_minute_open_drawdown"]<=R[c]["five_minute_open_drawdown"]
                per_cost.append(better_returns and better_risk)
            if all(per_cost): dominance.append({"candidate":left,"weakly_dominates":right,"criterion":"return >= in each of continuous and two calendar periods and 5m open max drawdown <= under both fee proxies; equality permitted"})
    report={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"registered_plan_sha256":sha(ACCOUNTS/"registered_plan.json"),
            "account_rows":rows,"costwise_screens":outcomes,"scope_limited_weak_dominance":dominance,"btc_buy_hold_reference_hourly":btc["rows"],"btc_buy_hold_reference_5m":btc5["rows"],
            "limitations":["Only two candidates and 36 registered spot pairs; no global best claim", "No prespecified user risk preference, capital allocation, acceptable drawdown or live execution contract", "Historical window predates source publication and was not live forward", "5m OPEN drawdown is not intrabar worst", "Cash figure is total ledger cash before subtracting pending order reserves", "Fixed fee cost proxies and bar-touch fills do not certify liquidity or queue position", "Flash crash attribution and fixed quantity repricing are descriptive post-hoc calculations, not rerun counterfactuals", "BTC reference uses fractional quantity, no historical exchange filters or executable depth"]}
    OUT.mkdir(parents=True,exist_ok=False)
    (OUT/"comparison.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"rows":len(rows),"screens":outcomes,"dominance":dominance,"output":str(OUT/"comparison.json")},indent=2))

if __name__=="__main__": main()
