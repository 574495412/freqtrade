"""Reconstruct complete NFI campaigns and initial stop exposure from actual fills."""

import math
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_contract_screen import audit
from research.crypto.audit_ensemble import raw_trades
from research.crypto.autonomous_study import sleeve_summary


def campaign_audit(folder, name):  # noqa: C901 - sequential actual fill and risk invariants
    plan = read(folder / "plan.json")
    trades = raw_trades(folder, name)
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
    capital = plan["initial_equity_usdt"] * 0.99
    prices, risks, margins, planned, durations, counts, full_requests = {}, [], [], [], [], [], 0
    layers = {"small": [], "core": []}
    for trade in trades:
        entries = [o for o in trade["orders"] if o["ft_is_entry"]]
        exits = [o for o in trade["orders"] if not o["ft_is_entry"]]
        if not 1 <= len(entries) <= 2 or not exits or trade["is_short"] or trade["leverage"] != 2:
            raise ValueError("Campaign order count or directional scope changed")
        if entries[0]["order_filled_timestamp"] != trade["open_timestamp"]:
            raise ValueError("First immediate-fill/source-bar assumption failed")
        sleeve = "core" if set(trade["enter_tag"].split()) == {"61"} else "small"
        layers[sleeve].append(trade)
        pair = trade["pair"]
        if pair not in prices:
            stem = pair.replace("/", "_").replace(":", "_")
            prices[pair] = pd.read_feather(
                Path(plan["dataset"]) / "candles/futures" / f"{stem}-5m-futures.feather"
            ).set_index("date")
        frame = prices[pair]
        source_time = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True) - pd.Timedelta(
            minutes=5
        )
        position = frame.index.get_loc(source_time)
        subset, previous = (
            frame.iloc[position - 13 : position + 1],
            frame.close.iloc[position - 14 : position].to_numpy(),
        )
        atr = (
            math.fsum(
                max(high - low, abs(high - prior), abs(low - prior))
                for high, low, prior in zip(subset.high, subset.low, previous, strict=True)
            )
            / 14
        )
        first = Decimal(str(entries[0]["safe_price"]))
        small = sleeve == "small"
        distance = max(0.06 if small else 0.10, (4 if small else 8) * atr / float(first))
        if distance > (0.12 if small else 0.20) + 1e-12:
            raise ValueError("Initial volatility ceiling violated")
        stop = first * (1 - Decimal(str(distance)))
        if Decimal(str(trade["stop_loss_abs"])) < stop - first * Decimal("1e-9"):
            raise ValueError("Campaign stop widened")
        risk, cap = capital * (0.001 if small else 0.005), capital * (0.02 if small else 0.20)
        fee_open, fee_close = Decimal(str(trade["fee_open"])), Decimal(str(trade["fee_close"]))
        first_margin = Decimal(str(entries[0]["amount"])) * first / 2
        planned_loss = (
            2 * first_margin * 2 * (1 - stop / first + fee_open + fee_close * stop / first)
        )
        planned.append(float(planned_loss) / risk)
        loss, used = Decimal(0), Decimal(0)
        for index, entry in enumerate(entries):
            price, quantity = Decimal(str(entry["safe_price"])), Decimal(str(entry["amount"]))
            stake = price * quantity / 2
            if index:
                if not stop < price <= first + first * Decimal("1e-12"):
                    raise ValueError("Addition exceeded frozen price boundary")
                if stake > first_margin + Decimal("0.00001"):
                    raise ValueError("Additional stake exceeded first stake")
                if any(
                    o["order_filled_timestamp"] <= entry["order_filled_timestamp"] for o in exits
                ):
                    raise ValueError("Partial realization was recycled into another entry")
                if entry["ft_order_tag"] != "bounded_nfi_add":
                    raise ValueError("Unknown additional fill tag")
            loss += quantity * (price - stop + fee_open * price + fee_close * stop)
            used += stake
            risks.append(float(loss) / risk)
            margins.append(float(used) / cap)
            if float(loss) > risk + 1e-5 or float(used) > cap + 1e-5:
                raise ValueError("Filled position exceeded fixed initial dollar risk or margin")
        if float(planned_loss) > risk + 1e-5:
            raise ValueError("Initial reserve for second fill exceeded budget")
        if name == "NFIBoundedFullExit" and len(exits) != 1:
            raise ValueError("Full-reduction version retained partial exits")
        full_requests += sum(o["ft_order_tag"] == "bounded_full_reduction" for o in exits)
        cap_minutes = 360 if small else 4320
        duration = (trade["close_timestamp"] - trade["open_timestamp"]) / 60000
        if duration > cap_minutes + 5:
            raise ValueError("Observed campaign exceeded frozen time cap")
        durations.append(duration)
        counts.append(len(entries))
    if sum(len(v) for v in layers.values()) != len(trades):
        raise ValueError("Layer totals differ")
    report.update(
        sleeves={key: sleeve_summary(value) for key, value in layers.items()},
        entry_count_distribution=dict(Counter(counts)),
        maximum_initial_planned_stop_loss_over_budget=max(planned, default=0),
        maximum_actual_entry_prefix_stop_loss_over_budget=max(risks, default=0),
        maximum_campaign_margin_over_cap=max(margins, default=0),
        maximum_holding_minutes=max(durations, default=0),
        full_reduction_request_fills=full_requests,
        campaigns_with_multiple_exit_fills=sum(
            sum(not o["ft_is_entry"] for o in t["orders"]) > 1 for t in trades
        ),
        fixed_reference_capital_usdt=capital,
        campaign_risk_audit_passed=True,
        audit_source_sha256=sha(Path(__file__)),
        result_sha256=sha(folder / "result.json"),
        risk_scope=(
            "Fixed initial-dollar stop and fee exposure at entry prefixes; future funding and "
            "gap loss are not guaranteed. Partial fills are not separate winning positions."
        ),
    )
    save(folder / "campaign_audit.json", report)
    return report
