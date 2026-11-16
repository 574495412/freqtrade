"""Low-memory backend of the existing local structural daily-input publisher.

The pandas/CCXT selector and pinned RuleBook remain the parity reference. This
backend prepares public inputs, not accounts or orders. Policy bytes, pool size,
ranking, structural blacklist, identity and closed-UTC admission stay unchanged.
"""

import fcntl
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import rapidjson

from deploy.nfi80_selected import selector
from freqtrade.request_guard_http import GuardedSession


DAY_MS = 86_400_000
REFERENCE_RULEBOOK_SHA256 = "a8d1cf260ce2c6965ac06f6247eae60826939e498ddcfcf2ba6bf422d33198e2"


def eligible_markets(payload, candidates, identities):
    """Project only pinned USDT contracts using the pinned CCXT/native predicates."""
    symbols = payload["symbols"]
    by_id = {v["symbol"]: v for v in symbols}
    if len(by_id) != len(symbols):
        raise ValueError("Duplicate exchange contract identity")
    result = {}
    for pair in candidates:
        frozen = identities[pair]
        market = by_id.get(frozen["id"])
        if market is None:
            continue
        # Equivalent to CCXT Binance swap/linear/active + native tradable filters.
        swap = (
            market.get("contractType") == "PERPETUAL"
            or market.get("deliveryDate", market.get("expiryDate")) == 4133404800000
        )
        active = market.get("status", market.get("contractStatus")) == "TRADING"
        if "tradability" in market:
            raise ValueError("Unreviewed market classification")
        ticks = [
            v["tickSize"] for v in market.get("filters", []) if v["filterType"] == "PRICE_FILTER"
        ]
        if len(ticks) > 1:
            raise ValueError("Duplicate price filter")
        if ticks and (not math.isfinite(float(ticks[0])) or float(ticks[0]) <= 1e-11):
            continue
        if not (
            swap
            and "contractType" in market
            and active
            and market.get("quoteAsset") == "USDT"
            and market.get("marginAsset") == "USDT"
            and market.get("baseAsset")
        ):
            continue
        if (
            market["symbol"] != frozen["id"]
            or market["onboardDate"] != frozen["info"]["onboardDate"]
            or market["baseAsset"] != frozen["info"]["baseAsset"]
        ):
            raise ValueError("Contract identity changed; explicit review required")
        result[pair] = market
    return result


def daily_summary(raw, onboard_ms, day_ms):
    """Same onboard/closed/tail/date gates as the reference pandas preparation."""
    onboard_day = int(onboard_ms) // DAY_MS * DAY_MS
    rows = [r for r in raw if int(r[0]) >= onboard_day and int(r[6]) < day_ms][-65:]
    if not rows:
        raise ValueError("Missing closed daily input")
    dates = [int(r[0]) for r in rows]
    if (
        dates != sorted(set(dates))
        or dates[-1] != day_ms - DAY_MS
        or any(int(r[6]) >= day_ms for r in rows)
    ):
        raise ValueError("Missing/noncausal daily input; do not silently rerank")
    # Validate the same numerical columns read by the reference implementation.
    for row in rows:
        if not all(math.isfinite(float(row[i])) for i in [1, 2, 3, 4, 5, 7]):
            raise ValueError("Nonfinite daily input")
    return {
        "count": len(rows),
        "date_ms": dates[-1],
        "close_ms": int(rows[-1][6]),
        "quote_volume": float(rows[-1][7]),
    }


def structural_selection(summaries, blacklist, day_ms):
    """Exact structural branch of the pinned RuleBook.select; no risk variant."""
    blocked = {
        p
        for pattern in blacklist
        for p in summaries
        if re.compile(pattern, re.IGNORECASE).fullmatch(p)
    }
    ranks, closes = [], []
    for pair, value in summaries.items():
        if pair in blocked or value["count"] < 60:
            continue
        if value["date_ms"] != day_ms - DAY_MS or value["quote_volume"] <= 0:
            continue
        ranks.append((pair, value["quote_volume"]))
        closes.append(value["close_ms"])
    ranks.sort(key=lambda item: (-item[1], item[0]))
    if not ranks:
        raise ValueError("No eligible closed daily inputs")
    latest = datetime.fromtimestamp(max(closes) / 1000, UTC).isoformat()
    return {
        "pairs": [p for p, _ in ranks[:80]],
        "admitted": len(ranks) >= 80,
        "eligible_before_risk": len(ranks),
        "eligible_after_risk": len(ranks),
        "latest_input_close": latest,
        "ranked_quote_volume": dict(ranks),
        "risk_exclusions": {},
        "risk_values": {},
    }


