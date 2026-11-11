"""Manual user entrypoint. --check-only never constructs a trading worker.

The default two-market initial launch is reserved for the user's manual invocation.
Targeted maintenance of an existing spot worker uses spot_restart and requires
explicit user authorization, identity/flat-state checks and ledger preservation.
"""

import argparse
import fcntl
import os
import subprocess
import sys

from deploy.mainnet_100_20261109.capital import wallet_class
from deploy.mainnet_100_20261109.prepare import HERE, ROOT, STATE, STRATEGY
from deploy.nfi80_selected.launch import verify as verify_paper_source
from deploy.nfi80_selected.selector import read, save, sha


class ResearchAuditHold(RuntimeError):
    """Research hold pending audit clearance or the user's explicit release."""

    def __init__(self, reasons):
        # Only these public diagnostic strings may reach the terminal. Other
        # exceptions retain type-only output because they may contain signed URLs.
        known = {
            "Retrospective author blacklist": "Historical author blacklist has hindsight bias",
            "Official spot and futures exit-time lookahead warnings unresolved": (
                "Spot/futures exit-time lookahead warnings remain unresolved"
            ),
            "No same-contract100USDT spot/futures economic reproduction": (
                "Matching 100-USDT spot/futures backtests have not been reproduced"
            ),
        }
        details = [
            known.get(reason, "Additional research audit item unresolved") for reason in reasons
        ]
        message = "Manual command stopped: ResearchAuditHold\nNew live startup is disabled."
        message += "".join("\n- " + detail for detail in details)
        message += (
            "\nAudit record: deploy/mainnet_100_20261109/audit-hold.json"
            "\n--check-only permits read-only checks; it does not clear this hold."
        )
        super().__init__(message)


def enforce_research_hold(audit):
    # A user release permits their manual command; it does not resolve research findings.
    if audit["status"] not in {"CLEARED", "RELEASED_BY_USER"}:
        raise ResearchAuditHold(audit.get("reasons", []))


def verify(*, allow_hold=False):
    verify_paper_source()
    release = read(HERE / "release.json")
    for name, digest in release["files"].items():
        if sha(HERE / name) != digest:
            raise ValueError("Manual release source changed")
    for market, digest in release["config_sha256"].items():
        path = STATE / market / "config.json"
        if path.is_symlink() or path.stat().st_mode & 0o077 or sha(path) != digest:
            raise ValueError("Private100USDT configuration changed")
        config = read(path)
        wallet_class(config)
        credential_path = STATE / market / "credentials.json"
        if credential_path.is_symlink() or credential_path.stat().st_mode & 0o077:
            raise ValueError("Credential overlay must remain private and local")
        if (
            config.get("dry_run") is not False
            or config["initial_state"] != "stopped"
            or config["strategy"] != STRATEGY
            or config["force_entry_enable"] is not False
        ):
            raise ValueError("Unexpected capital or strategy contract")
        if market == "futures" and (
            config["margin_mode"] != "isolated" or set(config["nfi_parameters"].values()) != {3.0}
        ):
            raise ValueError("Futures must remain isolated3x")
    if not allow_hold:
        enforce_research_hold(read(HERE / "audit-hold.json"))
    return release


def worker(market):
    # Only reached by the user's manual invocation, never during checks/tests.
    import freqtrade.freqtradebot as native_bot
    from freqtrade.main import main as native_main

    verify()
    folder = STATE / market
    lock = os.open(folder / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    save(folder / "worker.pid", {"pid": os.getpid()})
    native_bot.Wallets = wallet_class(read(folder / "config.json"))
    overlay = folder / "running.private.json"
    save(overlay, {"initial_state": "running"})
    native_main(
        [
            "trade",
            "--config",
            str(folder / "config.json"),
            "--config",
            str(folder / "credentials.json"),
            "--config",
            str(overlay),
            "--strategy-path",
            str(HERE / "strategy"),
            "--userdir",
            str(folder),
            "--logfile",
            str(folder / "freqtrade.log"),
        ]
    )


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--worker", choices=["spot", "futures"])
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    verify(allow_hold=args.check_only)
    from deploy.mainnet_100_20261109.checks import account_checks

    if not args.check_only and any(
        (STATE / m / "trades.sqlite").exists() or (STATE / m / "worker.pid").exists()
        for m in ("spot", "futures")
    ):
        raise FileExistsError("Existing live state: reconcile before a new manual launch")
    receipt = account_checks()
    if args.check_only:
        print({"checks_passed": True, "live_workers_started": 0, "methods": receipt["methods"]})
        return
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    started = []
    for market in ("spot", "futures"):
        folder = STATE / market
        with (folder / "manual.stdout.log").open("ab") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "deploy.mainnet_100_20261109.manual",
                    "--worker",
                    market,
                ],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        save(folder / "worker.pid", {"pid": child.pid})
        started.append({"market": market, "pid": child.pid})
    print(
        {
            "manual_start_requested": started,
            "capital_mode_each": {
                m: read(STATE / m / "config.json").get("live_capital_mode", "fixed_100")
                for m in ("spot", "futures")
            },
            "note": (
                "Real orders may follow; startup request is not successful-fill "
                "or ongoing-health verification"
            ),
        }
    )


def cli():
    try:
        main()
    except ResearchAuditHold as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        # Requests errors can contain signed queries; never print their text.
        raise SystemExit("Manual command stopped: " + type(exc).__name__) from None


if __name__ == "__main__":
    cli()
