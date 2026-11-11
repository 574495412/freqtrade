"""Prepare closed-UTC daily inputs with the identical pinned historical rulebook.

This is a local input-preparation extension. Orders and accounting are performed
by native Freqtrade; the historical blacklist's hindsight bias remains explicit.
"""

import fcntl
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import rapidjson

from freqtrade.enums import CandleType, RunMode
from freqtrade.exchange.request_guard import GuardedSession
from freqtrade.resolvers import ExchangeResolver


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATE = ROOT / "user_data/deployment/nfi80-selected-20261109"
SNAPSHOT = STATE / "current-pairs.json"
RUNTIME = ROOT / "user_data/deployment/live-selector-runtime.json"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def runtime_context():
    value = read(RUNTIME) if RUNTIME.exists() else {}
    if set(value) - {"source_root", "https_proxy"}:
        raise ValueError("Selector runtime may only relocate paths and exchange transport")
    if "source_root" in value and (
        not isinstance(value["source_root"], str) or not Path(value["source_root"]).is_absolute()
    ):
        raise ValueError("Registered input source root must be absolute")
    proxy = value.get("https_proxy")
    if proxy is not None and (
        not isinstance(proxy, str) or not proxy.startswith(("http://", "https://"))
    ):
        raise ValueError("Unsupported selector transport")
    return value


def checked(record):
    path = Path(record["path"])
    runtime = runtime_context()
    if "source_root" in runtime:
        path = ROOT / path.relative_to(runtime["source_root"])
        if not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Registered input escapes deployment root")
    if sha(path) != record["sha256"]:
        raise ValueError("Pinned research input/source changed")
    return path


def runtime_transport(config, runtime):
    if "https_proxy" in runtime:
        for name in ("ccxt_config", "ccxt_async_config"):
            options = config["exchange"].setdefault(name, {})
            options.pop("httpsProxy", None)
            if runtime["https_proxy"] is not None:
                options["httpsProxy"] = runtime["https_proxy"]
    return config


