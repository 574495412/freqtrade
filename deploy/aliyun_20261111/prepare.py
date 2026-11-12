"""Prepare a private Linux migration candidate; never stop/start workers or copy keys."""

import copy
import os
from pathlib import Path

from deploy.aliyun_20261111.systemd import slice_unit, timer_unit, unit
from deploy.mainnet_100_20261109.capital import wallet_class
from deploy.mainnet_100_20261109.manual import verify
from deploy.mainnet_100_20261109.prepare import ROOT, STATE
from deploy.nfi80_selected.selector import read, save, sha


REMOTE_ROOT = Path("/opt/freqtrade")


def rebase(value, source, target):
    if isinstance(value, dict):
        return {k: rebase(v, source, target) for k, v in value.items()}
    if isinstance(value, list):
        return [rebase(v, source, target) for v in value]
    if isinstance(value, str):
        return value.replace(str(source), str(target))
    return value


def linux_config(config, source=ROOT, target=REMOTE_ROOT):
    wallet_class(config)
    if (
        config.get("dry_run") is not False
        or config.get("initial_state") != "stopped"
        or config.get("strategy") != "NFIManualFlatBoost"
    ):
        raise ValueError("Unexpected existing live account contract")
    result = rebase(copy.deepcopy(config), source, target)
    # Server connectivity is a separate acceptance gate. A macOS loopback proxy
    # must never silently become the server's exchange transport.
    for name in ("ccxt_config", "ccxt_async_config"):
        options = result["exchange"].get(name, {})
        for key in ("httpsProxy", "httpProxy", "socksProxy", "wsProxy", "wssProxy"):
            options.pop(key, None)
    return result


def prepare(destination, remote_root=REMOTE_ROOT):
    verify()
    os.umask(0o077)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    (destination / "freqtrade-live.slice").write_text(slice_unit())
    configs = {}
    for market in ("spot", "futures"):
        configs[market] = linux_config(read(STATE / market / "config.json"), target=remote_root)
        save(destination / (market + "-config.private.json"), configs[market])
        (destination / ("freqtrade-live-" + market + ".service")).write_text(
            unit(market, "deploy.mainnet_100_20261109.manual", "--worker " + market, remote_root)
        )
    for name, module in (
        ("notifications", "deploy.live_prepare_20261109.notify_relay"),
        ("selector", "deploy.mainnet_100_20261109.live_selector"),
    ):
        (destination / ("freqtrade-live-" + name + ".service")).write_text(
            unit(name, module, root=remote_root)
        )
    (destination / "freqtrade-live-selector.timer").write_text(timer_unit())
    base = read(ROOT / "deploy/release.json")
    base["request_guard_policy"]["private_path"] = str(
        remote_root / "user_data/deployment/binance-request-guard.json"
    )
    save(destination / "base-release.candidate.json", base)
    release = read(ROOT / "deploy/mainnet_100_20261109/release.json")
    release["config_sha256"] = {m: sha(destination / (m + "-config.private.json")) for m in configs}
    release["operational_mode"] = "linux_candidate_not_started"
    save(destination / "manual-release.candidate.json", release)
    save(
        destination / "selector-runtime.private.json",
        {
            "source_root": str(ROOT),
            "https_proxy": None,
        },
    )
    save(
        destination / "preparation.json",
        {
            "target_root": str(remote_root),
            "source_config_sha256": {m: sha(STATE / m / "config.json") for m in configs},
            "private_configs_contain_existing_notification_token": True,
            "exchange_credentials_copied": False,
            "ledgers_copied": False,
            "workers_started": 0,
            "workers_stopped": 0,
            "orders_submitted": 0,
            "status": "PREPARED_PENDING_SERVER_CONNECTIVITY_AND_AUTHENTICATED_CUTOVER",
        },
    )
    return destination