def prepare_daily(pair, *, deadline, seed_inputs, markets, cache, day_ms, get):
    if time.monotonic() >= deadline:
        raise TimeoutError("Closed-input preparation deadline")
    selector.checked(seed_inputs[pair])
    market = markets[pair]
    path = cache / (market["symbol"] + ".json")
    if path.is_symlink():
        raise ValueError("Closed-input cache cannot be a symlink")
    if path.exists():
        record = selector.read(path)
        if (
            record["day_ms"] != day_ms
            or record["onboardDate"] != market["onboardDate"]
            or record["symbol"] != market["symbol"]
        ):
            raise ValueError("Closed-input cache identity differs")
        raw = record["raw"]
    else:
        raw = get(
            "klines",
            {
                "symbol": market["symbol"],
                "interval": "1d",
                "endTime": day_ms - 1,
                "limit": 65,
            },
        )
    summary = daily_summary(raw, market["onboardDate"], day_ms)
    if not path.exists():
        selector.save(
            path,
            {
                "day_ms": day_ms,
                "symbol": market["symbol"],
                "onboardDate": market["onboardDate"],
                "raw": raw,
            },
        )
    return pair, summary, {"path": str(path), "sha256": selector.sha(path)}


def pinned_inputs(policy):
    # Hash original archives without deserializing years of rankings or
    # unrelated markets. The release-pinned projection is checked against
    # those exact original bytes by the parity validation.
    projection = selector.read(selector.HERE / "input-projection.json")
    if projection["schema"] != "nfi-selected-input-projection.v1" or projection[
        "policy_sha256"
    ] != selector.sha(selector.HERE / "policy.json"):
        raise ValueError("Input projection policy differs")
    if (
        set(projection["source_sha256"]) != {"rulebook", "seed_schedule", "market_identity_source"}
        or set(projection["daily_inputs"]) != set(policy["candidates"])
        or set(projection["markets"]) != set(policy["candidates"])
    ):
        raise ValueError("Input projection coverage differs")
    for name, digest in projection["source_sha256"].items():
        if digest != policy[name]["sha256"]:
            raise ValueError("Input projection source differs")
        selector.checked(policy[name])
    seed_inputs = projection["daily_inputs"]
    blacklist = rapidjson.loads(
        selector.checked(policy["blacklist_source"]).read_text(),
        parse_mode=3,
    )["exchange"]["pair_blacklist"]
    identities = projection["markets"]
    return seed_inputs, blacklist, identities


def execution_deadline():
    budget = 135
    if selector.ROOT == Path("/opt/freqtrade"):
        budget = min(budget, 260 - time.time() % 300)
        if budget <= 3:
            raise TimeoutError("Outside bounded selector execution window")
    return time.monotonic() + budget