def rulebook(payload, exchange, config):
    policy = read(HERE / "policy.json")
    spec = importlib.util.spec_from_file_location(
        "selected_pinned_rulebook", checked(policy["rulebook"])
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RuleBook(payload, exchange, SimpleNamespace(), config)


def main():
    from deploy.nfi80_flatboost.launch import verify

    verify()
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(STATE / "selector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    now = datetime.now(UTC)
    day = pd.Timestamp(now).normalize()
    if SNAPSHOT.exists():
        current = read(SNAPSHOT)
        if (
            current["policy_sha256"] == sha(HERE / "policy.json")
            and current["decision_day_utc"] == day.date().isoformat()
            and now < datetime.fromisoformat(current["valid_until_utc"])
        ):
            print(json.dumps({"skipped": "same completed UTC day remains valid"}))
            return
    policy = read(HERE / "policy.json")
    seed = read(checked(policy["seed_schedule"]))
    blacklist = rapidjson.loads(checked(policy["blacklist_source"]).read_text(), parse_mode=3)[
        "exchange"
    ]["pair_blacklist"]
    identities = read(checked(policy["market_identity_source"]))["markets"]
    attempt = STATE / "selector" / now.strftime("%Y%m%dT%H%M%S%fZ")
    (attempt / "daily").mkdir(parents=True)
    runtime = runtime_context()
    config = runtime_transport(read(HERE / "optimized-config.json"), runtime)
    config.update(runmode=RunMode.DRY_RUN, candle_type_def=CandleType.FUTURES)
    exchange = ExchangeResolver.load_exchange(config, validate=False, load_leverage_tiers=False)
    try:
        markets = exchange.get_markets(
            quote_currencies=["USDT"], futures_only=True, active_only=True
        )
        eligible = [p for p in policy["candidates"] if p in markets]
        for pair in eligible:
            if (
                markets[pair]["id"] != identities[pair]["id"]
                or markets[pair]["info"]["onboardDate"] != identities[pair]["info"]["onboardDate"]
            ):
                raise ValueError("Contract identity changed; explicit review required")
        save(
            attempt / "market-identities.json",
            {
                p: {
                    "id": markets[p]["id"],
                    "onboardDate": markets[p]["info"]["onboardDate"],
                    "active": markets[p]["active"],
                }
                for p in eligible
            },
        )

        def daily(pair):
            record = seed["daily_inputs"][pair]
            frame = pd.read_parquet(checked(record))
            if frame.date.iloc[-1] != day - pd.Timedelta(days=1):
                with GuardedSession(max_wait=None) as session:
                    session.trust_env = False
                    response = session.get(
                        "https://fapi.binance.com/fapi/v1/klines",
                        params={
                            "symbol": markets[pair]["id"],
                            "interval": "1d",
                            "endTime": int(day.timestamp() * 1000) - 1,
                            "limit": 65,
                        },
                        proxies={"https": runtime.get("https_proxy", "http://127.0.0.1:7897")},
                        timeout=25,
                    )
                    response.raise_for_status()
                    raw = response.json()
                save(attempt / "daily" / (markets[pair]["id"] + ".raw.json"), raw)
                frame = pd.DataFrame(
                    {
                        "date": pd.to_datetime([r[0] for r in raw], unit="ms", utc=True),
                        "close_time": pd.to_datetime([r[6] for r in raw], unit="ms", utc=True),
                        **{
                            name: [float(r[index]) for r in raw]
                            for name, index in (
                                ("open", 1),
                                ("high", 2),
                                ("low", 3),
                                ("close", 4),
                                ("volume", 5),
                                ("quote_volume", 7),
                            )
                        },
                    }
                )
            onboard = pd.to_datetime(
                int(markets[pair]["info"]["onboardDate"]), unit="ms", utc=True
            ).normalize()
            frame = frame.loc[(frame.date >= onboard) & (frame.close_time < day)].tail(65)
            if (
                frame.empty
                or frame.date.iloc[-1] != day - pd.Timedelta(days=1)
                or frame.date.duplicated().any()
                or not frame.date.is_monotonic_increasing
                or not (frame.close_time < day).all()
            ):
                raise ValueError("Missing/noncausal daily input; do not silently rerank")
            path = attempt / "daily" / (markets[pair]["id"] + ".parquet")
            frame.to_parquet(path, index=False)
            return pair, {"path": str(path), "sha256": sha(path)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            inputs = dict(pool.map(daily, eligible))
        payload = {
            "variant": "structural",
            "daily_inputs": inputs,
            "structural_blacklist": blacklist,
        }
        row = rulebook(payload, exchange, config).select(day)
        if not row["admitted"] or len(row["pairs"]) != 80:
            raise ValueError("Native input rulebook returned fewer than80 valid names")
        save(attempt / "selection-inputs.json", payload)
        save(attempt / "selection-result.json", row)
        snapshot = {
            "pairs": row["pairs"],
            "refresh_period": 60,
            "snapshot_id": attempt.name,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "valid_until_utc": (day + pd.Timedelta(days=1)).isoformat(),
            "decision_day_utc": day.date().isoformat(),
            "selection_returncode": 0,
            "policy_sha256": sha(HERE / "policy.json"),
            "latest_input_close": row["latest_input_close"],
            "selector_attempt": str(attempt),
            "volume_basis": (
                "Previous completed UTC day actual quote turnover; "
                "same pinned historical rulebook; local input extension"
            ),
        }
        save(attempt / "selected-snapshot.json", snapshot)
        save(SNAPSHOT, snapshot)
        save(
            HERE / "latest-selector-receipt.json",
            {**snapshot, "snapshot_sha256": sha(SNAPSHOT), "real_orders_enabled": False},
        )
        print(
            json.dumps(
                {
                    "published": True,
                    "pairs": 80,
                    "decision_day_utc": snapshot["decision_day_utc"],
                    "eligible_before_ranking": row["eligible_after_risk"],
                }
            )
        )
    finally:
        exchange.close()


if __name__ == "__main__":
    main()
