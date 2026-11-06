"""Verify the paired episode cash and causal entry clocks directly from raw JSON."""

from bisect import bisect_right
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

from research.crypto.cross_venue_funding_20261107 import study
from research.crypto.nfi_strength_allocation_20261107.audit import read
from research.crypto.strategy_comparison_wide_20261105.run_wide_comparison import checked, save, sha


def catalog(raw, prefix):
    return [row for key, rows in raw.items() if key.startswith(prefix) for row in rows]


def sources(raw, coin):
    bn = {r[0]: (D(r[1]), D(r[4])) for r in catalog(raw, f"bn_price_{coin}USDT_")}
    hl = {r["t"]: (D(r["o"]), D(r["c"])) for r in raw[f"hl_price_{coin}"]}
    fx = {r[0]: (D(r[1]), D(r[4])) for r in catalog(raw, "bn_price_USDCUSDT_")}
    fbn = [
        (r["fundingTime"], D(r["fundingRate"]), D(r["markPrice"]))
        for r in catalog(raw, f"bn_fund_{coin}USDT_")
    ]
    fhl = []
    for r in catalog(raw, f"hl_fund_{coin}_"):
        clock = r["time"]
        hour = ((clock + 1_800_000) // 3_600_000) * 3_600_000
        if hour - 3_600_000 not in hl:
            continue
        fhl.append((clock, D(r["fundingRate"]), hl[hour - 3_600_000][1]))
    return bn, hl, fx, fbn, fhl


def asof_daily(records, clock):
    i = bisect_right([r[0] for r in records], clock - 300_000) - 1
    if i < 1:
        raise ValueError("No prior interval to compute the settlement rate")
    t, rate, _ = records[i]
    previous = records[i - 1][0]
    interval_hours = round((t - previous) / 3_600_000)
    return rate * 24 / interval_hours


def cash(coin_data, side, opened, priced_at, at_open, cost, gross=False):
    bn, hl, fx, fbn, fhl = coin_data
    first = opened - 5000
    last = priced_at - 5000 if at_open else priced_at - 3_600_000
    k = 0 if at_open else 1
    fee = D(0) if gross else D("0.0005")
    slip = D(0) if gross else D(cost - 5) / 10000
    a1 = bn[first][0] * (1 + side * slip)
    a2 = hl[first][0] * (1 - side * slip)
    b1 = bn[last][k] * (1 - side * slip)
    b2 = hl[last][k] * (1 + side * slip)
    f1 = fx[last][k]
    funding_bn = sum(rate * px for t, rate, px in fbn if opened < t < priced_at)
    funding_hl = sum(rate * px for t, rate, px in fhl if opened < t < priced_at)
    bn_cash = (side * (b1 - a1 - funding_bn) - fee * (a1 + b1)) / f1
    hl_cash = side * (a2 - b2 + funding_hl) - fee * (a2 + b2)
    denom = (a1 / fx[first][0] + a2) / 2
    return (bn_cash + hl_cash) / denom


def main():
    result = read(study.RESULT)
    if result["registration_sha256"] != sha(study.POLICY):
        raise ValueError("Study registration changed")
    receipts = read(checked(result["inputs"]))
    raw = {name: read(checked(row["source"])) for name, row in receipts.items()}
    coins = {coin: sources(raw, coin) for coin in study.COINS}
    output = {}
    for key, account in result["windows"].items():
        cost = int(key.split("_")[-1][:-3])
        prior = {}
        max_error = max_signal = 0.0
        rows = read(checked(account["ledger"]))
        zero_slip = []
        frictionless = []
        for row in rows:
            coin = row["coin"]
            data = coins[coin]
            open_ms, close_ms = (
                pd.Timestamp(row[field]).value // 1_000_000 for field in ("open", "close")
            )
            if open_ms <= prior.get(coin, 0) or not 0 < close_ms - open_ms <= 7 * 86_400_000:
                raise ValueError("Episode exposure overlaps or exceeds maximum duration")
            prior[coin] = close_ms
            entry_decision = open_ms - 3_605_000
            side = row["bn_side"]
            diff = asof_daily(data[3], entry_decision) - asof_daily(data[4], entry_decision)
            if diff * side > D("-0.001"):
                raise ValueError("Causal entry threshold or direction failed")
            signal_error = abs(float(diff) - row["signal_diff_per_day"])
            net = cash(data, side, open_ms, close_ms, True, cost)
            closing_decision = close_ms - 3_605_000
            closing_diff = asof_daily(data[3], closing_decision) - asof_daily(
                data[4], closing_decision
            )
            preview = cash(data, side, open_ms, closing_decision, False, cost)
            window_start = pd.Timestamp(key.split("_")[0], tz="UTC")
            window_end = study.SPLIT if window_start == study.BEGIN else study.END
            reason = study.exit_reason(
                float(-side * closing_diff),
                float(preview),
                pd.Timedelta(milliseconds=close_ms - open_ms),
                close_ms == study.ms(window_end) + 5000,
            )
            if reason != row["reason"] or closing_decision <= open_ms:
                raise ValueError("Causal exit condition differs")
            if row["reason"] != "boundary":
                zero_slip.append(cash(data, side, open_ms, close_ms, True, 5))
                frictionless.append(cash(data, side, open_ms, close_ms, True, 5, gross=True))
            error = abs(float(net) - row["net"])
            if max(error, signal_error) > 1e-10:
                raise ValueError("Independent decimal cash or funding signal differs")
            max_error = max(max_error, error)
            max_signal = max(max_signal, signal_error)
        output[key] = {
            "episodes": len(rows),
            "max_return_error": max_error,
            "max_signal_error": max_signal,
            "natural_mean_net_5bps_fee_zero_slip_fixed_episodes": float(
                sum(zero_slip) / len(zero_slip)
            ),
            "natural_mean_net_zero_cost_fixed_episodes": float(
                sum(frictionless) / len(frictionless)
            ),
        }
    save(
        Path(__file__).with_name("verification.json"),
        {
            "source_sha256": sha(Path(__file__)),
            "result_sha256": sha(study.RESULT),
            "windows": output,
        },
    )


if __name__ == "__main__":
    main()
