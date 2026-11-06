"""Launch an isolated X8 AddCap worker through the pinned Freqtrade paper CLI."""

import argparse
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = ROOT / "user_data/deployment/nfi-x8-addcap"
STRATEGY = "NFIX8AddCapPaper"
MARKETS = ("spot", "futures")
BASE_SETTINGS = (
    "dry_run_wallet",
    "grinding_enable",
    "max_open_trades",
    "stake_currency",
    "stake_amount",
    "tradable_balance_ratio",
    "fiat_display_currency",
    "timeframe",
    "unfilledtimeout",
    "order_types",
    "entry_pricing",
    "exit_pricing",
    "trading_mode",
    "margin_mode",
    "dataformat_ohlcv",
    "dataformat_trades",
    "pairlists",
    "dry_run",
    "force_entry_enable",
    "initial_state",
    "internals",
    "fee",
    "nfi_parameters",
)
EXCHANGE_SETTINGS = (
    "name",
    "enable_ws",
    "pair_whitelist",
    "pair_blacklist",
    "ccxt_config",
    "ccxt_async_config",
)


def verify():
    # Reuse the incumbent release verifier, including its pinned engine checks.
    sys.path.insert(0, str(ROOT))
    try:
        from deploy.launch import verify as verify_base

        baseline = verify_base()
    finally:
        sys.path.pop(0)

    release = json.loads((HERE / "release.json").read_text())
    if (
        release["mode"] != "dry_run"
        or release["real_orders_enabled"] is not False
        or release["upstream_commit"] != baseline["upstream_commit"]
        or release["strategy_commit"] != baseline["strategy_commit"]
    ):
        raise ValueError("Paper release provenance mismatch")
    for name, expected in release["files"].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Candidate release file differs: " + name)
    return release


def paper_config(market):
    base = json.loads((ROOT / "deploy/configs" / f"{market}.json").read_text())
    if (
        base["dry_run"] is not True
        or base["force_entry_enable"] is not False
        or base["trading_mode"] != market
        or base["dry_run_wallet"] != 1000
    ):
        raise ValueError("Base paper contract mismatch")
    config = {key: base[key] for key in BASE_SETTINGS if key in base}
    config.update(
        strategy=STRATEGY,
        bot_name=f"nfi-x8-addcap-{market}",
        exchange={key: base["exchange"][key] for key in EXCHANGE_SETTINGS},
    )
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("market", choices=MARKETS)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    release = verify()
    config = paper_config(args.market)
    if args.verify:
        from tempfile import TemporaryDirectory

        from freqtrade.configuration.config_validation import validate_config_consistency
        from freqtrade.enums import RunMode
        from freqtrade.resolvers import StrategyResolver

        with TemporaryDirectory(prefix="nfi-x8-addcap-verify-") as directory:
            effective = {
                **config,
                "runmode": RunMode.DRY_RUN,
                "user_data_dir": Path(directory),
                "strategy_path": str(HERE / "strategy"),
            }
            strategy = StrategyResolver.load_strategy(effective)
            validate_config_consistency(effective)
            if not strategy.position_adjustment_enable:
                raise ValueError("Candidate adjustment callback is disabled")
        print(
            json.dumps(
                {
                    "verified": True,
                    "mode": release["mode"],
                    "market": args.market,
                    "strategy": STRATEGY,
                    "pairs": len(config["exchange"]["pair_whitelist"]),
                }
            )
        )
        return

    os.umask(0o077)
    state = STATE / args.market
    state.mkdir(parents=True, exist_ok=True)
    if state.resolve() != state or (state / "trades.sqlite").is_symlink():
        raise ValueError("Candidate state must use its own directory and SQLite file")
    lock = os.open(state / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(lock, True)
    config_path = state / "config.json"
    text = json.dumps(config, indent=2) + "\n"
    if config_path.is_symlink():
        raise ValueError("Candidate config is a symlink")
    if config_path.exists():
        if config_path.read_text() != text:
            raise ValueError("Candidate configuration drift")
    else:
        with config_path.open("x") as file:
            file.write(text)

    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    env.update(PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    python = str(ROOT / ".venv-paper/bin/python")
    os.chdir(ROOT)
    os.execve(  # noqa: S606 -- fixed interpreter and official CLI, no shell
        python,
        [
            python,
            "-m",
            "freqtrade",
            "trade",
            "--dry-run",
            "--config",
            str(config_path),
            "--strategy-path",
            str(HERE / "strategy"),
            "--userdir",
            str(state),
            "--db-url",
            "sqlite:///" + str(state / "trades.sqlite"),
            "--logfile",
            str(state / "freqtrade.log"),
        ],
        env,
    )


if __name__ == "__main__":
    main()
