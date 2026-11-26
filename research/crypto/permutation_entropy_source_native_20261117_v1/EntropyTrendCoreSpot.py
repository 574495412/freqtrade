"""New local ordinal-complexity gate on the sealed source-derived trend policy."""

import pandas as pd
from OrdinalEntropy import entropy_gate
from TrendCoreSpot import TrendCoreSpot


class EntropyTrendCoreSpot(TrendCoreSpot):
    def ft_bot_start(self, **kwargs):
        if self.config.get("research_ordinal_entropy") is not True:
            raise ValueError("Frozen research entropy hypothesis required")
        result = super().ft_bot_start(**kwargs)
        entropy, threshold, gate = entropy_gate(pd.read_parquet(self.config["signal_prices"]))
        clocks = self.research_weights.index
        selected = gate.reindex(clocks).fillna(False)
        self.research_trace["base_weights"] = self.research_trace["weights"]
        self.research_weights = self.research_weights * selected
        self.research_trace["weights"] = [
            dict(day=str(clock), **row.to_dict()) for clock, row in self.research_weights.iterrows()
        ]
        self.research_trace["entropy"] = [
            {
                "clock": str(clock),
                "pair": pair,
                "entropy": None if pd.isna(entropy.at[clock, pair]) else entropy.at[clock, pair],
                "preceding_year_median": None
                if pd.isna(threshold.at[clock, pair])
                else threshold.at[clock, pair],
                "gate": bool(selected.at[clock, pair]),
            }
            for clock in clocks
            for pair in self.research_weights
        ]
        return result
