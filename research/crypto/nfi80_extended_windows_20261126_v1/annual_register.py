"""Register the fully covered 2025 accounts against the native Freqtrade runner."""

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.holdout_accounts import invariant_config
from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt
from research.crypto.nfi80_historical_universe_20261109.v003.native_runner import verify

from .accounts import CAP_ROLE, COSTS, frozen_sources, write_once
from .annual_archives import DEST


def main(destination=DEST):
    raw_plan_path = destination / "raw_plan.json"
    normalization_path = destination / "normalization.json"
    data_receipt = receipt(destination / "normalized-receipts.json")
    raw = read(raw_plan_path)
    normalization = read(normalization_path)
    assert normalization["raw_plan"] == receipt(raw_plan_path)
    assert normalization["native_inputs_admitted"] is True
    checked(normalization["boundary_evidence"])
    checked(normalization["normalizer"])
    schedule = read(checked(raw["schedule"]))
    union = schedule["union"]
    assert sorted(normalization["records"]) == union and normalization["normalized_pairs"] == len(union)
    files = read(checked(data_receipt))
    assert len(files) == 8 * len(union)
    policy = read(checked(raw["protocol"]))
    start, stop = map(pd.Timestamp, policy["window"])
    last = stop - pd.Timedelta(minutes=5)
    rules = read(checked(raw["market_snapshot"]))
    registrations = destination / "registrations"
    registrations.mkdir(exist_ok=True)
    market = write_once(registrations / "markets.json", {
        **rules, "markets": {pair: rules["markets"][pair] for pair in union},
        "leverage_tiers": {pair: rules["leverage_tiers"][pair] for pair in union},
    })
    datadir = destination / "candles"
    marks = {}
    for pair in union:
        name = pair.replace("/", "_").replace(":", "_") + "-1h-mark.feather"
        key = f"futures/{name}"
        marks[pair] = {"path": str(datadir / key), "sha256": files[key]}
    common = {
        "start": start.isoformat(), "end": stop.isoformat(), "comparison_end": last.isoformat(),
        "periods": [{"name": "continuous", "start": start.isoformat(), "end": last.isoformat()}],
        "pairs": union, "datadir": str(datadir), "historical_schedule": raw["schedule"],
        "market_snapshot": market, "data_receipts": data_receipt, "marks": marks,
        "protocol_receipt": raw["protocol"], "total_cost_bps_per_side": list(COSTS),
        "annual_admission": receipt(normalization_path),
        "comparison_purpose": f"Full {start.year} matched native account, previously empty wallet",
        "orders_enabled": False, "production_eligible": False,
    }
    result = {}
    baseline_config = None
    for role, source in frozen_sources().items():
        if role == CAP_ROLE:
            continue
        plan = read(source)
        candidate, = plan["candidates"]
        config = read(checked(candidate["config"]))
        config["exchange"]["pair_whitelist"] = union
        config["pairlists"] = [{"method": "HistoricalVolumePairList",
                                "schedule": raw["schedule"]["path"], "sha256": raw["schedule"]["sha256"]}]
        config["bot_name"] = f"nfi80-annual-{start.year}-matched-accounts"
        if baseline_config is None:
            baseline_config = invariant_config(config)
        assert invariant_config(config) == baseline_config
        config_source = write_once(registrations / f"{role}-config.json", config)
        annual_candidate = {**candidate, "name": role, "config": config_source}
        annual_plan = {**plan, **common, "candidates": [annual_candidate], "discovery_parent": receipt(source)}
        verify(annual_plan, annual_candidate)
        result[role] = write_once(registrations / f"{role}-plan.json", annual_plan)
        print("verified annual native plan", role, flush=True)
    write_once(registrations / "registry.json", {
        "accounts": result, "costs_per_side_bp": list(COSTS), "annual_input": receipt(normalization_path),
        "scope_deferred": {"short_rebuy_cap_12": "Pending completed 8bp discovery-window comparison"},
        "native_execution_started": False,
    })


if __name__ == "__main__":
    main()
