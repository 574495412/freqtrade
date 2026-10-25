"""Weekly cross-coin funding dispersion; price risk remains in both legs."""

import importlib.util
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from freqtrade.persistence import Trade
from freqtrade.research.data import save, sha
from freqtrade.strategy import IStrategy


logger = logging.getLogger(__name__)
DAY_MS = 86_400_000
VISIBILITY_MS = 300_000


def load_factor(path, expected):
    if sha(path) != expected:
        raise ValueError("Pinned funding formula changed")
    spec = importlib.util.spec_from_file_location("rotation_funding_formula", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_week(snapshot, values, symbols):
    if len(set(symbols)) != len(symbols) or set(values) != set(symbols) or len(symbols) < 4:
        raise ValueError("Weekly ranks require the complete registered universe")
    known = all(np.isfinite(value) for value in values.values())
    ordered = sorted(symbols, key=lambda symbol: (values[symbol], symbol)) if known else []
    eligible = known and values[ordered[-1]] > values[ordered[0]]
    short = set(ordered[:2]) if eligible else set()
    long = set(ordered[-2:]) if eligible else set()
    return [
        {
            "date": snapshot,
            "symbol": symbol,
            "funding_carry_24h": values[symbol],
            "rotation_long": symbol in long,
            "rotation_short": symbol in short,
            "complete_universe": known,
            "eligible_week": eligible,
        }
        for symbol in sorted(symbols)
    ]


def weekly_exit_time(opened):
    monday = opened.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=opened.weekday()
    )
    return monday + timedelta(days=7, hours=1)


def capture_week(symbols, snapshot, output, factor):
    """Read current public history; persist actual receipt clocks before paper decisions."""
    cut = int(snapshot.timestamp() * 1000) - VISIBILITY_MS
    start = cut - DAY_MS - 8 * 3_600_000

    def fetch(symbol):
        params = {"symbol": symbol, "startTime": start, "endTime": cut, "limit": 1000}
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate", params=params, timeout=15
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, list) or not 1 <= len(body) < 1000:
            raise ValueError("Public funding history is incomplete or truncated")
        if any(row["symbol"] != symbol for row in body):
            raise ValueError("Public funding response changed symbol")
        events = [
            {
                "symbol": symbol,
                "time_ms": int(row["fundingTime"]),
                "rate": float(row["fundingRate"]),
            }
            for row in body
        ]
        feature = factor.calculate([cut + VISIBILITY_MS], events, start, cut)
        return symbol, {
            "params": params,
            "received_utc": datetime.now(UTC).isoformat(),
            "body": body,
            "carry": float(feature.funding_carry_24h.iloc[0]),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = dict(pool.map(fetch, symbols))
    frame = pd.DataFrame(
        select_week(snapshot, {s: r["carry"] for s, r in receipts.items()}, symbols)
    )
    if not frame.complete_universe.all():
        raise ValueError("At least one current funding feature is unknown")
    output.mkdir(parents=True, exist_ok=True)
    save(
        output / (snapshot.strftime("%Y%m%dT%H%M%S") + ".json"),
        {"snapshot": snapshot.isoformat(), "sources": receipts, "real_orders_enabled": False},
    )
    return frame


class FundingRotation(IStrategy):
    """Fixed weekly four-leg portfolio; no averaging, no prediction model or parameter search."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True
    startup_candle_count = 48
    process_only_new_candles = True
    minimal_roi = {"0": 1000.0}
    stoploss = -0.10
    trailing_stop = False
    position_adjustment_enable = False

    def panel(self):
        if self.dp.runmode.value == "backtest":
            if not hasattr(self, "_history_panel"):
                path = Path(self.config["research_rotation_panel"])
                if sha(path) != self.config["research_rotation_panel_sha256"]:
                    raise ValueError("Frozen weekly panel changed")
                self._history_panel = pd.read_parquet(path)
            return self._history_panel
        if self.dp.runmode.value != "dry_run":
            raise ValueError("Funding rotation currently permits only backtest and isolated paper")
        now = datetime.now(UTC)
        if now.weekday() != 0 or now.hour != 2 or now.minute >= 5:
            return pd.DataFrame()
        snapshot = pd.Timestamp(now.replace(hour=1, minute=0, second=0, microsecond=0))
        if getattr(self, "_paper_snapshot", None) != snapshot:
            factor = load_factor(
                Path(__file__).with_name("funding.py"),
                self.config["research_rotation_factor_sha256"],
            )
            try:
                self._paper_panel = capture_week(
                    self.config["research_rotation_symbols"],
                    snapshot,
                    Path(self.config["user_data_dir"]) / "public_funding",
                    factor,
                )
            except (ValueError, requests.RequestException) as error:
                logger.warning("Weekly public funding capture failed; no new entries: %s", error)
                self._paper_panel = pd.DataFrame()
            self._paper_snapshot = snapshot
        return self._paper_panel

    def populate_indicators(self, dataframe, metadata):
        panel = self.panel()
        if panel.empty:
            dataframe["rotation_long"] = False
            dataframe["rotation_short"] = False
            dataframe["funding_carry_24h"] = np.nan
            return dataframe
        symbol = metadata["pair"].split("/")[0] + "USDT"
        columns = ["date", "funding_carry_24h", "rotation_long", "rotation_short"]
        return dataframe.merge(panel.loc[panel.symbol.eq(symbol), columns], on="date", how="left")

    def populate_entry_trend(self, dataframe, metadata):
        monday = (
            dataframe.date.dt.dayofweek.eq(0)
            & dataframe.date.dt.hour.eq(1)
            & dataframe.volume.gt(0)
        )
        dataframe["enter_long"] = (monday & dataframe.rotation_long.eq(True)).astype(int)
        dataframe["enter_short"] = (monday & dataframe.rotation_short.eq(True)).astype(int)
        dataframe["enter_tag"] = "weekly_funding_dispersion"
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_time >= weekly_exit_time(trade.open_date_utc):
            return "weekly_rebalance"
        return None

    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if current_time.weekday() != 0 or current_time.hour != 2 or current_time.minute >= 5:
            return False
        start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        return not any(
            trade.nr_of_successful_entries > 0
            for trade in Trade.get_trades_proxy(pair=pair, open_date=start)
        )
