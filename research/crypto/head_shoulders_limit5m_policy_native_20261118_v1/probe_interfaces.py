"""Guarded, frozen object/interface probe. No account or market-result execution."""

import hashlib
import json
import resource
import shutil
import socket
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
OWNER = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    with (HERE / name).open("x") as stream:
        json.dump(value, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")


def verify():
    freeze = json.loads((HERE / "implementation_freeze.json").read_text())
    for path, digest in freeze["files"].items():
        if sha(path) != digest:
            raise ValueError("Frozen file changed: " + path)
    return len(freeze["files"])


def resources():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss = value if sys.platform == "darwin" else value * 1024
    free = shutil.disk_usage(HERE).free
    if rss >= 2 * 1024**3 or free < 1024**3:
        raise RuntimeError("Object probe resource guard failed")
    return {"peak_rss_bytes": rss, "free_disk_bytes": free}


def forbidden(*args, **kwargs):
    raise RuntimeError("Forbidden account/financial/exchange/network operation in interface probe")


def main():
    started = time.monotonic()
    resources()
    checked = verify()
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(OWNER))
    import freqtrade
    from freqtrade.exchange import Exchange
    from freqtrade.optimize.backtesting import Backtesting
    from freqtrade.wallets import Wallets

    if Path(freqtrade.__file__).resolve() != OWNER / "freqtrade/__init__.py":
        raise RuntimeError("Wrong official Freqtrade owner")
    with (
        patch.object(Backtesting, "__init__", forbidden),
        patch.object(Backtesting, "start", forbidden),
        patch.object(Backtesting, "backtest", forbidden),
        patch.object(Wallets, "__init__", forbidden),
        patch.object(Exchange, "__init__", forbidden),
        patch.object(socket.socket, "connect", forbidden),
    ):
        from test_interfaces import run

        result = run()
    result.update(
        started_by=".venv-paper/bin/python -B probe_interfaces.py",
        finished_at=datetime.now(UTC).isoformat(),
        seconds=time.monotonic() - started,
        frozen_files_before=checked,
        frozen_files_after=verify(),
        implementation_freeze_sha256=sha(HERE / "implementation_freeze.json"),
        resources=resources(),
        guards="Backtesting init/start/backtest, Wallets/Exchange init, network connect",
    )
    save("interface_validation.json", result)
    print(
        json.dumps(
            {
                key: result[key]
                for key in ["passed", "case_count", "seconds", "financial_accounts", "resources"]
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        save(
            "failed_probe.json",
            {
                "passed": False,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "at": datetime.now(UTC).isoformat(),
            },
        )
        raise
