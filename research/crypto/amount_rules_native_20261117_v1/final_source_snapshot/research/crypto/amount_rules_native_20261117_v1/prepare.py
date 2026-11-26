"""Freeze actual native fixture inputs and all producers before lifecycle runs."""

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "user_data/crypto_quant/amount_rules_native_20261117_v1"
PAIR = "FAKEONE/USDT:USDT"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str) + "\n")


def main():
    OUT.mkdir(exist_ok=False)
    source = ROOT / "user_data/crypto_quant/suspension_native_20261117_v1/inputs/continue"
    metadata = json.loads((source / "markets.json").read_text())
    template = json.loads((source / "config.json").read_text())
    start, end = pd.Timestamp("2026-04-29T05:30Z"), pd.Timestamp("2026-04-29T06:30Z")
    cases = []
    for name in ("fine_DCA", "notional_fall", "post_round", "coarse_dust", "unknown"):
        folder = OUT / "inputs" / name
        data = folder / "candles/futures"
        data.mkdir(parents=True)
        times = pd.date_range(start, end, freq="min", inclusive="left")
        detail = pd.DataFrame(
            {
                "date": times,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
            }
        )
        main = (
            detail.set_index("date")
            .resample("5min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .reset_index()
        )
        main.to_feather(data / "FAKEONE_USDT_USDT-5m-futures.feather")
        detail.to_feather(data / "FAKEONE_USDT_USDT-1m-futures.feather")
        for suffix in ("1h-mark.feather", "1h-funding_rate.feather"):
            original = source / "candles/futures" / ("FAKEONE_USDT_USDT-" + suffix)
            (data / original.name).write_bytes(original.read_bytes())
        raw = copy.deepcopy(template)
        raw.update(
            strategy="FixtureAmount",
            strategy_path=str(HERE),
            fixture_case=name,
            max_open_trades=1,
            position_adjustment_enable=True,
            max_entry_position_adjustment=1,
            research_dated_amount_rules=True,
            amount_reserve_percent=0.0 if name == "post_round" else 0.05,
        )
        raw["exchange"]["pair_whitelist"] = [PAIR]
        save(folder / "config.json", raw)
        save(folder / "markets.json", metadata)
        cases.append(
            {
                "name": name,
                "folder": str(folder),
                "inputs": {str(p): sha(p) for p in sorted(folder.rglob("*")) if p.is_file()},
            }
        )
    sources = [HERE / n for n in ("FixtureAmount.py", "prepare.py", "run_native.py")]
    sources += [
        ROOT / ("freqtrade/optimize/" + n)
        for n in ("backtesting.py", "dated_amount_rules.py", "dated_price_precision.py")
    ]
    save(
        HERE / "fixture_plan.json",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "transition": "2026-04-29T06:00:00+00:00",
            "pair": PAIR,
            "cases": cases,
            "source_hashes": {str(p): sha(p) for p in sources},
            "rule_specs": {
                "fine_DCA": [["1", "1", "50"], ["0.1", "0.1", "50"]],
                "notional_fall": [["0.1", "0.1", "100"], ["0.1", "0.1", "50"]],
                "post_round": [["0.2", "0.2", "51"], ["0.2", "0.2", "51"]],
                "coarse_dust": [["0.1", "0.1", "5"], ["1", "1", "5"]],
                "unknown": [["0.1", "0.1", "5"], None],
            },
            "expected": {
                "fine_DCA": "1+.6-1.2-.4 exact native quantities and cash",
                "notional_fall": "60USDT proposal rejected before transition,admitted afterward",
                "post_round": "rounded40USDT below51minimum,no Orders",
                "coarse_dust": "unsupported close quantity aborts,not zeroed",
                "unknown": "preflight rejects missing interval before native.start",
            },
            "synthetic_only": True,
            "orders_enabled": False,
            "goal_complete": False,
        },
    )
    print("Frozen5amount-rule fixtures and every producer before native outcomes")


if __name__ == "__main__":
    main()
