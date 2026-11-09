"""Write private, STOPPED100USDT configurations; never instantiate a trading bot."""

import os
from pathlib import Path

from deploy.live_prepare_20261109.preflight import private_env
from deploy.live_prepare_20261109.prepare import webhook_config
from deploy.mainnet_100_20261109.capital import CAPITAL, STAKE_BUDGET
from deploy.nfi80_selected.launch import verify
from deploy.nfi80_selected.selector import read, save, sha


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATE = ROOT / "user_data/deployment/mainnet-100-20261109"
STRATEGY = "NFIManualFlatBoost"


def main():
    verify()
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    values = private_env(ROOT / "user_data/deployment/.env.mainnet")
    sources = {
        "spot": ROOT / "user_data/deployment/spot-live-prepare-20261109/config.json",
        "futures": ROOT / "deploy/nfi80_selected/optimized-config.json",
    }
    for market, source in sources.items():
        folder = STATE / market
        if (folder / "trades.sqlite").exists() or (folder / "worker.pid").exists():
            raise FileExistsError("Preserve existing live ledger and worker")
        folder.mkdir(mode=0o700, exist_ok=True)
        config = read(source)
        config.pop("fee", None)
        config.update(
            dry_run=False,
            initial_state="stopped",
            available_capital=STAKE_BUDGET,
            capital_ceiling_usdt=CAPITAL,
            strategy=STRATEGY,
            strategy_path=str(HERE / "strategy"),
            force_entry_enable=False,
            bot_name=f"nfi-manual-cap100-{market}",
            db_url="sqlite:///" + str(folder / "trades.sqlite"),
        )
        config["api_server"]["enabled"] = False
        config["exchange"].update(key="", secret="", password="")
        if market == "futures":
            config["nfi_parameters"] = {name: 3.0 for name in config["nfi_parameters"]}
            config["webhook"] = webhook_config(
                market,
                read(ROOT / "user_data/deployment/spot-live-prepare-20261109/config.json")[
                    "webhook"
                ]["url"],
            )
        credentials = {
            "exchange": {
                "key": values[
                    "BINANCE_COPYTRADING_SPOT_API_KEY"
                    if market == "spot"
                    else "BINANCE_COPYTRADING_API_KEY"
                ],
                "secret": values[
                    "BINANCE_COPYTRADING_SPOT_API_SECRET"
                    if market == "spot"
                    else "BINANCE_COPYTRADING_API_SECRET"
                ],
            }
        }
        save(folder / "config.json", config)
        save(folder / "credentials.json", credentials)
        for path in (folder / "config.json", folder / "credentials.json"):
            path.chmod(0o600)
    files = ["capital.py", "prepare.py", "manual.py", "checks.py", "strategy/NFIManualFlatBoost.py"]
    save(
        HERE / "release.json",
        {
            "files": {name: sha(HERE / name) for name in files},
            "config_sha256": {m: sha(STATE / m / "config.json") for m in sources},
            "stake_budget_usdt_each": STAKE_BUDGET,
            "capital_ceiling_usdt_each": CAPITAL,
            "futures_leverage": 3,
            "futures_nominal_ceiling_usdt": 300,
            "mode": "manual_live_only",
            "production_eligible": False,
            "spot_2026_same_contract_backtest_available": False,
            "live_workers_started_by_agent": 0,
        },
    )
    print(
        "Prepared STOPPED configurations: spot100USDT, futures100USDTmargin/3x. No trader started."
    )


if __name__ == "__main__":
    main()
