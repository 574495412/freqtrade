"""Research figure from audited native OI confirmation accounts."""

import json
from pathlib import Path

import matplotlib


matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    here = Path(__file__).resolve().parent
    result = json.loads((here / "comparison.json").read_text())
    figure, axis = plt.subplots(figsize=(9.5, 4.8), layout="constrained")
    categories, width = np.array([0, 1]), 0.32
    series = [
        ("OIConfirmedBreakout", "Price breakout + rising OI", "#3772A6"),
        ("PriceBreakoutAvailable", "Price breakout control", "#D78C38"),
    ]
    for index, (key, label, color) in enumerate(series):
        values = [
            next(
                row["net_return"]
                for row in result["accounts"]
                if row["strategy"] == key and row["cost_bps_per_side"] == cost
            )
            * 100
            for cost in [15, 8]
        ]
        bars = axis.bar(categories + (index - 0.5) * width, values, width, label=label, color=color)
        for bar, value in zip(bars, values, strict=True):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                value - 1.5,
                f"{value:.2f}%",
                ha="center",
                va="top",
                fontsize=10,
            )
    axis.set_xticks(categories, ["15 bps / side", "8 bps / side"])
    axis.set_ylim(-68, 0)
    axis.set_ylabel("Cumulative model net return (%)")
    axis.set_title(
        "Open-interest confirmation reduced losses but did not create profit",
        loc="left",
        fontsize=12,
        pad=46,
    )
    axis.text(
        0,
        1.07,
        "2025-01-01 00:00 to 2026-08-31 23:00 UTC | BTC perp | 1x | initial1,000 USDT",
        transform=axis.transAxes,
        fontsize=9,
        color="#555555",
    )
    axis.text(
        0,
        1.025,
        "Both price controls have identical actual trades and all14,592 hourly NAV values",
        transform=axis.transAxes,
        fontsize=9,
        color="#555555",
    )
    axis.grid(axis="y", alpha=0.2)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, frameon=False, fontsize=9)
    figure.savefig(here / "account-comparison.png", dpi=160)


if __name__ == "__main__":
    main()
