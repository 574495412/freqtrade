"""Supervise the existing user-authorized live workers through launchd only."""

import argparse
import os

from deploy.mainnet_100_20261109.manual import worker
from deploy.mainnet_100_20261109.prepare import ROOT, STATE


def definition(market):
    if market not in {"spot", "futures"}:
        raise ValueError("Unknown existing live market")
    return {
        "Label": "io.freqtrade.live." + market,
        "ProgramArguments": [
            str(ROOT / ".venv-paper/bin/python"),
            "-B",
            "-m",
            "deploy.runtime_recovery_20261111.service",
            market,
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 60,
        "ExitTimeOut": 60,
        "StandardOutPath": str(STATE / market / "service.stdout.log"),
        "StandardErrorPath": str(STATE / market / "service.stderr.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market", choices=["spot", "futures"])
    args = parser.parse_args()
    os.umask(0o077)
    for key in list(os.environ):
        if key.startswith(("FREQTRADE__", "BINANCE_", "OKX_", "WECHAT_WORK_")):
            del os.environ[key]
    # Native worker retains source/config verification and the exclusive per-DB
    # lock. Source/config validation failures remain closed on every restart.
    worker(args.market)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Live service stopped: " + type(error).__name__) from None
