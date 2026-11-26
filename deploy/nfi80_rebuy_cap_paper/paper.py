"""Prepare and start the candidate through Freqtrade's isolated dry-run CLI."""

import argparse
import fcntl
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from deploy.nfi80_selected.selector import read, sha
from deploy.nfi80_selected.strategy.NFISelectedPaper import snapshot_pairs


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT / "user_data/deployment/nfi80-rebuy-cap-paper-20261126"
ENGINE = STATE / "engine"
ACCOUNT = STATE / "account"
CONFIG = ACCOUNT / "config.json"
SNAPSHOT = STATE / "selection/current-pairs.json"
ENGINE_REVISION = "80917bd14"
STRATEGY = "NFISelectedRebuyCapPaper"


def configuration(start):
    source = ENGINE / "deploy/nfi80_selected"
    config = read(source / "optimized-config.json")
    for field in ("api_server", "telegram", "webhook"):
        config.pop(field, None)
    config.update(
        strategy=STRATEGY,
        max_open_trades=14,
        bot_name="NFI80-14slots-rebuy-cap-paper",
        paper_entry_start=start,
        daily_snapshot_file=str(SNAPSHOT),
    )
    # RemotePairList strips its file:/// prefix before building the local Path.
    config["pairlists"][0]["pairlist_url"] = "file:///" + str(SNAPSHOT)
    if (
        config["dry_run"] is not True
        or config["initial_state"] != "running"
        or config["force_entry_enable"] is not False
        or config["daily_policy_sha256"] != sha(source / "policy.json")
        or any(config["exchange"].get(key) for key in ("key", "apiKey", "secret", "password"))
    ):
        raise ValueError("The pinned baseline must be a credential-free paper profile")
    return config


def verify():
    revision = subprocess.check_output(
        ["git", "rev-parse", "--short=9", "HEAD"], cwd=ENGINE, text=True,
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ENGINE, text=True,
    )
    if revision != ENGINE_REVISION or dirty:
        raise ValueError("The isolated engine must match the clean pinned revision")
    for relative in (
        "deploy/nfi80_selected/strategy/NFISelectedPaper.py",
        "deploy/nfi80_flatboost/strategy/NFIFlatTopBoostPaper.py",
        "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py",
    ):
        if sha(ROOT / relative) != sha(ENGINE / relative):
            raise ValueError(f"Paper strategy dependency differs from pinned checkout: {relative}")


def require_snapshot(config):
    now = datetime.now(UTC)
    if not snapshot_pairs(SNAPSHOT, now, config["daily_policy_sha256"]):
        raise ValueError("A fresh isolated 80-pair snapshot is required")


def refresh_snapshot():
    """Run in the pinned checkout so the native selector verifies that release."""
    import freqtrade
    from deploy.mainnet_100_20261109 import manual
    from deploy.nfi80_selected import light_selector, selector

    if Path(freqtrade.__file__).resolve() != ENGINE / "freqtrade/__init__.py":
        raise ValueError("Selector must run from the pinned isolated engine")
    original = STATE.parent / "binance-request-guard.json"
    policy = ENGINE / "user_data/deployment" / original.name
    policy.parent.mkdir(parents=True, exist_ok=True)
    if not policy.exists():
        with policy.open("xb") as stream:
            stream.write(original.read_bytes())
    if policy.is_symlink() or policy.stat().st_mode & 0o077 or sha(policy) != sha(original):
        raise ValueError("The isolated selector request policy differs from the verified policy")

    manual.STATE = STATE.parent / "mainnet-100-20261109"
    selector.STATE = SNAPSHOT.parent
    selector.SNAPSHOT = SNAPSHOT
    result = light_selector.main(publish=False)
    if "attempt" in result:
        source = Path(result["attempt"]) / "selected-snapshot.json"
        selector.save(SNAPSHOT, selector.read(source))
    require_snapshot(configuration(datetime.now(UTC).isoformat()))
    print(f"{SNAPSHOT} {sha(SNAPSHOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("refresh", "prepare", "run"))
    args = parser.parse_args()
    os.umask(0o077)
    verify()
    python = str(ROOT / ".venv-paper/bin/python")
    if args.mode == "refresh":
        entry = 'import runpy,sys; runpy.run_path(sys.argv[1])["refresh_snapshot"]()'
        subprocess.run([python, "-B", "-c", entry, __file__], cwd=ENGINE, check=True)
        return
    if args.mode == "prepare":
        config = configuration(datetime.now(UTC).isoformat())
        require_snapshot(config)
        ACCOUNT.mkdir(mode=0o700, exist_ok=True)
        with CONFIG.open("x") as out:
            out.write(json.dumps(config, indent=2) + "\n")
        print(CONFIG)
        return

    config = read(CONFIG)
    if config != configuration(config["paper_entry_start"]):
        raise ValueError("Effective paper configuration differs from the prepared profile")
    require_snapshot(config)
    db = ACCOUNT / "trades.sqlite"
    if ACCOUNT.is_symlink() or CONFIG.is_symlink() or db.is_symlink():
        raise ValueError("The paper account requires an independent ledger")
    lock = os.open(ACCOUNT / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(lock, True)
    allowed_env = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
    env = {key: os.environ[key] for key in allowed_env if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    os.chdir(ENGINE)
    os.execve(  # noqa: S606 -- pinned interpreter and native dry-run CLI
        python,
        [python, "-m", "freqtrade", "trade", "--dry-run", "--config", str(CONFIG),
         "--userdir", str(ACCOUNT), "--strategy-path", str(HERE / "strategy"),
         "--db-url", "sqlite:///" + str(db), "--logfile", str(ACCOUNT / "freqtrade.log")],
        env,
    )


if __name__ == "__main__":
    main()
