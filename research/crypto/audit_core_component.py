"""Independent fill cash, frozen signal and fixed dollar stop-risk reconstruction."""

import math
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_contract_screen import audit
from research.crypto.audit_ensemble import raw_trades
from research.crypto.autonomous_study import sleeve_summary


def campaign_audit(folder, name):  # noqa: C901 - sequential original candle and fill invariants
    plan, result = read(folder / "plan.json"), read(folder / "result.json")
    policy = next(c["policy"] for c in plan["candidates"] if c["name"] == name)
    source = plan["settings"]["research_core_component_panels"][policy]
    trades, panels, prices = raw_trades(folder, name), {}, {}
    report = (
        audit(folder, name)
        if trades
        else {
            "candidate": name,
            "closed_trades": 0,
            "no_positions_observed": True,
            "maximum_fill_cash_error_usdt": 0,
            "native_fill_cash_audit_passed": True,
        }
    )
    reference = plan["initial_equity_usdt"] * 0.99
    budget, cap = reference * 0.005, reference * 0.20
    risks, margins, source_rows, atr_errors = [], [], [], []
    for trade in trades:
        entries = [o for o in trade["orders"] if o["ft_is_entry"]]
        exits = [o for o in trade["orders"] if not o["ft_is_entry"]]
        if (
            len(entries) != 1
            or len(exits) != 1
            or trade["leverage"] != 2
            or trade["enter_tag"] != "core_interaction"
        ):
            raise ValueError("Unexpected component campaign lifecycle")
        symbol = trade["pair"].split("/")[0] + "USDT"
        if symbol not in panels:
            path = Path(source["path"]) / f"{symbol}.parquet"
            if sha(path) != source["hashes"][symbol]:
                raise ValueError("Frozen forecast export changed")
            panels[symbol] = pd.read_parquet(path).set_index("date")
        clock = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        row = panels[symbol].loc[clock - pd.Timedelta(minutes=5)]
        if symbol not in prices:
            stem = trade["pair"].replace("/", "_").replace(":", "_")
            prices[symbol] = pd.read_feather(
                Path(plan["dataset"]) / "candles/futures" / f"{stem}-5m-futures.feather"
            ).set_index("date")
        market = prices[symbol]
        position = market.index.get_loc(clock - pd.Timedelta(minutes=10))
        bars = market.iloc[position - 13 : position + 1]
        prior = market.close.iloc[position - 14 : position].to_numpy()
        atr = (
            math.fsum(
                max(high - low, abs(high - previous), abs(low - previous))
                for high, low, previous in zip(bars.high, bars.low, prior, strict=True)
            )
            / 14
        )
        error = abs(float(row.feature_atr) - atr)
        if not math.isclose(float(row.feature_atr), atr, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("Frozen ATR differed from the original closed source candles")
        atr_errors.append(error)
        side = -1 if trade["is_short"] else 1
        if (
            side != row.forecast_side
            or entries[0]["order_filled_timestamp"] != trade["open_timestamp"]
        ):
            raise ValueError("Native entry did not match the known frozen direction/clock")
        rate, amount = entries[0]["safe_price"], entries[0]["amount"]
        distance = max(0.015, 6 * atr / rate)
        if distance > 0.12 + 1e-12:
            raise ValueError("Entry exceeded the volatility ceiling")
        stop = rate * (1 - side * distance)
        if side * (trade["stop_loss_abs"] - stop) < -rate * 1e-9:
            raise ValueError("Absolute stop widened")
        loss = amount * (
            -side * (stop - rate) + trade["fee_open"] * rate + trade["fee_close"] * stop
        )
        margin = amount * rate / 2
        if loss > budget + 1e-5 or margin > cap + 1e-5:
            raise ValueError("Actual entry exceeded fixed initial-dollar risk/margin")
        if trade["trade_duration"] > 1445:
            raise ValueError("Campaign exceeded the 24-hour full-exit cap")
        if trade["exit_reason"] == "missing_core_interaction_state":
            raise ValueError("Required native risk state was missing")
        risks.append(loss / budget)
        margins.append(margin / cap)
        source_rows.append(int(row.row_id))
    report.update(
        sleeves={"small": sleeve_summary([]), "core": sleeve_summary(trades)},
        maximum_entry_stop_loss_over_fixed_budget=max(risks, default=0),
        maximum_margin_over_fixed_cap=max(margins, default=0),
        maximum_original_atr_error=max(atr_errors, default=0),
        fixed_reference_capital_usdt=reference,
        frozen_source_row_ids=source_rows,
        campaign_risk_audit_passed=True,
        audit_source_sha256=sha(Path(__file__)),
        result_sha256=sha(folder / "result.json"),
        core_component_only=True,
        prior_joint_unit_screen_passed=False,
        risk_scope="Entry stop plus known fees at fixed initial-dollar budget; future funding and "
        "gap losses are not guaranteed. No additions or partial exits. This does not meet the "
        "small-frequency-plus-core-profit objective by itself.",
    )
    if result["closed_trades"] != len(trades):
        raise ValueError("Complete position counts differ")
    save(folder / "campaign_audit.json", report)
    return report
