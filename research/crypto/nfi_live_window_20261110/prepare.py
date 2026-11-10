"""Freeze the requested two-hour live cohort and append public candle tails.

Input preparation only. One paced public reader uses the installed cross-process
Binance guard; no exchange credentials, trading worker or simulator is started.
"""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from freqtrade.exchange.request_guard import install


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "user_data/crypto_quant/nfi_live_window_20261110_v001"
START = pd.Timestamp("2026-11-10T05:00:00Z")
END = pd.Timestamp("2026-11-10T07:00:00Z")
FREQUENCIES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temporary.replace(path)


def freeze():
    OUT.mkdir(parents=True, exist_ok=True)
    plan = {
        "start": str(START),
        "end_exclusive": str(END),
        "start_shanghai": str(START.tz_convert("Asia/Shanghai")),
        "end_shanghai": str(END.tz_convert("Asia/Shanghai")),
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Matched native replay, not a new strategy or a live order test",
        "accounts": {},
        "orders_enabled": False,
        "results_observed": False,
    }
    for market, oldplan in (("spot", "spot100-plan-v002.json"), ("futures", "author100-plan.json")):
        folder = ROOT / "user_data/deployment/mainnet-100-20261109" / market
        config = json.loads((folder / "config.json").read_text())
        old = json.loads((ROOT / "research/crypto/nfi_truth_audit_20261109" / oldplan).read_text())
        pairs = config["exchange"]["pair_whitelist"]
        if market == "futures":
            snapshot = json.loads(Path(config["daily_snapshot_file"]).read_text())
            if not pd.Timestamp(snapshot["generated_at_utc"]) < START:
                raise ValueError("Pool was published after the comparison began")
            if not pd.Timestamp(snapshot["valid_until_utc"]) > END:
                raise ValueError("Pool expired inside the comparison window")
            pairs = snapshot["pairs"]
            save(OUT / "frozen-live-pool.json", snapshot)
        # No private notification endpoints or exchange credentials enter the research config.
        config["webhook"] = {"enabled": False, "url": "http://127.0.0.1/unused"}
        config["telegram"] = {"enabled": False}
        config["api_server"] = {"enabled": False}
        config["exchange"].update(key="", secret="", password="")
        if config["available_capital"] != 99 or config["capital_ceiling_usdt"] != 100:
            raise ValueError("Unexpected live capital contract")
        save(OUT / f"{market}-live-contract.json", config)
        plan["accounts"][market] = {
            "pairs": pairs,
            "live_config_sha256": sha(folder / "config.json"),
            "contract": str(OUT / f"{market}-live-contract.json"),
            "historical_data": old["datadir"],
            "metadata": old["market_snapshot"],
            "initial_equity": 100,
            "stake_budget": 99,
            "max_open_trades": config["max_open_trades"],
            "leverage": 1 if market == "spot" else 3,
            "cost_bps_per_side": 13 if market == "spot" else 8,
            "cost_scope": (
                "Same frozen all-in historical model; not actual symbol commission certification"
            ),
        }
    save(OUT / "plan.json", plan)
    save(HERE / "protocol.json", plan)
    return plan


def frame(rows):
    return pd.DataFrame(
        {
            "date": pd.to_datetime([r[0] for r in rows], unit="ms", utc=True),
            **{
                name: [float(r[i]) for r in rows]
                for name, i in (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5))
            },
        }
    )


