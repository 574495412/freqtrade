"""Freeze interface preparation before object fixtures; no financial execution."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


HERE = Path(__file__).absolute().parent
ROOT = HERE.parents[2]
PRIOR = HERE.parent / "nfi40_idle_capacity_native_20261117_v1"
VENDOR = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")
NFI = HERE.parent / "strategy_comparison_20261105/sources/nfi-x8-1x/NostalgiaForInfinityX8.py"
CONFIG = (
    ROOT / "user_data/crypto_quant/wide_strategy_cost15_20261105_v001/nfi-x8-1x-cost15/config.json"
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ref(path):
    return {"path": str(path), "sha256": sha(path)}


def main():
    assert not (HERE / "freeze.json").exists()
    prior = json.loads((PRIOR / "protocol.json").read_text())
    callbacks = [
        "version",
        "informative_pairs",
        "populate_indicators",
        "populate_entry_trend",
        "populate_exit_trend",
        "custom_exit",
        "custom_stake_amount",
        "order_filled",
        "adjust_trade_position",
        "confirm_trade_entry",
        "confirm_trade_exit",
        "check_entry_timeout",
        "check_exit_timeout",
        "bot_loop_start",
        "leverage",
        "custom_entry_price",
        "custom_exit_price",
        "adjust_order_price",
        "adjust_entry_price",
        "adjust_exit_price",
        "custom_stoploss",
        "bot_start",
        "ft_bot_start",
        "ft_advise_signals",
        "advise_entry",
        "advise_exit",
    ]
    protocol = {
        "registered_at": datetime.now(UTC).isoformat(),
        "stage": "IStrategy interface preparation, not financial admission",
        "prior_policy": ref(PRIOR / "protocol.json"),
        "official_workflow_addendum": ref(PRIOR / "official_workflow_addendum_20261118.json"),
        "prior_policy_parameters_changed": False,
        "information_disclosure": {
            "parent_reported_first_NFI500_cost15_result_before_this_interface_freeze": True,
            "NFI500_financial_artifacts_read_by_this_stage": False,
            "HS_financial_outcomes_available": False,
            "new_result_used_to_change_weights_conditions_clocks": False,
        },
        "vendor_root": str(VENDOR),
        "vendor_manifest": ref(VENDOR.parent / "FREQTRADE_SOURCE.json"),
        "NFI_source": ref(NFI),
        "NFI_version": "v18.0.146",
        "reference_config": ref(CONFIG),
        "ordered_pairs": prior["contract"]["ordered_pairs"],
        "unchanged_callbacks": callbacks,
        "implemented": {
            "NFIIdleCapacityDisabled": "Native IStrategy subclass of exact pinned NFI, only __init__ admission guard added; all26 callbacks inherited as original function objects. auxiliary must explicitly beFalse; only backtest/dry_run config; no available_capital.",
            "CompletedIntentCalendar": "Preparation-only read-only date/flags/tag calendar, not wired into disabled wrapper. Capture original NFI complete entry+exit outputs; exact T-5m lookup for all40, conflict rules equal nativecheck_for_trade_entry; missing row/pair rejects. No OHLC, acceptance, wallet or return data is stored.",
            "enabled_route": "NOT_IMPLEMENTED_HARD_REJECTED",
            "NFI_full_account_parity": "UNKNOWN_UNTIL_FUTURE_NATIVE_REPLAY",
        },
        "clock_owner": {
            "unshifted_DP_cache": "Backtesting._get_ohlcv_as_lists caches source signals, then shifts signals and tags one row and drops first post-trim row",
            "bot_loop": "time_pair_generator invokes bot_loop_start before updating each pair DP max_index, so DP global view may be stale/empty. Never treat its last row as current all40 intent.",
            "supported_clock_solution": "Future enabled wrapper captures original signals through supported populate callbacks; after original entry AND exit logic, store immutable raw-dated intent calendar. AtT read exactly rowT-5m; future rowT excluded. Does not change DP pointers or native execution order.",
            "HS_projection": "Completed source1h confirmation endsT; put single auxiliary intent on5m rowT-5m, then native shift toT. Original1hmarket H&S is not execution-equivalent.",
            "first_boundary": "Keep original NFI timerange start2025-01-01T00 and startup800. Native first post-shift execution00:05; H&S event at00:00 is retained as unexecutable initial-row boundary, not silently moved to00:05 or enabled by an earlier native_start.",
            "late_entry": "Reject T+5m and later; existing pending entry is checked/canceled before any later fill attempt.",
        },
        "future_routing": {
            "initial_entry": "Exact numeric/space NFI tags pass unchanged to original custom_stake/leverage/confirm/custom_entry_price; hs1 tags use separate event rules; unknown tag rejects. Trade-bound callbacks route only by originalTrade.enter_tag, never byDCA order tags.",
            "NFI_callbacks": "All original callbacks and custom-data side effects preserved; do not filter auxiliary trades out of NFI global trade queries. Their resulting interactions are retained.",
            "aux_admission": "No NFI open or pending campaign, no aux campaign/pending, no direction-valid currentNFI intent anywhere in ordered40, max1 auxiliary; simultaneous events originalpairorder. Recheck actual state in confirm_trade_entry.",
            "aux_budget": "Use same native wallet B=total stake,F=available; S=max(0,min(B/6,(F-5B/6)/(1+2f))). In confirm after native amount/minimum adjustment require amount*rate/leverage<=B/6 and margin*(1+2f)<=F-5B/6; no credit for requested exits.",
            "aux_price": "Global5m/LIMIT preserved. Auxiliary source entry and source/preemption exit price uses native proposed5mopen, then unchanged native precision/clamp/fill semantics. No currenthigh/low signals.",
            "position_adjustment": "Globally stays enabled for NFI; auxiliary returnsNone and never invokes NFI adjustment/customdata callbacks. No auxiliary DCA or partial exit.",
            "aux_exit": "Read only sourcehour closes date+1h<=T, original strict target/stop+expiry; persistent first reason; newNFI intent/open/pending has separately frozen priority-preempt reason. Native-99%/liq/force remain separatelytyped. Auxiliary ROI must be rejected without altering NFI global ROI.",
            "preemption": "Request full auxiliary exit persistently untilactual fill. Native open-pair-first scheduler often processesaux first, but atomic cashrelease is never assumed. OtherNFI pairs may use only real remainingcash; samepair sameclock closed/reverse reentry rejected, laterNFI needs ownnew signal.",
            "timeouts_and_replacement": "NFI check_entry/exit_timeout, adjust_order/entry/exit_price delegated unchanged. Global original3minentry/2minexit timeout runs first on5m grid. Auxentry canceled on firstlater5m, neverrepriced/retried. Auxexit reason persists across cancel/reissue; actual pending time andfees retained.",
            "order_filled": "NFI original callback with unchangedorder object and tags; aux independent audit state only. Wallet release remains native _process_exit_order→wallet.update, not set by thecallback.",
            "error_handling": "Native safe wrappers can default confirmTrue/customexitFalse. Futureaux admission catches errors andreturnsFalse; invalidtrade exits policyerror. Runtime callback-error audit/abort guard required before financial release; cannot rely onraising alone.",
        },
        "guarantees_not_provided": [
            "Full NFI signals/indicators/DCA/partial exits/fills/cash/funding/NAV parity: only future full native disabled replay can prove it.",
            "One auxiliary cannot be made invisible to originalNFI globaltradequeries or sharedwallet; enabled NFI outcomes may differ.",
            "No guaranteedfuture DCA budget orfundingreserve; noatomicpreemption or guaranteedoriginalsignal acceptance.",
            "5m LIMIT H&S standalone control and live/native lifecyclebehavior remain unimplemented/unrun; 1hmarket results cannot substitute.",
            "Full native min-order/quantity/reserve and pause/continue lifecycle require real native ownerfixtures, not pure equations.",
            "Strategy names/exportmetadata differ for thedisabled wrapper; noeconomics equivalence inferredfrom inheritedmethodidentity.",
            "Unknown AXS/TRB funding exposure and oldhistoricalprecision/ruleeligibility limits remain; two-fill funding proof does not generalize toNFI DCA orpending auxiliaryvariants.",
        ],
        "minimal_owner_extensions_if_stronger_requirements_requested": {
            "global_intent_without_calendar": "Would need native time_pair_generator/DataProvider read-only allpaircompleted-intent phase before bot_loop. Notneeded for theproposed causal sourcecalendar andnotimplemented.",
            "atomic_NFI_preemption_and_reallocatedcash": "Would require native scheduler explicitexit/actualfill/walletupdate before entry pass, changing matching order; cannot be suppliedbyIStrategy callbackorvirtualcash. Notpart of frozen nonatomicpolicy.",
            "per_source_market_vs_limit": "Would require native ordertype selection interface; notselected. KeepNFI LIMIT andseparatelyauditH&S LIMIT control.",
            "historical_operation_pause": "Owning native exchange/order lifecycle must enforce effective/known-time rules; IStrategy intentalone cannot certify cancellation/fill duringpause. Existing lifecycle work isseparate andpending.",
        },
        "fixtures_planned": [
            "26functionidentitychecks",
            "realStrategyResolverwithoutBacktesting",
            "sourceconstructorandnumericspace tags",
            "actualNFIstake/leverage/confirm/exitvector/timeouts/order_filledcustomdata onsynthetic arguments",
            "native_get_ohlcv_as_lists shiftandinitialboundary",
            "realDPperpairslicestaleness",
            "all40causalintentcalendar futureshock/missingrowrejection",
            "16directionconflictcombinationsvsactualnativehelper",
        ],
        "fixture_config_changes": "Temporary isolated cache directory, backtestRunMode, wrapperstrategy name/path, researchauxFalse. Original1000wallet config remains unchanged; noWalletobject exists.",
        "financial_execution_admitted": False,
        "execution_release_created": False,
        "financial_accounts_run": 0,
        "orders_enabled": False,
        "production_eligible": False,
    }
    (HERE / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    files = [
        HERE / name
        for name in (
            "NFIIdleCapacityDisabled.py",
            "CompletedIntentCalendar.py",
            "test_interfaces.py",
            "register.py",
            "protocol.json",
        )
    ]
    files += [
        PRIOR / "protocol.json",
        PRIOR / "artifact_manifest.json",
        PRIOR / "official_workflow_addendum_20261118.json",
        NFI,
        CONFIG,
        VENDOR.parent / "FREQTRADE_SOURCE.json",
    ]
    files += [
        VENDOR / name
        for name in (
            "freqtrade/optimize/backtesting.py",
            "freqtrade/strategy/interface.py",
            "freqtrade/data/dataprovider.py",
            "freqtrade/wallets.py",
            "freqtrade/persistence/trade_model.py",
            "freqtrade/resolvers/strategy_resolver.py",
        )
    ]
    freeze = {
        "frozen_at": datetime.now(UTC).isoformat(),
        "fixtures_executed": False,
        "financial_admitted": False,
        "files": {str(p): sha(p) for p in files},
    }
    (HERE / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n")
    print(
        json.dumps(
            {
                "frozen_files": len(files),
                "freeze_sha256": sha(HERE / "freeze.json"),
                "financial_start_called": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
