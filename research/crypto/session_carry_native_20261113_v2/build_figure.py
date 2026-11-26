"""Standalone figure from audited, actually funded native wallet sums."""

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


HERE = Path(__file__).resolve().parent


def main():
    result = json.loads((HERE / "comparison.json").read_text())
    curves = [
        ("BoundaryCarrySource", "Carry at boundary", "#146f73", "-"),
        ("ClockMatchedCarrySource", "Carry, old entry clock", "#3e80b0", "-"),
        ("BoundaryCarryLong", "Matched constant long", "#78659c", "-"),
        ("Old_SourceSessions", "Prior forced rollover", "#c57a3b", "--"),
    ]
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.5), sharey=True)
    for ax, cost in zip(axes, [15, 8], strict=True):
        for policy, name, color, style in curves:
            nav = pd.read_parquet(HERE / f"{policy}_{cost}bps_combined.parquet").iloc[:, 0]
            record = next(
                r
                for r in result["combinations"]
                if r["policy"] == policy and r["cost_bps_per_side"] == cost
            )
            full = record["periods"][0]
            label = (
                f"{name}: {full['net_return']:+.1%}, DD {full['maximum_hourly_mark_drawdown']:.1%}"
            )
            ax.plot(nav.index, nav.values, color=color, ls=style, lw=1.7, label=label)
        ax.axhline(1000, color="#89959a", ls=":", lw=0.9)
        ax.set_title(f"{cost} bps per side", loc="left", fontweight="bold")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.tick_params(axis="x", labelrotation=20)
        ax.grid(axis="y", color="#e4e9ec")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.23), frameon=False, fontsize=9)
    axes[0].set_ylabel("Actual native wallet sum (USDT)")
    fig.suptitle(
        "Carry improves the session model; acceptance still fails",
        x=0.08,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.91,
        "2025-01-01 to 2026-08-31 23:00 UTC | BTC 500 + ETH 500 | no transfers",
        color="#52616a",
    )
    fig.text(
        0.08,
        0.025,
        "Hourly marks, native inclusive funding clocks, close-fee reserve. "
        "Boundary ownership is conditional.\n"
        "Fixed held quantities permit exposure drift. Known history; "
        "no independent OOS/live qualification.",
        fontsize=9,
        color="#52616a",
    )
    fig.subplots_adjust(left=0.08, right=0.98, top=0.82, bottom=0.32, wspace=0.12)
    fig.savefig(HERE / "account-comparison.png", dpi=170, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
