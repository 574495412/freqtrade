"""Local synthetic-only bridge through the inherited Backtesting.start lifecycle.

This closes a technical integration gap, not historical equity admission. The
ordinary EquityBacktesting constructor remains closed. Exported native trade
statistics are gross of the equity fee/dividend overlay and never net returns.
"""

import hashlib
from dataclasses import asdict

import pandas as pd

from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.data.btanalysis.historic_precision import get_tick_size_over_time
from freqtrade.optimize.equity_nav import EquityMark, capture_equity_nav
from freqtrade.persistence import LocalTrade


class EquityFixtureLifecycleMixin:
    """Compose before open-only/session/fee-fill mixins and native Backtesting."""

    def _require_fixture_lifecycle(self):
        plan = self._require_session_routing()
        if (
            self.config.get("synthetic_equity_fixture") is not True
            or plan.pairs not in (("ABC/USD",), ("ABC/HKD",))
            or tuple(self.config["exchange"]["pair_whitelist"]) != plan.pairs
            or len(self.strategylist) != 1
            or self.config.get("backtest_cache") != "none"
            or self.required_startup != 0
            or self.config.get("export") not in {"trades", "signals"}
            or getattr(self, "_equity_fixture_started", False)
        ):
            raise EquityAdmissionError("Single uncached synthetic ABC lifecycle required")
        return plan

    def load_bt_data(self):
        plan = self._require_fixture_lifecycle()
        if self.dataprovider._equity_raw:
            raise EquityAdmissionError("Fresh native disk loading required, no injected frame")
        data = {}
        self._equity_source_sha = {}
        for pair in plan.pairs:
            frame = self.dataprovider.historic_ohlcv(pair, "1d")
            plan._contracts[pair].validate(frame)
            data[pair] = frame
            self._equity_source_sha[pair] = hashlib.sha256(frame.to_csv().encode()).hexdigest()
            self.price_pair_prec[pair] = get_tick_size_over_time(frame)
            self.available_pairs.append(pair)
        self._equity_loaded = {p: d.copy(deep=True).set_index("date") for p, d in data.items()}
        self.detail_data = {}
        return data, self.timerange

    def backtest(self, processed, start_date, end_date):
        plan = self._require_fixture_lifecycle()
        self._require_open_only()
        if (pd.Timestamp(start_date), pd.Timestamp(end_date)) != (
            plan.labels[0], plan.labels[-1]
        ):
            raise EquityAdmissionError("Native trimmed labels must equal the complete fixture window")
        self.equity_open_snapshots, self.equity_close_snapshots = [], []
        result = super().backtest(processed, plan.opens[0], plan.opens[-1])
        if len(self.equity_close_snapshots) != len(plan.labels):
            raise EquityAdmissionError("Incomplete synthetic account has no completed result")
        return result

    def _fixture_capture(self, at, kind, label):
        plan = self.equity_session_plan
        marks = []
        for pair in plan.pairs:
            owned = self.wallets.get_owned(pair, pair.split("/")[0])
            if owned:
                price = float(self._equity_loaded[pair].loc[label, kind])
                marks.append(EquityMark(pair, plan.currency, at, price, kind,
                                        self._equity_source_sha[pair]))
        return asdict(capture_equity_nav(self.wallets, marks, at=at, mark_kind=kind))

    def _time_generator(self, start_date, end_date):
        plan = self.equity_session_plan
        for index, at in enumerate(super()._time_generator(start_date, end_date)):
            label = plan.labels[index]
            self.equity_open_snapshots.append(self._fixture_capture(at, "open", label))
            yield at
            closed = plan.closes[index]
            self.wallets.advance(closed)
            self.dataprovider._set_dataframe_max_date(closed)
            self.equity_close_snapshots.append(self._fixture_capture(closed, "close", label))

    def handle_left_open(self, open_trades, data):
        if any(t.has_open_orders for t in LocalTrade.bt_trades_open):
            raise EquityAdmissionError("Unfilled terminal order cannot become a marked result")
        # Exact terminal close marks retain holdings. No fabricated terminal sale.

    def start(self):
        self._require_fixture_lifecycle()
        self.equity_lifecycle_completed = False
        super().start()
        plan = self.equity_session_plan
        if len(self.equity_open_snapshots) != len(plan.opens) or len(
            self.equity_close_snapshots
        ) != len(plan.closes):
            raise EquityAdmissionError("Missing opening or closing account boundary")
        first, last = self.equity_open_snapshots[0], self.equity_close_snapshots[-1]
        if first["holdings"] or first["filled_orders"] or first["net_nav"] != first["initial_cash"]:
            raise EquityAdmissionError("Synthetic account must start untouched and flat")
        self.equity_fixture_result = {
            "scope": "synthetic native start integration only",
            "market": plan.market, "currency": plan.currency,
            "start": first["at"].isoformat(), "end": last["at"].isoformat(),
            "initial_cash": first["initial_cash"], "final_net_nav": last["net_nav"],
            "execution_fees": str(last["execution_fees"]),
            "dividend_receivable": str(last["dividend_receivable"]),
            "paid_dividends": str(last["paid_dividends"]),
            "terminal_holdings": last["holdings"],
            "terminal_unsettled_sale_proceeds": last["unsettled_receivable"],
            "native_zip_statistics": "diagnostic; gross of equity fee/dividend overlay",
            "historical_equity_accounts": 0, "bench_records": 0,
            "financial_strategy_performance": None,
        }
        self.equity_lifecycle_completed = True
        self._equity_fixture_started = True
