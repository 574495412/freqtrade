"""Freeze joint native lifecycle inputs and producers before observing outputs."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/joint_rules_native_20261117_v1"
PAIRS = ("FAKEONE/USDT:USDT", "FAKETWO/USDT:USDT")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str, allow_nan=False) + "\n")


def main():
    assert not (HERE / "fixture_plan.json").exists()
    OUT.mkdir(exist_ok=False)
    parent = ROOT / "user_data/crypto_quant/suspension_native_20261117_v1/inputs/continue"
    metadata = json.loads((parent / "markets.json").read_text())
    template = json.loads((parent / "config.json").read_text())
    start, end = pd.Timestamp("2026-04-29T05:30Z"), pd.Timestamp("2026-04-29T06:30Z")
    cases = []
    for name, policy, expected in (
        ("continue", "continue", "completed"),
        ("stop", "stop", "completed"),
        ("coarse_dust", "continue", "rejected"),
        ("unknown_amount", "continue", "rejected"),
        ("unknown_pending", "unknown", "rejected"),
    ):
        folder = OUT / "inputs" / name
        data = folder / "candles/futures"
        data.mkdir(parents=True)
        for pair in PAIRS:
            stem = pair.replace("/", "_").replace(":", "_")
            dates = pd.date_range(start, end, freq="min", inclusive="left")
            detail = pd.DataFrame(
                dict(date=dates, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
            )
            if pair == PAIRS[0]:
                detail.loc[
                    detail.date.isin(pd.to_datetime(["2026-04-29T06:01Z", "2026-04-29T06:03Z"])),
                    "high",
                ] = 104.0
            main = (
                detail.set_index("date")
                .resample("5min")
                .agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                )
                .reset_index()
            )
            main.to_feather(data / f"{stem}-5m-futures.feather")
            detail.to_feather(data / f"{stem}-1m-futures.feather")
            for suffix in ("1h-mark.feather", "1h-funding_rate.feather"):
                old = parent / "candles/futures" / f"{stem}-{suffix}"
                (data / old.name).write_bytes(old.read_bytes())
        raw = copy.deepcopy(template)
        raw.update(
            strategy="FixtureJoint",
            strategy_path=str(HERE),
            fixture_case=name,
            position_adjustment_enable=True,
            max_entry_position_adjustment=1,
            research_dated_amount_rules=True,
            custom_price_max_distance_ratio=0.05,
        )
        save(folder / "config.json", raw)
        save(folder / "markets.json", metadata)
        cases.append(
            dict(
                name=name,
                policy=policy,
                expected=expected,
                folder=str(folder),
                inputs={str(p): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()},
            )
        )
    save(
        HERE / "protocol.json",
        dict(
            frozen_at=datetime.now(UTC).isoformat(),
            objective="Joint DatedAmountMixin+OrderOperationMixin+Backtesting full native lifecycle engineering",
            cases=[dict(name=c["name"], policy=c["policy"], expected=c["expected"]) for c in cases],
            universe=list(PAIRS),
            start=start.isoformat(),
            end=end.isoformat(),
            initial_usdt=1000,
            leverage=1,
            fee_per_side=0.0015,
            decision_timeframe="5m",
            detail_timeframe="1m",
            planned_sequence="FAKEONE1+0.6-1.2-0.4;FAKETWO new entry delayed during pause",
            amount_transition="05:50step1->0.1;coarse_dust additionally06:00step1;unknown_amount lacks final interval",
            price_transition="06:00tick0.001->0.01;old103.005partial order keeps accepted price",
            operation_window="06:00<=t<06:03;continue/stop are explicit synthetic policies;unknown rejects reachable old order",
            expected_complete_fills=dict(
                continue_partial="06:01", stop_partial="06:03", resumed_entry="06:03"
            ),
            independent_audit="Read actual ZIP fills;Decimal cash,fees,06:00funding and terminalwallet;no candidate or engine import",
            synthetic_only=True,
            new_historical_strategy_accounts=0,
            corrected_NFI_returns=None,
            orders_enabled=False,
            production_eligible=False,
            goal_complete=False,
        ),
    )
    sources = [
        HERE / n
        for n in (
            "FixtureJoint.py",
            "prepare.py",
            "run_native.py",
            "execute.py",
            "audit.py",
            "protocol.json",
        )
    ]
    sources += [
        ROOT / "freqtrade/optimize" / n
        for n in (
            "backtesting.py",
            "dated_amount_rules.py",
            "dated_price_precision.py",
            "order_operations.py",
        )
    ]
    save(
        HERE / "fixture_plan.json",
        dict(
            frozen_at=datetime.now(UTC).isoformat(),
            start=start.isoformat(),
            end=end.isoformat(),
            quantity_transition="2026-04-29T05:50:00+00:00",
            operation_start="2026-04-29T06:00:00+00:00",
            operation_end="2026-04-29T06:03:00+00:00",
            pairs=list(PAIRS),
            cases=cases,
            source_hashes={str(p): sha(p) for p in sources},
            parent_inputs={
                str(parent / n): sha(parent / n) for n in ("config.json", "markets.json")
            },
            synthetic_only=True,
            orders_enabled=False,
        ),
    )
    print("Frozen 5 cases and all producers before native output", flush=True)


if __name__ == "__main__":
    main()
