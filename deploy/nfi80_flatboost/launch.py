"""Launch independent, credential-free native Freqtrade paper comparison accounts."""

import argparse
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT / "user_data/deployment/nfi80-flatboost-20261109"
ROLES = {"optimized": "NFIFlatTopBoostPaper", "control": "NFIX8PaperControl"}


def read(path):
    return json.loads(path.read_text())


def verify():
    sys.path.insert(0, str(ROOT))
    try:
        from deploy.launch import verify as verify_base

        base = verify_base()
    finally:
        sys.path.pop(0)
    release = read(HERE / "release.json")
    if (
        release["mode"] != "dry_run"
        or release["real_orders_enabled"] is not False
        or release["upstream_commit"] != base["upstream_commit"]
    ):
        raise ValueError("Paper-only release identity mismatch")
    for name, digest in release["files"].items():
        path = HERE / name
        if path.is_symlink() or not path.resolve().is_relative_to(HERE):
            raise ValueError("Release source must remain inside its directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Paper release changed: " + name)
    if read(HERE / "parity-result.json")["passed"] is not True:
        raise ValueError("Native paper adapter parity must pass first")
    return release


def validate_paper(config, role):
    if (
        config.get("dry_run") is not True
        or config["strategy"] != ROLES[role]
        or config["trading_mode"] != "futures"
        or config["margin_mode"] != "isolated"
        or config["dry_run_wallet"] != 1000
        or config["max_open_trades"] != 12
        or config["stake_amount"] != "unlimited"
        or config["tradable_balance_ratio"] != 0.99
        or config["force_entry_enable"] is not False
        or len(config["exchange"]["pair_whitelist"]) != 80
        or any(config["exchange"].get(k) for k in ("key", "secret", "password"))
        or set(config["nfi_parameters"].values()) != {3.0}
    ):
        raise ValueError("Unexpected paper financial contract")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    verify()
    config = read(HERE / f"{args.role}-config.json")
    validate_paper(config, args.role)
    if args.verify:
        print(
            json.dumps(
                {
                    "verified": True,
                    "role": args.role,
                    "dry_run": True,
                    "strategy": config["strategy"],
                    "pairs": 80,
                    "leverage": 3,
                    "max_open_trades": 12,
                }
            )
        )
        return
    os.umask(0o077)
    state = STATE / args.role
    if not state.is_dir() or state.is_symlink():
        raise ValueError("Prepared independent paper state is required")
    runtime_path = state / "runtime.private.json"
    if runtime_path.is_symlink() or runtime_path.stat().st_mode & 0o077:
        raise ValueError("Private runtime file must be owned and mode600")
    runtime = read(runtime_path)
    if set(runtime) != {"api_server", "paper_entry_start", "webhook"}:
        raise ValueError("Runtime overlay cannot override financial settings")
    if runtime["api_server"]["listen_ip_address"] != "127.0.0.1":
        raise ValueError("Paper REST API must remain loopback-only")
    if not runtime["webhook"]["url"].startswith("http://127.0.0.1:18789/events/"):
        raise ValueError("Use the existing local notification relay")
    db = state / "trades.sqlite"
    if db.is_symlink():
        raise ValueError("Paper database cannot be a symlink")
    lock = os.open(state / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(lock, True)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    python = str(ROOT / ".venv-paper/bin/python")
    os.chdir(ROOT)
    os.execve(  # noqa: S606 -- fixed interpreter and official paper CLI, no shell
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
            str(runtime_path),
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