def main():  # noqa: C901 -- Public input preparation, no financial engine.
    plan = json.loads((OUT / "plan.json").read_text()) if (OUT / "plan.json").exists() else freeze()
    client = ccxt.binance(
        {
            "enableRateLimit": True,
            "httpsProxy": "http://127.0.0.1:7897",
            "timeout": 25000,
            "apiKey": "",
            "secret": "",
        }
    )
    install(client, sync=True)
    records = []
    last_request = 0

    def get(name, params, raw_path):
        nonlocal last_request
        if raw_path.exists():
            return json.loads(raw_path.read_text())
        time.sleep(max(0, 1 - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        # No local retry on418/429. The shared response hook retains the deadline.
        value = getattr(client, name)(params)
        save(raw_path, value)
        return value

    for market, account in plan["accounts"].items():
        base = Path(account["historical_data"])
        target = OUT / market / "candles"
        native_target = target / "futures" if market == "futures" else target
        native_target.mkdir(parents=True, exist_ok=True)
        raw = OUT / market / "public"
        raw.mkdir(exist_ok=True)
        for index, pair in enumerate(account["pairs"], 1):
            stem = pair.replace("/", "_").replace(":", "_")
            suffix = "-futures" if market == "futures" else ""
            output = native_target / f"{stem}-5m{suffix}.feather"
            receipt = OUT / market / f"{stem}-admission.json"
            if receipt.exists():
                records.append(json.loads(receipt.read_text()))
                continue
            source = next(base.rglob(output.name))
            before = pd.read_feather(source)
            cursor = int((before.date.iloc[-1] + pd.Timedelta(minutes=5)).timestamp() * 1000)
            additions = []
            while cursor < int(END.timestamp() * 1000):
                rows = get(
                    "publicGetKlines" if market == "spot" else "fapiPublicGetKlines",
                    {
                        "symbol": pair.split(":")[0].replace("/", ""),
                        "interval": "5m",
                        "startTime": cursor,
                        "endTime": int(END.timestamp() * 1000) - 1,
                        "limit": 499 if market == "futures" else 1000,
                    },
                    raw / f"{stem}-5m-{cursor}.json",
                )
                if not rows:
                    raise ValueError(f"Missing actual tail: {market} {pair}")
                additions.extend(rows)
                cursor = int(rows[-1][0]) + 300000
            prices = (
                pd.concat([before, frame(additions)], ignore_index=True) if additions else before
            )
            prices["date"] = pd.to_datetime(prices.date, utc=True).dt.as_unit("ns")
            prices = prices.loc[
                (prices.date >= START.floor("D") - pd.Timedelta(days=4)) & (prices.date < END)
            ].reset_index(drop=True)
            expected = pd.date_range(
                prices.date.iloc[0], END - pd.Timedelta(minutes=5), freq="5min"
            )
            if not pd.DatetimeIndex(prices.date).equals(expected):
                raise ValueError(f"Missing/duplicate actual5m candles: {pair}")
            if not np.isfinite(prices[["open", "high", "low", "close", "volume"]]).all().all():
                raise ValueError(f"Nonfinite input: {pair}")
            if len(prices.loc[prices.date < START]) < 800:
                raise ValueError(f"Insufficient main warmup: {pair}")
            prices.to_feather(output)
            for tf, frequency in list(FREQUENCIES.items())[1:]:
                old = pd.read_feather(next(base.rglob(f"{stem}-{tf}{suffix}.feather")))
                # Replace recent possibly incomplete source bars, preserving older history.
                boundary = START.floor("D") - pd.Timedelta(days=2)
                recent = (
                    prices.loc[prices.date >= boundary]
                    .set_index("date")
                    .resample(frequency, label="left", closed="left")
                    .agg(
                        {
                            "open": "first",
                            "high": "max",
                            "low": "min",
                            "close": "last",
                            "volume": "sum",
                        }
                    )
                    .dropna()
                    .reset_index()
                )
                seconds = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}[tf]
                recent = recent.loc[recent.date + pd.Timedelta(seconds=seconds) <= END]
                combined = pd.concat([old.loc[old.date < boundary], recent], ignore_index=True)
                combined["date"] = pd.to_datetime(combined.date, utc=True).dt.as_unit("ns")
                lower = START.floor(frequency) - pd.Timedelta(seconds=seconds * 840)
                combined.loc[combined.date >= lower].reset_index(drop=True).to_feather(
                    native_target / f"{stem}-{tf}{suffix}.feather"
                )
            if market == "futures":
                params = {
                    "symbol": pair.split(":")[0].replace("/", ""),
                    "startTime": int((START - pd.Timedelta(days=1)).timestamp() * 1000),
                    "endTime": int(END.timestamp() * 1000) - 1,
                    "limit": 99,
                }
                marks = get(
                    "fapiPublicGetMarkPriceKlines",
                    {**params, "interval": "1h"},
                    raw / f"{stem}-mark.json",
                )
                frame(marks).to_feather(native_target / f"{stem}-1h-mark.feather")
                funding = get("fapiPublicGetFundingRate", params, raw / f"{stem}-funding.json")
                pd.DataFrame(
                    {
                        "date": pd.to_datetime(
                            [int(r["fundingTime"]) for r in funding], unit="ms", utc=True
                        ),
                        "funding_rate": [float(r["fundingRate"]) for r in funding],
                    }
                ).to_feather(native_target / f"{stem}-1h-funding_rate.feather")
            files = list(native_target.glob(stem + "-*.feather"))
            row = {
                "market": market,
                "pair": pair,
                "rows5m": len(prices),
                "window5m_rows": len(prices.loc[prices.date >= START]),
                "last5m": str(prices.date.iloc[-1]),
                "source5m_sha256": sha(source),
                "files": {str(p.relative_to(OUT)): sha(p) for p in files},
            }
            save(receipt, row)
            records.append(row)
            print(
                json.dumps(
                    {
                        "stage": "data",
                        "market": market,
                        "pair": pair,
                        "completed": index,
                        "total": len(account["pairs"]),
                    }
                ),
                flush=True,
            )
    save(
        OUT / "input-admission.json",
        {"records": records, "closed_candles_only": True, "invented_bars": False, "complete": True},
    )
    print(json.dumps({"data_complete": True, "pairs": len(records)}), flush=True)


if __name__ == "__main__":
    main()
