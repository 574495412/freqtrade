"""Standalone native-account figure; plot data are actual funded sleeve sums."""

import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


HERE = Path(__file__).resolve().parent


def main():
    result = json.loads((HERE / "comparison.json").read_text())
    policies = ["SourceSessions", "TimedLongSessions", "PassiveLong"]
    names = ["Session sign rules (local rollover)", "Timed long: same rollover", "Passive long"]
    colors = ["#176b75", "#ce763c", "#74659d"]
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.8), sharey=True)
    for ax, cost in zip(axes, [15, 8], strict=True):
        for policy, name, color in zip(policies, names, colors, strict=True):
            nav = pd.read_parquet(HERE / f"{policy}_{cost}bps_combined.parquet").iloc[:, 0]
            record = next(
                r
                for r in result["combinations"]
                if r["policy"] == policy and r["cost_bps_per_side"] == cost
            )
            full = record["periods"][0]
            label = (
                f"{name}: {full['net_return']:+.1%} / DD {full['maximum_hourly_mark_drawdown']:.1%}"
            )
            ax.plot(nav.index, nav.values, lw=1.6, color=color, label=label)
        ax.axhline(1000, color="#8c939a", lw=0.8, ls="--")
        ax.set_title(f"{cost} bps per side", loc="left", fontsize=13, fontweight="bold")
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.tick_params(axis="x", labelrotation=20)
        ax.grid(axis="y", color="#e4e8eb")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.23), frameon=False, fontsize=9)
    axes[0].set_ylabel("Native account NAV (USDT)")
    fig.suptitle(
        "BTC + ETH: funded session rules fail both cost cases",
        x=0.08,
        ha="left",
        fontsize=17,
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.91,
        "2025-01-01 00:00 to 2026-08-31 23:00 UTC | two actual 500-USDT wallets | 1x isolated",
        color="#505c65",
    )
    fig.text(
        0.08,
        0.025,
        "Hourly marks: open PNL/funding and close-fee reserve. Timed rules use a 5m gap.\n"
        "Rollover differs from paper turnover; selection bias remains. No live/OOS claim.",
        fontsize=9,
        color="#505c65",
    )
    fig.subplots_adjust(left=0.08, right=0.98, top=0.83, bottom=0.30, wspace=0.12)
    fig.savefig(HERE / "account-comparison.png", dpi=170, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
