"""Full native replay NAV and cash-feedback differences from inclusive model."""

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


HERE = Path(__file__).resolve().parent


def main():
    data = json.loads((HERE / "comparison.json").read_text())
    fig, axes = plt.subplots(
        2, 2, figsize=(12.8, 7.5), sharex="col", gridspec_kw={"height_ratios": [2.2, 1]}
    )
    colors = {"inclusive": "#80888b", "before_orders": "#177477", "after_orders": "#ba6b35"}
    names = {
        "inclusive": "Prior inclusive",
        "before_orders": "Before orders",
        "after_orders": "After orders",
    }
    for col, cost in enumerate([15, 8]):
        reference = pd.read_parquet(
            HERE / f"BoundaryCarrySource_inclusive_{cost}bps_combined.parquet"
        ).iloc[:, 0]
        for mode, color in colors.items():
            nav = pd.read_parquet(
                HERE / f"BoundaryCarrySource_{mode}_{cost}bps_combined.parquet"
            ).iloc[:, 0]
            full = next(
                r["periods"][0]
                for r in data["combinations"]
                if r["strategy"] == "BoundaryCarrySource"
                and r["ownership"] == mode
                and r["cost_bps_per_side"] == cost
            )
            label = f"{names[mode]}: {full['net_return']:+.2%}"
            axes[0, col].plot(nav.index, nav.values, color=color, lw=1.4, label=label)
            if mode != "inclusive":
                axes[1, col].plot(
                    nav.index, nav.values - reference.values, color=color, lw=1.1
                )
        axes[0, col].set_title(f"{cost} bps per side", loc="left", fontweight="bold")
        axes[0, col].legend(frameon=False, fontsize=9)
        axes[0, col].axhline(1000, color="#a4afb4", lw=0.7, ls=":")
        axes[1, col].axhline(0, color="#a4afb4", lw=0.7)
        for row in [0, 1]:
            axes[row, col].spines[["top", "right"]].set_visible(False)
            axes[row, col].grid(axis="y", color="#e3e9ed")
        axes[1, col].xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        axes[1, col].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axes[1, col].tick_params(axis="x", labelrotation=20)
    axes[0, 0].set_ylabel("Native wallet sum (USDT)")
    axes[1, 0].set_ylabel("NAV minus inclusive\n(USDT)")
    fig.suptitle(
        "Funding ownership: full native replay still fails acceptance",
        x=0.08,
        ha="left",
        fontweight="bold",
        fontsize=16,
    )
    fig.text(
        0.08,
        0.92,
        "2025-01-01 to 2026-08-31 23:00 UTC | actual BTC 500 + ETH 500 wallets",
        fontsize=11,
        color="#52616a",
    )
    fig.text(
        0.08,
        0.025,
        "Top: hourly account marks. Bottom: replays include cash/quantity feedback.\n"
        "Nominal-clock research scenarios, not certified settlement or bounds on actual returns.",
        fontsize=9,
        color="#52616a",
    )
    fig.subplots_adjust(left=0.09, right=0.98, top=0.85, bottom=0.14, wspace=0.2, hspace=0.17)
    fig.savefig(HERE / "funding-comparison.png", dpi=170, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
