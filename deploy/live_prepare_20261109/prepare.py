"""Prepare isolated STOPPED live configs and native webhook templates; never start a trader."""


# ruff: noqa: RUF001 -- Chinese notification copy uses native punctuation.

import json
import os
import plistlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from deploy.live_prepare_20261109.notify_relay import PORT, initialize
from deploy.live_prepare_20261109.preflight import ROOT, STATE, private_env, save


def webhook_config(market, url):
    label = "[NFI X8｜" + ("现货实盘" if market == "spot" else "永续实盘") + "] "
    order = (
        "\n交易编号 {trade_id}｜{pair}｜{direction}｜{leverage}×"
        "\n数量 {amount}｜订单价格 {order_rate}｜引擎开仓参考价 {open_rate}"
        "\n资金 {stake_amount} {stake_currency}｜仓位调整={sub_trade}"
    )
    pnl = "\n引擎盈亏 {profit_amount:.4f} {stake_currency} ({profit_ratio:.2%})"
    texts = {
        "entry": "开仓／加仓委托（尚未成交）" + order,
        "entry_fill": "开仓／加仓成交" + order,
        "entry_cancel": "开仓／加仓委托取消" + order + "\n原因 {reason}",
        "exit": "平仓／减仓委托（尚未成交）" + order + pnl + "\n原因 {exit_reason}",
        "exit_fill": "平仓／减仓成交" + order + pnl + "\n原因 {exit_reason}",
        "exit_cancel": "平仓／减仓委托取消" + order + pnl + "\n原因 {reason}",
        "startup": "程序启动：{status}",
        "status": "运行状态：{status}",
        "warning": "运行警告：{status}",
        "exception": "运行异常：{status}",
        "protection_trigger": "交易保护：{pair}｜{side}\n{reason}\n截止 {lock_end_time}",
        "protection_trigger_global": "全局交易保护：{side}\n{reason}\n截止 {lock_end_time}",
        "liquidation_warning": (
            "保证金风险预警：{pair}｜{direction}\n当前价 {current_rate}"
            "｜清算价 {liquidation_price}\n风险距离 {remaining_ratio:.2%}"
        ),
    }
    config = {
        "enabled": True,
        "format": "json",
        "url": url,
        "timeout": 3,
        "retries": 2,
        "retry_delay": 0.2,
        "allow_custom_messages": False,
    }
    config.update(
        {key: {"msgtype": "text", "text": {"content": label + text}} for key, text in texts.items()}
    )
    return config


def native_schema_check(config):
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver

    # Offline configuration/strategy parsing only. No Exchange or FreqtradeBot is instantiated.
    with TemporaryDirectory(prefix="nfi-live-config-schema-") as folder:
        effective = {
            **config,
            "runmode": RunMode.LIVE,
            "user_data_dir": Path(folder),
            "strategy_path": str(ROOT / "deploy/strategy/nfi-x8"),
        }
        strategy = StrategyResolver.load_strategy(effective)
        validate_config_consistency(effective)
        return {
            "strategy": strategy.__class__.__name__,
            "stoploss": strategy.stoploss,
            "position_adjustment_enable": strategy.position_adjustment_enable,
            "futures_leverage": {
                "regular": strategy.futures_mode_leverage,
                "rebuy": strategy.futures_mode_leverage_rebuy_mode,
                "grind": strategy.futures_mode_leverage_grind_mode,
            },
        }


def account_blockers(checks, available, missing_pairs):
    check = checks["futures_account"]
    allowed = checks["permissions"].get("permissions", {}).get("enableFutures")
    blockers = []
    if not all(value.get("ok") for value in checks.values()):
        blockers.append("INCOMPLETE_AUTHENTICATED_READS")
    if not allowed:
        blockers.append("CURRENT_KEY_TRADE_PERMISSION_NOT_ENABLED")
    if available <= 0:
        blockers.append("NO_AVAILABLE_USDT_FOR_THIS_KEY_AND_WALLET")
    if checks["futures_config"].get("hedge_mode") is not False:
        blockers.append("HEDGE_MODE_UNSUPPORTED_BY_PINNED_FREQTRADE")
    if checks["futures_config"].get("multi_assets_margin") is not False:
        blockers.append("MULTI_ASSET_MODE_NOT_SUPPORTED")
    if checks["futures_config"].get("can_trade") is not True:
        blockers.append("ACCOUNT_TRADING_NOT_CONFIRMED")
    if checks["copytrading_status"].get("is_lead_trader") is not True:
        blockers.append("LEAD_TRADER_STATUS_NOT_CONFIRMED")
    if missing_pairs:
        blockers.append("CONFIGURED_PAIRS_OUTSIDE_LEAD_WHITELIST")
    if check.get("nonzero_positions") != 0 or checks["futures_open_orders"].get("open_orders") != 0:
        blockers.append("EXISTING_ACCOUNT_STATE_REQUIRES_RECONCILIATION")
    blockers.extend(["USER_MANUAL_ACTIVATION_REQUIRED", "UNRESOLVED_EXIT_TIME_DIAGNOSTICS"])
    return blockers


