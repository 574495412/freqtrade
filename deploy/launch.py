"""Verify a pinned paper release, then exec the official Freqtrade CLI."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]


def verify() -> dict:
    release = json.loads((ROOT / "deploy/release.json").read_text())
    upstream = json.loads((ROOT / "deploy/upstream-source.json").read_text())
    if release["mode"] != "dry_run" or release["production_eligible"] is not False:
        raise ValueError("This deployment is paper-only")
    if upstream["commit"] != release["upstream_commit"]:
        raise ValueError("Upstream source identity mismatch")
    upstream_files = {k: v for k, v in upstream["files"].items()
                      if k not in release.get("upstream_metadata_exceptions", [])}
    for group in (upstream_files, release["files"]):
        for name, expected in group.items():
            path = ROOT / name
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError("Source escapes deployment root")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Release file differs: " + name)
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market", choices=("spot", "futures"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    release = verify()
    if args.verify:
        print(json.dumps({"verified": True, "mode": release["mode"],
                          "upstream_commit": release["upstream_commit"]}))
        return
    state = ROOT / "user_data/deployment" / args.market
    if not (state / "migration.json").is_file():
        raise ValueError("Paper ledger migration receipt is missing")
    config_path = ROOT / "deploy/configs" / (args.market + ".json")
    config = json.loads(config_path.read_text())
    if config.get("dry_run") is not True or config.get("trading_mode") != args.market:
        raise ValueError("Paper market contract mismatch")
    secrets = ROOT / "user_data/deployment/.env.notifications"
    if secrets.stat().st_mode & 0o077:
        raise ValueError("Notification file must have permissions 600")
    values = {}
    for line in secrets.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key != "WECHAT_WORK_WEBHOOK":
            raise ValueError("Unexpected notification setting")
        values[key] = shlex.split(value)[0]
    url = values["WECHAT_WORK_WEBHOOK"]
    if not url.startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="):
        raise ValueError("Invalid notification endpoint")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FREQTRADE__", "BINANCE_", "OKX_", "WECHAT_WORK_"))}
    env.update(FREQTRADE__WEBHOOK__URL=url, PYTHONUNBUFFERED="1",
               PYTHONDONTWRITEBYTECODE="1")
    os.umask(0o077)
    descriptor = os.open(state / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(descriptor, True)
    os.chdir(ROOT)
    python = str(ROOT / ".venv-paper/bin/python")
    # No CZ imports or execution adapter: official CLI owns the entire lifecycle.
    os.execve(python, [python, "-m", "freqtrade", "trade", "--dry-run",
                      "--config", str(config_path),
                      "--strategy-path", str(ROOT / "deploy/strategy/nfi-x8"),
                      "--userdir", str(state),
                      "--db-url", "sqlite:///" + str(state / "trades.sqlite"),
                      "--logfile", str(state / "freqtrade.log")], env)


if __name__ == "__main__":
    main()
