"""Read-only full-position valuation for the pinned native NFI replay.

Five-minute spot OPEN prices are a reference valuation, not certified marks
or executable bid/ask. Actual hourly mark observations are reported separately.
No downloaded pickle or user credentials are loaded.
"""

import json
import math
from pathlib import Path

import pandas as pd

from freqtrade.persistence import LocalTrade


def native_open_value(trade, price):
  filled = [order for order in trade.orders if not order.ft_is_open and order.filled]
  exits = [i for i, order in enumerate(filled) if order.ft_order_side != trade.entry_side]
  realized_funding = math.fsum(order.funding_fee or 0.0 for order in filled[: exits[-1] + 1]) if exits else 0.0
  return trade.calculate_profit(price).total_profit - realized_funding


def order_value(trade, price):
  filled = [order for order in trade.orders if not order.ft_is_open and order.filled]
  entries = [order for order in filled if order.ft_order_side == trade.entry_side]
  exits = [order for order in filled if order.ft_order_side != trade.entry_side]
  entry_value = math.fsum(order.filled * order.safe_price for order in entries)
  exit_value = math.fsum(order.filled * order.safe_price for order in exits)
  quantity = math.fsum(order.filled for order in entries) - math.fsum(order.filled for order in exits)
  if abs((quantity - trade.amount) * price) > 1e-6:
    raise SystemExit("Open inventory does not reconcile from filled orders")
  pnl = (-1 if trade.is_short else 1) * (exit_value + trade.amount * price - entry_value)
  fees = entry_value * trade.fee_open + exit_value * trade.fee_close
  reserve = trade.amount * price * trade.fee_close
  return pnl - fees - reserve + (trade.funding_fees or 0.0)


def basis_rounding_bias(trade):
  """Explain native tick-rounded average basis; leave strategy behavior intact."""
  side = -1 if trade.is_short else 1
  exact_basis = trade.stake_amount * trade.leverage
  rounded_basis = trade.amount * trade.open_rate
  return side * (exact_basis - rounded_basis) * (1 + side * trade.fee_open)


class Observer:
  def __init__(self, account, plan, output):
    self.account = account
    self.output = output
    self.capital = float(account.config["dry_run_wallet"])
    self.fee = float(account.config["fee"])
    self.prices = {}
    self.marks = {}
    self.rows = []
    self.peak = self.capital
    self.maximum_identity_error = 0.0
    self.maximum_basis_rounding_bias = 0.0
    self.current = None
    directory = Path(plan["datadir"])
    for pair in plan["observer_pairs"]:
      stem = pair.replace("/", "_").replace(":", "_")
      for suffix, destination in [
        ("5m", self.prices),
        ("1h", self.marks),
      ]:
        path = directory / f"{stem}-{suffix}.feather"
        if path.exists():
          frame = pd.read_feather(path).set_index("date")
          selected = frame.loc[plan["observation_start"] : plan["observation_end"], "open"]
          destination[pair] = selected

  def trade_values(self, current_time, marks=False):
    prices = self.marks if marks else self.prices
    pnl = []
    gross = []
    for trade in LocalTrade.bt_trades_open:
      if trade.pair not in prices or current_time not in prices[trade.pair].index:
        if marks:
          return None
        raise SystemExit(f"Missing current OPEN price for held {trade.pair} at {current_time}")
      price = float(prices[trade.pair].loc[current_time])
      if not math.isfinite(price) or price <= 0:
        raise SystemExit("Invalid current reference price")
      value = order_value(trade, price)
      native_value = native_open_value(trade, price)
      rounding_bias = basis_rounding_bias(trade)
      error = abs(value - (native_value - rounding_bias))
      self.maximum_identity_error = max(self.maximum_identity_error, error)
      self.maximum_basis_rounding_bias = max(self.maximum_basis_rounding_bias, abs(rounding_bias))
      if error > 1e-6:
        (self.output / "observation_failure.json").write_text(
          json.dumps(
            {
              "time": str(current_time),
              "pair": trade.pair,
              "side": trade.is_short,
              "order_ledger_value": value,
              "native_value": native_value,
              "rounding_bias": rounding_bias,
              "adjusted_error": error,
              "amount": trade.amount,
              "open_rate": trade.open_rate,
              "stake_amount": trade.stake_amount,
              "leverage": trade.leverage,
              "funding_fees": trade.funding_fees,
              "realized_profit": trade.realized_profit,
            },
            indent=2,
          )
          + "\n"
        )
        raise SystemExit(f"Adjusted open cash mismatch: {trade.pair}: {error}")
      pnl.append(value)
      gross.append(trade.amount * price)
    return {
      "equity": self.capital + LocalTrade.bt_total_profit + math.fsum(pnl),
      "gross": math.fsum(gross),
      "open_pnl": math.fsum(pnl),
    }

  def capture(self, current_time):
    current_time = pd.Timestamp(current_time)
    values = self.trade_values(current_time)
    self.peak = max(self.peak, values["equity"])
    row = {
      "date": current_time,
      **values,
      "peak": self.peak,
      "drawdown": 1 - values["equity"] / self.peak,
      "gross_ratio": values["gross"] / values["equity"] if values["equity"] > 0 else None,
      "reference_insolvent": values["equity"] <= 0,
      "open_trades": len(LocalTrade.bt_trades_open),
      "closed_realized_pnl": LocalTrade.bt_total_profit,
      "hourly_mark_equity": None,
    }
    if current_time.minute == 0:
      mark = self.trade_values(current_time, marks=True)
      if mark is not None:
        row["hourly_mark_equity"] = mark["equity"]
    self.current = row
    self.rows.append(row)

  def finish(self, end_date, final_cash):
    if any(trade.is_open for trade in LocalTrade.bt_trades_open):
      raise SystemExit("Native end-of-window inventory remains open")
    self.rows.append(
      {
        "date": pd.Timestamp(end_date),
        "equity": final_cash,
        "gross": 0.0,
        "open_pnl": 0.0,
        "peak": max(self.peak, final_cash),
        "drawdown": 1 - final_cash / max(self.peak, final_cash),
        "gross_ratio": 0.0,
        "reference_insolvent": False,
        "open_trades": 0,
        "closed_realized_pnl": final_cash - self.capital,
        "hourly_mark_equity": final_cash,
      }
    )
    frame = pd.DataFrame(self.rows)
    frame.to_parquet(self.output / "reference_equity.parquet", index=False)
    (self.output / "observer_receipt.json").write_text(
      json.dumps(
        {
          "valuation": "current5m futures OPEN; current hourly mark OPEN separately",
          "fees_and_exit_reserve_included": True,
          "funding": "native model accrued to the observed account state",
          "maximum_identity_error_after_tick_basis_adjustment_USDT": self.maximum_identity_error,
          "maximum_single_trade_tick_basis_bias_USDT": self.maximum_basis_rounding_bias,
          "rows": len(frame),
          "max_reference_drawdown": float(frame.drawdown.max()),
          "max_gross_ratio": float(frame.gross_ratio.max()),
          "hourly_mark_rows": int(frame.hourly_mark_equity.notna().sum()),
          "execution_or_live_equity_certified": False,
        },
        indent=2,
      )
      + "\n"
    )


def attach(account, plan, output):
  observer = Observer(account, plan, output)
  for strategy in account.strategylist:
    original_loop = strategy.bot_loop_start

    def loop(current_time, original=original_loop, **kwargs):
      original(current_time=current_time, **kwargs)
      observer.capture(current_time)

    strategy.bot_loop_start = loop
  return observer
