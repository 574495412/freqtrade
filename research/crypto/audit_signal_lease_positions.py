"""Replay every actual first-hit entry, fixed risk, complete exit and funding settlement."""

import math
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from freqtrade.exchange.exchange import TICK_SIZE
from freqtrade.research.data import read, save, sha
from research.crypto.audit_contract_screen import audit
from research.crypto.audit_ensemble import raw_trades
from research.crypto.autonomous_study import sleeve_summary
from research.crypto.first_hit_paths import market_bundle
from research.crypto.intrabar_small_paths import SPECS
from research.crypto.replay_signal_lease import replay


def campaign_audit(folder, name):  # noqa: C901 - every actual entry and complete position
    plan, result = read(folder / "plan.json"), read(folder / "result.json")
    candidate = next(c for c in plan["candidates"] if c["name"] == name)
    policy = candidate["policy"]
    source = plan["settings"]["research_first_hit_panels"][policy]
    trades, panels = raw_trades(folder, name), {}
    report = (
        audit(folder, name)
        if trades
        else {
            "candidate": name,
            "closed_trades": 0,
            "maximum_fill_cash_error_usdt": 0,
            "native_fill_cash_audit_passed": True,
            "no_positions_observed": True,
            "fees_usdt": "0",
            "funding_net_usdt": 0,
            "exit_reasons": {},
            "net_profit_usdt": 0,
        }
    )
    calls_path = folder / "precision_calls.parquet"
    if sha(calls_path) != read(folder / "runtime_adapter.json")["precision_calls_sha256"]:
        raise ValueError("Precision proposal observations changed")
    calls = pd.read_parquet(calls_path)
    errors, risks, margins, row_ids, maxima = {}, [], [], [], Counter()
    lease_entries = []
    reference = plan["initial_equity_usdt"] * 0.99
    for trade in trades:
        entries = [o for o in trade["orders"] if o["ft_is_entry"]]
        exits = [o for o in trade["orders"] if not o["ft_is_entry"]]
        if len(entries) != 1 or len(exits) != 1 or trade["leverage"] != 2:
            raise ValueError("Not a single-entry complete-position lifecycle")
        sleeve = trade["enter_tag"].removesuffix("_first_hit")
        if sleeve not in SPECS or trade["exit_reason"] not in {
            "first_hit_profit",
            "first_hit_time",
            "first_hit_gap",
            "stop_loss",
            "roi",
            "core_profit_lock",
        }:
            raise ValueError("Unexpected first-hit lifecycle state or trailing stop")
        symbol = trade["pair"].split("/")[0] + "USDT"
        if symbol not in panels:
            path = Path(source["path"]) / f"{symbol}.parquet"
            if sha(path) != source["hashes"][symbol]:
                raise ValueError("Frozen signal changed")
            panels[symbol] = pd.read_parquet(path).set_index("date")
        clock = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        row = panels[symbol].loc[clock - pd.Timedelta(minutes=5)]
        if row.forecast_sleeve != sleeve or row.forecast_side != (-1 if trade["is_short"] else 1):
            raise ValueError("Actual entry does not match the frozen signal")
        price, funding, marks = market_bundle(plan["dataset"], plan["data_receipts_sha256"], symbol)
        position = price.index.get_loc(pd.Timestamp(row.parent_feature_open))
        bars, prior = (
            price.iloc[position - 13 : position + 1],
            price.close.iloc[position - 14 : position].to_numpy(),
        )
        atr = (
            math.fsum(
                max(high - low, abs(high - previous), abs(low - previous))
                for high, low, previous in zip(bars.high, bars.low, prior, strict=True)
            )
            / 14
        )
        if not math.isclose(atr, row.feature_atr, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("Original closed-candle ATR differs")
        observed = calls.loc[calls.pair.eq(trade["pair"]) & calls.date.eq(clock)]
        if len(observed) != 1:
            raise ValueError("Actual entry lacks one native precision proposal")
        proposal = observed.iloc[0]
        if proposal.tick != price.loc[clock, "known_tick"] or proposal["mode"] != TICK_SIZE:
            raise ValueError("Actual native precision differs from the causal proxy")
        if proposal[f"open_{sleeve}"] + 1 > 2 or proposal.open_total + 1 > 4:
            raise ValueError("Actual successful entry exceeded the global/per-layer capacity")
        maxima[sleeve] = max(maxima[sleeve], int(proposal[f"open_{sleeve}"]) + 1)
        maxima["total"] = max(maxima["total"], int(proposal.open_total) + 1)
        unit = SimpleNamespace(
            feature_open=clock - pd.Timedelta(minutes=10),
            sleeve=sleeve,
            side=int(row.forecast_side),
            atr=atr,
            protect_core=candidate["protect_core"],
        )
        native = replay(unit, price, funding, marks, result["extra_cost_bps_per_side"])
        if (
            native["exit_time"] != pd.to_datetime(trade["close_timestamp"], unit="ms", utc=True)
            or native["exit_reason"] != trade["exit_reason"]
        ):
            raise ValueError("Actual complete exit differs from an independent native path replay")
        notional = entries[0]["amount"] * entries[0]["safe_price"]
        for key, actual, expected, tolerance in [
            ("entry_price", trade["open_rate"], float(price.loc[clock, "open"]), 1e-10),
            ("exit_price", trade["close_rate"], native["exit_price"], 1e-10),
            ("locked_stop", trade["stop_loss_abs"], native["locked_stop"], 1e-10),
            ("net_cash_usdt", trade["profit_abs"], native["net_label"] * notional, 1e-7),
            ("funding_usdt", trade["funding_fees"], native["funding_credit"] * notional, 1e-8),
        ]:
            errors[key] = max(errors.get(key, 0), abs(actual - expected))
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=tolerance):
                raise ValueError((key, actual, expected))
        sign, spec = unit.side, SPECS[sleeve]
        distance = max(spec["floor"], spec["atr_multiple"] * atr / trade["open_rate"])
        risk_fraction, cap_fraction = (0.001, 0.04) if sleeve == "small" else (0.005, 0.20)
        planned_loss = notional * (
            distance + trade["fee_open"] + trade["fee_close"] * (1 - sign * distance)
        )
        margin = notional / 2
        if (
            distance > spec["ceiling"]
            or planned_loss > reference * risk_fraction + 1e-5
            or margin > reference * cap_fraction + 1e-5
        ):
            raise ValueError("Actual entry violated the initial-dollar risk or margin limit")
        risks.append(planned_loss / (reference * risk_fraction))
        margins.append(margin / (reference * cap_fraction))
        row_ids.append(int(row.row_id))
        lease_entries.append(
            {
                "pair": trade["pair"],
                "sleeve": sleeve,
                "lease_id": row.lease_id,
                "parent_date": pd.Timestamp(row.parent_date).isoformat(),
                "open_timestamp": trade["open_timestamp"],
                "close_timestamp": trade["close_timestamp"],
                "profit_abs": trade["profit_abs"],
            }
        )
    sleeves = {
        layer: sleeve_summary([t for t in trades if t["enter_tag"] == f"{layer}_first_hit"])
        for layer in SPECS
    }
    report.update(
        sleeves=sleeves,
        lease_entry_receipts=lease_entries,
        campaign_risk_audit_passed=True,
        maximum_actual_path_errors=errors,
        actual_paths_replayed=len(trades),
        maximum_entry_stop_loss_over_fixed_budget=max(risks, default=0),
        maximum_margin_over_fixed_cap=max(margins, default=0),
        maximum_positions_after_successful_entry=dict(maxima),
        frozen_source_row_ids=row_ids,
        initial_equity_usdt=plan["initial_equity_usdt"],
        audit_source_sha256=sha(Path(__file__)),
        result_sha256=sha(folder / "result.json"),
        precision_calls_sha256=sha(calls_path),
        prior_joint_unit_admission_passed=False,
        risk_scope=(
            "Fixed initial-dollar planned stop plus known fees; gaps and future funding "
            "can exceed budget. Both sleeve rules retained; no promotion from this diagnostic."
        ),
    )
    if result["closed_trades"] != len(trades):
        raise ValueError("Complete positions differ")
    save(folder / "campaign_audit.json", report)
    return report
