"""Read-only pre-event observation over native yielded times, without matching edits.

Sparse gaps are filled only while the account has no trade objects (including
pending entries) and closed cash is unchanged. Nonflat gaps remain missing.
Futures trade OPEN is explicitly not relabeled as 5-minute mark.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
from freqtrade.persistence import LocalTrade


class Observer:
    def __init__(self, account, plan, output):
        self.account, self.plan, self.output = account, plan, Path(output)
        self.start, self.end = pd.Timestamp(plan["start"]), pd.Timestamp(plan["end"])
        self.step = pd.Timedelta(minutes=5)
        self.last = self.start - self.step
        self.five_minute_rows = []
        self.rows = []  # Legacy runner expects a complete *hourly* view here.
        self.gaps = []
        self.finished = False
        self.previous_flat = True
        self.previous_cash = float(plan["initial_equity_usdt"])
        path = Path(plan["native_observer_source"]["path"])
        spec = importlib.util.spec_from_file_location("rd_original_valuation_helpers", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.original = module.Observer(account, plan, output)
        original_generator = account.time_pair_generator

        def observed_generator(*args, **kwargs):
            for item in original_generator(*args, **kwargs):
                current = pd.Timestamp(item[0])
                if not self.finished:
                    if current >= self.end:
                        # Freeze before the native caller processes this event.
                        # Its resulting cash/trades are outside our window and
                        # cannot establish the state of earlier missing times.
                        self._finish_before_native_force_exit()
                    elif self.start <= current and current > self.last:
                        self._before_event(current)
                yield item
                # Resuming after yield means the native caller completed this
                # pair's backtest_loop. No order or wallet mutation is performed.
                if not self.finished:
                    self.previous_flat = not LocalTrade.bt_trades_open
                    self.previous_cash = self.capital + float(LocalTrade.bt_total_profit)
            self._finish_before_native_force_exit()

        account.time_pair_generator = observed_generator

    @property
    def capital(self):
        return float(self.plan["initial_equity_usdt"])

    @property
    def maximum_identity_error(self):
        return self.original.maximum_identity_error

    @property
    def maximum_basis_rounding_bias(self):
        return self.original.maximum_basis_rounding_bias

    def capture(self, current_time):
        # The archived runner calls this at main-candle wallet capture. The
        # generator hook supplies the finer, globally once-per-time observation.
        return None

    def _append(self, current, values, provenance):
        row = {"date": current, **values, "open_trades": len(LocalTrade.bt_trades_open),
               "closed_realized_pnl": float(LocalTrade.bt_total_profit),
               "hourly_mark_equity": None, "observation_kind": provenance}
        if current.minute == 0:
            mark = self.original.trade_values(current, marks=True)
            if mark is not None:
                row["hourly_mark_equity"] = mark["equity"]
            self.rows.append(row.copy())
        self.five_minute_rows.append(row)
        self.last = current

    def _fill_gap(self, before):
        cursor = self.last + self.step
        if cursor >= before:
            return
        flat_now = not LocalTrade.bt_trades_open
        cash_now = self.capital + float(LocalTrade.bt_total_profit)
        if not (self.previous_flat and flat_now and cash_now == self.previous_cash):
            self.gaps.append({"from": cursor.isoformat(), "to_exclusive": before.isoformat(),
                              "reason": "nonflat_or_changed_cash_without_native_yield; not interpolated"})
            return
        while cursor < before:
            self._append(cursor, {"equity": cash_now, "gross": 0.0, "open_pnl": 0.0},
                         "flat_no_native_event_gap")
            cursor += self.step

    def _before_event(self, current):
        self._fill_gap(current)
        self._append(current, self.original.trade_values(current), "native_pre_first_pair_event")

    def _finish_before_native_force_exit(self):
        if self.finished:
            return
        self._fill_gap(self.end)
        frame = pd.DataFrame(self.five_minute_rows)
        expected = pd.date_range(self.start, self.end, freq="5min", inclusive="left")
        actual = pd.DatetimeIndex(frame["date"]) if not frame.empty else pd.DatetimeIndex([])
        missing = expected.difference(actual)
        if len(actual) != len(actual.unique()) or not actual.is_monotonic_increasing:
            raise ValueError("Duplicate or nonmonotonic native observation times")
        frame.to_parquet(self.output / "five_minute_reference_equity.parquet", index=False)
        market = str(self.account.config["trading_mode"])
        receipt = {"scope": "native model state at yielded events; flat-only gap completion",
                   "start": self.start.isoformat(), "end_exclusive": self.end.isoformat(),
                   "rows": len(frame), "expected_rows": len(expected), "missing_rows": len(missing),
                   "gap_records": self.gaps, "complete_reference_calendar": not len(missing),
                   "native_order_matching_changed": False, "native_terminal_force_exit_included": False,
                   "five_minute_mark_verified": False,
                   "valuation": "spot 5m OPEN reference" if market == "spot" else "futures 5m trade OPEN conditional reference; hourly mark separately",
                   "economic_qualified": False,
                   "maximum_identity_error_usdt": self.maximum_identity_error,
                   "maximum_basis_rounding_bias_usdt": self.maximum_basis_rounding_bias,
                   "old_hourly_anchor": self.plan["comparison_end"],
                   "new_five_minute_endpoint": (self.end - self.step).isoformat()}
        (self.output / "five_minute_observer_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        self.finished = True
