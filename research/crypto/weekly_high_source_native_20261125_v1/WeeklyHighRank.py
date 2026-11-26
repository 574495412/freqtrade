"""Fixed-universe, close-confirmed weekly rank through native Freqtrade orders."""

import hashlib
import io
from pathlib import Path

import numpy as np
import pandas as pd

from research.crypto.donchian60_tail_native_20261125_v1.Donchian60Tail import LONG, SHORT, Donchian60Tail
from research.crypto.weekly_high_source_native_20261125_v1.zoo.core.week_high import compute


def weekly_calendar(receipts):
    closes, highs = {}, {}
    for pair, record in receipts.items():
        data = Path(record["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"Daily input changed for {pair}")
        frame = pd.read_feather(io.BytesIO(data)).set_index("date")
        dates = frame.index.as_unit("ns")
        if not len(dates) or not dates.equals(pd.date_range(dates[0], periods=len(dates), freq="D").as_unit("ns")):
            raise ValueError(f"Expected a contiguous daily calendar for {pair}")
        close_time = frame.index + pd.Timedelta(days=1)
        complete = frame.close.notna().rolling(7).sum().eq(7)
        keep = (close_time.dayofweek == 0) & complete
        closes[pair] = frame.close.loc[keep].set_axis(close_time[keep])
        highs[pair] = frame.high.rolling(7).max().loc[keep].set_axis(close_time[keep])
    panel = {"close": pd.DataFrame(closes), "high": pd.DataFrame(highs)}
    score = compute(panel).dropna()
    if not np.isfinite(score).all().all() or score.empty:
        raise ValueError("Usable weekly ranks are empty or nonfinite")
    rank = score.rank(axis=1, method="first")
    return rank.gt(rank.shape[1] - 3).astype(int) - rank.le(3).astype(int)


class WeeklyHighRank(Donchian60Tail):
    def bot_start(self, **kwargs):
        super().bot_start(**kwargs)
        receipts = self.config["research_donchian60_daily"]
        self.calendar = weekly_calendar({pair: receipts[pair] for pair in self.config["exchange"]["pair_whitelist"]})

    def populate_indicators(self, dataframe, metadata):
        dataframe["dc60_direction"] = dataframe.date.map(self.calendar[metadata["pair"]]).fillna(0).astype("int8")
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe = super().populate_entry_trend(dataframe, metadata)
        dataframe["enter_tag"] = dataframe["enter_tag"].replace({LONG: "week_high_long", SHORT: "week_high_short"})
        return dataframe

    def entry_row(self, pair, time, side, tag):
        clock = pd.Timestamp(time)
        if clock.weekday() != 0 or clock - clock.normalize() != pd.Timedelta(hours=1):
            return None
        direction = 1 if side == "long" else -1
        rank = self.calendar[pair].get(clock.normalize())
        return True if rank == direction and tag == f"week_high_{side}" else None

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake,
                            max_stake, leverage, entry_tag, side, **kwargs):
        try:
            if self.entry_row(pair, current_time, side, entry_tag) is not True:
                return 0.0
            stake = min(proposed_stake, max_stake)
            return stake if min_stake is None or stake >= min_stake else 0.0
        except Exception as error:
            self.callback_errors.append(("custom_stake_amount", str(error)))
            return 0.0

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_time.weekday() == 0 and self.midnight(current_time):
            self.source_exits.append({"pair": pair, "entry": trade.open_date_utc.isoformat(),
                                      "decision_clock": current_time.isoformat(), "reason": "week_end"})
            return "week_end"
        return None
