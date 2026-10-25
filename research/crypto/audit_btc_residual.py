"""Independent cash, past-bar ATR and fixed initial position-budget checks."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_contract_screen import audit
from research.crypto.audit_ensemble import raw_trades
from research.crypto.autonomous_study import sleeve_summary


def campaign_audit(folder, name):
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
    rank = {s.removesuffix("USDT") + "/USDT:USDT": i for i, s in enumerate(plan["symbols"])}
    root = Path(plan["dataset"]) / "candles/futures"
    btc = pd.read_feather(root / "BTC_USDT_USDT-5m-futures.feather").set_index("date")
    prices, panels, risk_ratios, margin_ratios, fit_checks = {}, {}, [], [], []
    for index, trade in enumerate(trades):
        entries = [o for o in trade["orders"] if o["ft_is_entry"]]
        exits = [o for o in trade["orders"] if not o["ft_is_entry"]]
        if len(entries) != 1 or len(exits) != 1 or trade["leverage"] != 2:
            raise ValueError("One complete position and two-times leverage required")
        if entries[0]["order_filled_timestamp"] != trade["open_timestamp"]:
            raise ValueError("Immediate fill capital replay assumption failed")
        pair = trade["pair"]
        if pair not in prices:
            stem = pair.replace("/", "_").replace(":", "_")
            prices[pair] = pd.read_feather(root / f"{stem}-5m-futures.feather").set_index("date")
            panels[pair] = pd.read_parquet(
                Path(plan["settings"]["research_residual_panel"])
                / f"{pair.split('/')[0]}USDT.parquet"
            ).set_index("date")
        opened = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        row = panels[pair].loc[opened - pd.Timedelta(minutes=5)]
        if row.feature_open != opened - pd.Timedelta(minutes=10):
            raise ValueError("Feature publication or execution delay changed")
        frame = prices[pair]
        pos = frame.index.get_loc(row.feature_open)
        data = frame.iloc[pos - 13 : pos + 1]
        previous = frame.close.iloc[pos - 14 : pos].to_numpy()
        atr = (
            math.fsum(
                max(high - low, abs(high - prior), abs(low - prior))
                for high, low, prior in zip(data.high, data.low, previous, strict=True)
            )
            / 14
        )
        if not math.isclose(atr, row.feature_atr, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("Independent completed-bar ATR differs")
        small = trade["enter_tag"] == "small_residual"
        distance = max(0.008 if small else 0.015, (2 if small else 6) * atr / trade["open_rate"])
        if distance > (0.03 if small else 0.06) + 1e-12:
            raise ValueError("Position exceeded the frozen volatility ceiling")
        side = -1 if trade["is_short"] else 1
        stop = trade["open_rate"] * (1 - side * distance)
        if side * (trade["stop_loss_abs"] - stop) < -abs(trade["open_rate"]) * 1e-9:
            raise ValueError("Original absolute stop widened")
        clock = trade["open_timestamp"]
        prior = [
            t["profit_abs"]
            for t in trades
            if t["close_timestamp"] < clock
            or (
                t["close_timestamp"] == clock
                and (
                    t["open_timestamp"] < clock
                    or (t["open_timestamp"] == clock and rank[t["pair"]] < rank[pair])
                )
            )
        ]
        capital = (plan["initial_equity_usdt"] + math.fsum(prior)) * 0.99
        risk, margin = capital * (0.001 if small else 0.005), capital * (0.02 if small else 0.20)
        order = entries[0]
        p, q = order["safe_price"], order["amount"]
        loss = q * (-side * (stop - p) + trade["fee_open"] * p + trade["fee_close"] * stop)
        used = p * q / 2
        if loss > risk + 1e-5 or used > margin + 1e-5:
            raise ValueError("Actual fill exceeded initial risk or margin allowance")
        risk_ratios.append(loss / risk)
        margin_ratios.append(used / margin)
        if index % max(1, len(trades) // 12) == 0 and len(fit_checks) < 12:
            clocks = frame.index[pos - 4032 : pos]
            y, x = (
                np.log(frame.close.loc[clocks].to_numpy()),
                np.log(btc.close.loc[clocks].to_numpy()),
            )
            coefficient = np.linalg.lstsq(np.column_stack([np.ones(4032), x]), y, rcond=None)[0]
            beta = coefficient[1]
            residuals = y - beta * x
            old_clocks = frame.index[pos - 4033 : pos - 1]
            lagged = np.log(frame.close.loc[old_clocks].to_numpy()) - beta * np.log(
                btc.close.loc[old_clocks].to_numpy()
            )
            phi = np.linalg.lstsq(np.column_stack([np.ones(4032), lagged]), residuals, rcond=None)[
                0
            ][1]
            residual = (
                math.log(frame.close.iloc[pos])
                - beta * math.log(btc.close.loc[row.feature_open])
                - coefficient[0]
            )
            z = residual / float(np.std(residuals, ddof=0))
            errors = {
                "beta": abs(beta - row.beta),
                "residual": abs(residual - row.residual),
                "z": abs(z - row.residual_z),
                "phi": abs(phi - row.ar1_phi),
            }
            if (
                errors["beta"] > 1e-6
                or errors["residual"] > 1e-6
                or errors["z"] > 1e-4
                or errors["phi"] > 1e-4
            ):
                raise ValueError(f"Independent entry regression differs: {errors}")
            fit_checks.append({"position_index": index, "pair": pair, "errors": errors})
    report.update(
        sleeves={
            tag: sleeve_summary([t for t in trades if t["enter_tag"] == tag])
            for tag in ["small_residual", "core_residual"]
        },
        maximum_stop_risk_over_initial_budget=max(risk_ratios, default=0),
        maximum_margin_over_initial_cap=max(margin_ratios, default=0),
        independent_entry_regression_checks=fit_checks,
        campaign_risk_audit_passed=True,
        audit_source_sha256=sha(Path(__file__)),
        risk_scope="Known fixed stop and fees at entry, excludes gap loss and future funding.",
    )
    save(folder / "campaign_audit.json", report)
    return report
