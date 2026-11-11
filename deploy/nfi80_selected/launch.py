"""Verify and execute the official CLI for an isolated daily-pool paper cohort."""
# ruff: noqa: RUF001 -- Chinese notification punctuation.

import argparse
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT / "user_data/deployment/nfi80-selected-20261109"
ROLES = {"optimized": "NFISelectedFlatBoost"}


def read(path):
    return json.loads(Path(path).read_text())


def verify():
    sys.path.insert(0, str(ROOT))
    try:
        from deploy.nfi80_flatboost.launch import verify as parent_verify
        parent_verify()
    finally:
        sys.path.pop(0)
    release = read(HERE / "release.json")
    if release["mode"] != "dry_run" or release["real_orders_enabled"] is not False:
        raise ValueError("This experiment is paper-only")
    for name, digest in release["files"].items():
        path = HERE / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(HERE)
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Daily release source changed: " + name)
    return release


def validate(config, role):
    from deploy.nfi80_selected.selector import SNAPSHOT, sha

    if (
        config.get("dry_run") is not True
        or config["strategy"] != ROLES[role]
        or config["trading_mode"] != "futures"
        or config["margin_mode"] != "isolated"
        or config["dry_run_wallet"] != 1000
        or config["max_open_trades"] != 12
        or config["fee"] != 0.0008
        or config["stake_amount"] != "unlimited"
        or config["tradable_balance_ratio"] != 0.99
        or config["force_entry_enable"] is not False
        or any(config["exchange"].get(k) for k in ("key", "secret", "password"))
        or set(config["nfi_parameters"].values()) != {3.0}
        or config["daily_snapshot_file"] != str(SNAPSHOT)
        or config["daily_policy_sha256"] != sha(HERE / "policy.json")
        or config["pairlists"]
        != [
            {
                "method": "RemotePairList",
                "number_assets": 80,
                "pairlist_url": "file:///" + str(SNAPSHOT),
                "refresh_period": 60,
                "keep_pairlist_on_failure": True,
            }
        ]
    ):
        raise ValueError("Daily paper financial or snapshot contract mismatch")


def selector_job():
    import requests

    from deploy.nfi80_selected.selector import SNAPSHOT
    from deploy.nfi80_selected.selector import main as select

    previous = read(SNAPSHOT) if SNAPSHOT.exists() else {}
    try:
        select()
        current = read(SNAPSHOT)
        if current["snapshot_id"] == previous.get("snapshot_id"):
            return
        entered = len(set(current["pairs"]) - set(previous.get("pairs", [])))
        message = (
            f"[NFI80收益优先日更模拟选币] 更新成功：80币，新增{entered}币。"
            "仅影响新开仓，持仓沿用原策略退出。"
        )
    except Exception as exc:
        message = (
            "[NFI80收益优先日更模拟选币] 更新失败："
            + type(exc).__name__
            + "。旧名单到期后将暂停新开仓，已有持仓继续管理；请检查本地选择器日志。"
        )
        raise_after = exc
    else:
        raise_after = None
    url = read(STATE / "optimized/runtime.private.json")["webhook"]["url"]
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url, json={"msgtype": "text", "text": {"content": message}}, timeout=5
        )
        response.raise_for_status()
    if raise_after:
        raise raise_after


def main():
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=[*ROLES, "selector"])
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify()
    if args.role == "selector":
        if not args.verify:
            selector_job()
        return
    config = read(HERE / f"{args.role}-config.json")
    validate(config, args.role)
    if args.verify:
        print(
            json.dumps(
                {
                    "verified": True,
                    "role": args.role,
                    "dry_run": True,
                    "strategy": config["strategy"],
                    "pool_size": 80,
                    "leverage": 3,
                    "max_open_trades": 12,
                }
            )
        )
        return
    os.umask(0o077)
    state = STATE / args.role
    private = state / "runtime.private.json"
    if state.is_symlink() or private.is_symlink() or private.stat().st_mode & 0o077:
        raise ValueError("Use private, independent paper state")
    overlay = read(private)
    if (
        set(overlay) != {"api_server", "paper_entry_start", "webhook"}
        or overlay["api_server"]["listen_ip_address"] != "127.0.0.1"
        or not overlay["webhook"]["url"].startswith("http://127.0.0.1:18789/events/")
    ):
        raise ValueError("Runtime overlay may not override financial rules")
    db = state / "trades.sqlite"
    if db.is_symlink():
        raise ValueError("Independent database required")
    lock = os.open(state / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(lock, True)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    python = str(ROOT / ".venv-paper/bin/python")
    os.chdir(ROOT)
    os.execve(  # noqa: S606 -- fixed interpreter and official paper CLI
        python,
        [
            python,
            "-m",
            "freqtrade",
            "trade",
            "--dry-run",
            "--config",
            str(HERE / f"{args.role}-config.json"),
            "--config",
            str(private),
            "--strategy-path",
            str(HERE / "strategy"),
            "--userdir",
            str(state),
            "--db-url",
            "sqlite:///" + str(db),
            "--logfile",
            str(state / "freqtrade.log"),
        ],
        env,
    )


if __name__ == "__main__":
    main()
