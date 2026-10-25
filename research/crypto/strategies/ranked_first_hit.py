"""Rank known forecasts by predicted dollars at fixed risk; no future price or exit input."""

from pathlib import Path

import pandas as pd

from freqtrade.persistence import Trade
from freqtrade.research.data import sha
from research.crypto.first_hit_paths import SPECS
from research.crypto.strategies.first_hit_dual_v2 import FirstHitDualFixedBase


def rank_pool(frame, source_date, tag, open_pairs, open_layer, open_total, fee, reference):
    sleeve = tag.removesuffix("_first_hit")
    pool = frame.loc[frame.date.eq(source_date) & frame.forecast_sleeve.eq(sleeve)].copy()
    pool["pair"] = pool.symbol.str.removesuffix("USDT") + "/USDT:USDT"
    pool = pool.loc[~pool.pair.isin(open_pairs)].copy()
    spec = SPECS[sleeve]
    distance = (spec["atr_multiple"] * pool.known_atr_fraction).clip(lower=spec["floor"])
    risk, margin = (0.001, 0.04) if sleeve == "small" else (0.005, 0.20)
    loss = distance + fee * (2 - pool.forecast_side * distance)
    pool["estimated_notional"] = (0.99 * reference * risk / loss).clip(upper=2 * reference * margin)
    pool["estimated_profit"] = pool.predicted_net * pool.estimated_notional
    pool["score_key"] = pool.estimated_profit.round(10)
    slots = max(0, min(2 - open_layer, 4 - open_total))
    return pool.sort_values(["score_key", "symbol"], ascending=[False, True]).head(slots), slots


class RankedFirstHitBase(FirstHitDualFixedBase):
    def confirm_trade_entry(
        self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
    ):
        if not super().confirm_trade_entry(
            pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs
        ):
            return False
        if not hasattr(self, "_ranking_frame"):
            record = self.config["research_first_hit_panels"][self.policy]
            path = Path(record["ranking_path"])
            if sha(path) != record["ranking_sha256"]:
                raise ValueError("Frozen ranking input changed")
            self._ranking_frame = pd.read_parquet(path)
            self._rank_allowed = {}
            self._rank_decisions = []
        clock = pd.Timestamp(current_time).floor("5min")
        key = (clock, entry_tag)
        if key not in self._rank_allowed:
            opened = Trade.get_trades_proxy(is_open=True)
            pairs = sorted(t.pair for t in opened)
            layer = sum(t.enter_tag == entry_tag for t in opened)
            source = clock - pd.Timedelta(minutes=5)
            ranked, slots = rank_pool(
                self._ranking_frame,
                source,
                entry_tag,
                pairs,
                layer,
                len(opened),
                float(self.config["fee"]),
                float(self.config["research_initial_equity_usdt"]) * 0.99,
            )
            self._rank_allowed[key] = {
                r.pair: int(r.forecast_side) for r in ranked.itertuples(index=False)
            }
            self._rank_decisions.append(
                {
                    "clock": clock.isoformat(),
                    "trigger_pair": pair,
                    "source_date": source.isoformat(),
                    "tag": entry_tag,
                    "open_pairs": pairs,
                    "open_layer": layer,
                    "open_total": len(opened),
                    "slots": slots,
                    "allowed": ranked[
                        [
                            "pair",
                            "symbol",
                            "forecast_side",
                            "row_id",
                            "estimated_profit",
                            "estimated_notional",
                            "score_key",
                        ]
                    ].to_dict("records"),
                }
            )
        return self._rank_allowed[key].get(pair) == (1 if side == "long" else -1)


class RankedFirstHitLinear(RankedFirstHitBase):
    policy = "LinearStatic"


class RankedFirstHitStatic(RankedFirstHitBase):
    policy = "BoostStatic"


class RankedFirstHitMonthly(RankedFirstHitBase):
    policy = "BoostMonthly"