def main(*, force_refresh=False, publish=True, now=None):
    from deploy.mainnet_100_20261109.manual import verify

    verify()
    os.umask(0o077)
    if now is not None and publish:
        raise ValueError("Injected clocks are for nonpublishing verification only")
    now = now or datetime.now(UTC)
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_ms = int(day.timestamp() * 1000)
    policy = selector.read(selector.HERE / "policy.json")
    if policy["rulebook"]["sha256"] != REFERENCE_RULEBOOK_SHA256:
        raise ValueError("Unreviewed reference rulebook")
    selector.checked(policy["rulebook"])
    selector.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(selector.STATE / "selector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if selector.SNAPSHOT.exists() and not force_refresh:
            current = selector.read(selector.SNAPSHOT)
            if (
                current["policy_sha256"] == selector.sha(selector.HERE / "policy.json")
                and current["decision_day_utc"] == day.date().isoformat()
                and now < datetime.fromisoformat(current["valid_until_utc"])
            ):
                return {"skipped": "same completed UTC day remains valid"}
        seed_inputs, blacklist, identities = pinned_inputs(policy)
        runtime = selector.runtime_context()
        # Verification may replay the same decision clock more than once. Use
        # the real invocation time for attempt identity, not the injected day.
        attempt = selector.STATE / "selector-light" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        cache = selector.STATE / "closed-daily-cache" / day.date().isoformat()
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        attempt.mkdir(mode=0o700, parents=True)
        deadline = execution_deadline()

        def get(path, params=None):
            remaining = deadline - time.monotonic()
            if remaining <= 3:
                raise TimeoutError("Closed-input preparation deadline")
            with GuardedSession(max_wait=min(10, remaining - 2)) as session:
                session.trust_env = False
                response = session.get(
                    "https://fapi.binance.com/fapi/v1/" + path,
                    params=params,
                    proxies={"https": runtime.get("https_proxy", "http://127.0.0.1:7897")},
                    timeout=(3, min(20, remaining)),
                    allow_redirects=False,
                )
                response.raise_for_status()
                return response.json()

        exchange_info = get("exchangeInfo")
        markets = eligible_markets(exchange_info, policy["candidates"], identities)
        selector.save(attempt / "exchange-info.json", exchange_info)
        selector.save(
            attempt / "market-identities.json",
            {
                p: {"id": m["symbol"], "onboardDate": m["onboardDate"], "active": True}
                for p, m in markets.items()
            },
        )

        def daily(pair):
            return prepare_daily(
                pair,
                deadline=deadline,
                seed_inputs=seed_inputs,
                markets=markets,
                cache=cache,
                day_ms=day_ms,
                get=get,
            )

        # Completed pair inputs survive bounded/failed attempts; retries need only
        # missing pairs. Every retry repeats source and current-market identity gates.
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(daily, markets))
        summaries = {p: s for p, s, _ in values}
        inputs = {p: r for p, _, r in values}
        row = structural_selection(summaries, blacklist, day_ms)
        if not row["admitted"] or len(row["pairs"]) != 80:
            raise ValueError("Reference structural rule returned fewer than80 valid names")
        selector.save(
            attempt / "selection-inputs.json",
            {
                "variant": "structural",
                "backend": "bounded-public-json-v1",
                "daily_inputs": inputs,
                "structural_blacklist": blacklist,
                "summaries": summaries,
                "reference_rulebook_sha256": REFERENCE_RULEBOOK_SHA256,
            },
        )
        selector.save(attempt / "selection-result.json", row)
        snapshot = {
            "pairs": row["pairs"],
            "refresh_period": 60,
            "snapshot_id": attempt.name,
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "valid_until_utc": (day + timedelta(days=1)).isoformat(),
            "decision_day_utc": day.date().isoformat(),
            "selection_returncode": 0,
            "policy_sha256": selector.sha(selector.HERE / "policy.json"),
            "latest_input_close": row["latest_input_close"],
            "selector_attempt": str(attempt),
            "volume_basis": "Previous completed UTC day actual quote turnover; "
            "parity-checked pinned structural rulebook; local input extension",
        }
        selector.save(attempt / "selected-snapshot.json", snapshot)
        if publish:
            if datetime.now(UTC).date() != day.date():
                raise ValueError("UTC day changed during preparation; retry fresh day")
            selector.save(selector.SNAPSHOT, snapshot)
            selector.save(
                selector.HERE / "latest-selector-receipt.json",
                {
                    **snapshot,
                    "snapshot_sha256": selector.sha(selector.SNAPSHOT),
                    "real_orders_enabled": False,
                },
            )
        result = {
            "published": publish,
            "pairs": 80,
            "attempt": str(attempt),
            "decision_day_utc": snapshot["decision_day_utc"],
            "backend": "bounded-public-json-v1",
        }
        print(json.dumps(result), flush=True)
        return result
    finally:
        os.close(fd)