def main():
    from deploy.launch import verify

    verify()
    os.umask(0o077)
    token = initialize()
    local_url = f"http://127.0.0.1:{PORT}/events/" + token
    credentials = private_env(ROOT / "user_data/deployment/.env.mainnet")
    save(
        STATE / "credentials.json",
        {
            "exchange": {
                "key": credentials["BINANCE_COPYTRADING_API_KEY"],
                "secret": credentials["BINANCE_COPYTRADING_API_SECRET"],
            }
        },
    )
    readiness = json.loads((STATE / "account_readiness.json").read_text())
    checks = readiness["checks"]
    lead_body = json.loads((STATE / "lead_symbols.private.json").read_text())["body"]
    lead_symbols = {value["symbol"] for value in lead_body.get("data", [])}
    prepared = {}
    for market in ("futures",):
        config = json.loads((ROOT / "deploy/configs" / f"{market}.json").read_text())
        config.pop("dry_run_wallet", None)
        config.pop("fee", None)  # Live mode must fetch the actual exchange fee, not a paper proxy.
        config.update(
            dry_run=False,
            initial_state="stopped",
            force_entry_enable=False,
            bot_name=f"nfi-x8-live-prepared-{market}",
        )
        config["exchange"].update(key="", secret="", password="")
        config["api_server"]["enabled"] = False
        config["webhook"] = webhook_config(market, local_url)
        folder = STATE / market
        folder.mkdir(exist_ok=True)
        config.update(
            db_url="sqlite:///" + str(folder / "trades.sqlite"),
            strategy_path=str(ROOT / "deploy/strategy/nfi-x8"),
        )
        save(folder / "config.json", config)
        schema = native_schema_check(config)
        check = checks["futures_account"]
        available = Decimal(check.get("available_usdt", "0"))
        missing_pairs = [
            pair
            for pair in config["exchange"]["pair_whitelist"]
            if pair.split(":")[0].replace("/", "") not in lead_symbols
        ]
        blockers = account_blockers(checks, available, missing_pairs)
        prepared[market] = {
            "config_path": str(folder / "config.json"),
            "initial_state": "stopped",
            "native_schema": schema,
            "observed_available_usdt": str(available),
            "observed_99pct_budget_usdt": str(available * Decimal("0.99")),
            "capital_policy": (
                "Native unlimited stake with 0.99 tradable_balance_ratio; own wallet only."
            ),
            "no_wallet_transfers": True,
            "existing_credential_source": True,
            "pairs_outside_lead_whitelist": missing_pairs,
            "account_checked_at_utc": readiness["checked_at_utc"],
            "startup_blockers": blockers,
            "live_worker_started": False,
        }
    plist = {
        "Label": "io.freqtrade.notifications",
        "ProgramArguments": [
            str(ROOT / ".venv-paper/bin/python"),
            "-B",
            "-m",
            "deploy.live_prepare_20261109.notify_relay",
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(STATE / "notification.stdout.log"),
        "StandardErrorPath": str(STATE / "notification.stderr.log"),
    }
    (STATE / "io.freqtrade.notifications.plist").write_bytes(plistlib.dumps(plist))
    previous_spot = STATE / "spot/config.json"
    if previous_spot.exists():
        if (STATE / "spot/trades.sqlite").exists():
            raise ValueError("Unexpected spot trading state; do not archive without reconciliation")
        archived = STATE / "spot/config.not-selected.json"
        previous_spot.replace(archived)
    save(
        STATE / "preparation.json",
        {
            "prepared_at_utc": datetime.now(UTC).isoformat(),
            "markets": prepared,
            "same_existing_api_credentials": True,
            "user_selected_markets": ["futures"],
            "spot_selected": False,
            "private_credentials_overlay": str(STATE / "credentials.json"),
            "live_workers_started": 0,
            "orders_submitted": 0,
            "native_engine_and_strategy_unchanged": True,
            "notification_types": 13,
            "notification_delivery_semantics": "at-least-once; bounded retries",
        },
    )
    print(
        json.dumps(
            {
                "prepared_markets": list(prepared),
                "native_schema": "passed",
                "live_workers_started": 0,
                "notification_types": 13,
            }
        )
    )


if __name__ == "__main__":
    main()
