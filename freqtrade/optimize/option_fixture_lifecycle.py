"""Synthetic-only inherited Backtesting.start bridge for option coin cash.

This is not historical option admission. Source prices/marks are explicitly
synthetic and the ordinary OptionBacktesting constructor stays closed.
"""

from dataclasses import asdict

from freqtrade.data.history.inverse_options import load_option_chart_history
from freqtrade.data.inverse_option_contracts import OptionAdmissionError
from freqtrade.option_wallets import OptionPremiumWallets
from freqtrade.persistence import LocalTrade, Trade


class OptionFixtureLifecycleMixin:
    def load_bt_data(self):
        if (
            self.config.get("synthetic_option_fixture") is not True
            or self.config.get("synthetic_option_scheduled_start") is not True
            or self.config.get("backtest_cache") != "none"
            or self.timeframe != "1h"
            or self.required_startup != 0
            or len(self.strategylist) != 1
            or not isinstance(self.wallets, OptionPremiumWallets)
        ):
            raise OptionAdmissionError("Explicit uncached synthetic-only option lifecycle required")
        expected = set(self.config["exchange"]["pair_whitelist"])
        if set(self.option_chart_contracts) != expected:
            raise OptionAdmissionError("Exact synthetic option storage coverage required")
        data = {}
        for pair, contract in self.option_chart_contracts.items():
            data[pair] = load_option_chart_history(self.config["datadir"], contract)
            self.available_pairs.append(pair)
        self.detail_data, self.futures_data, self.price_pair_prec = {}, {}, {}
        return data, self.timerange

    def _capture_option_state(self, at, phase, inputs):
        return {
            "at": at.isoformat(),
            "phase": phase,
            "state": asdict(self.wallets.cash_state),
            "input_marks": [asdict(m) for m in inputs],
            "native_orders": [
                {
                    "trade_id": t.id,
                    "pair": t.pair,
                    "is_short": t.is_short,
                    "order_id": o.order_id,
                    "at": str(o.order_filled_utc),
                    "created_at": str(o.order_date_utc),
                    "side": o.ft_order_side,
                    "quantity": str(o.safe_filled),
                    "requested_quantity": str(o.safe_amount),
                    "price": str(o.safe_price),
                    "status": o.status,
                    "ft_is_open": o.ft_is_open,
                }
                for t in Trade.get_trades_proxy()
                for o in t.orders
            ],
        }

    def _time_generator(self, start_date, end_date):
        self.option_boundaries = []
        for at in super()._time_generator(start_date, end_date):
            inputs = self.option_mark_inputs[at]
            self.wallets.advance(at, inputs)
            self.option_boundaries.append(self._capture_option_state(at, "before", inputs))
            yield at
            self.wallets.update()
            if self.wallets.cash_state.liquidation_required:
                raise OptionAdmissionError(
                    "Unimplemented liquidation cannot become completed fixture"
                )
            self.option_boundaries.append(self._capture_option_state(at, "after", inputs))

    def handle_left_open(self, open_trades, data):
        if any(t.has_open_position or t.has_open_orders for t in LocalTrade.bt_trades_open):
            raise OptionAdmissionError(
                "Full lifecycle fixture must end flat;no fabricated force sale"
            )
