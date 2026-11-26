"""Plot only independently audited full-window native wallet/reference curves."""

# ruff: noqa: E402
# Select the noninteractive renderer before pyplot is imported.

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    results = json.loads((HERE / "results_attempt2.json").read_text())
    plan = json.loads((HERE / "plan_attempt2.json").read_text())
    accounts = {r["account"]: r for r in results["accounts"]}
    colors = {
        "Wavelet": "#1262c4",
        "Wavelet + NFI": "#16846b",
        "Wavelet + TrendCore": "#9a5b16",
        "NFI": "#5f6876",
        "TrendCore": "#8055a5",
        "Constant thirds": "#ac8e72",
    }
    receipts = []
    for scenario, cost in [("conservative", 20), ("lower", 13)]:
        curves = {}
        name = "wavelet_1000_" + scenario
        path = Path(plan["output_root"]) / (name + f"_{cost}bps") / "hourly_equity.parquet"
        curves["Wavelet"] = pd.read_parquet(path).set_index("date").hourly_mark_equity
        for prefix, label in [
            ("nfi_spot", "NFI"),
            ("trendcore", "TrendCore"),
            ("crp", "Constant thirds"),
        ]:
            source = accounts[f"{prefix}_1000_{scenario}"]["source_curve"]
            if sha(source["path"]) != source["sha256"]:
                raise ValueError("Audited source curve changed")
            curves[label] = pd.read_parquet(source["path"]).set_index("date").hourly_mark_equity
        for portfolio in results["portfolios"]:
            if portfolio["cost_bps"] != cost:
                continue
            label = (
                "Wavelet + NFI"
                if portfolio["legs"][1].startswith("nfi_spot")
                else "Wavelet + TrendCore"
            )
            source = portfolio["curve"]
            if sha(source["path"]) != source["sha256"]:
                raise ValueError("Audited portfolio curve changed")
            curves[label] = pd.read_parquet(source["path"]).set_index("date").hourly_mark_equity
        fig, axes = plt.subplots(
            2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
        )
        for label, nav in curves.items():
            if nav.iloc[0] != 1000:
                raise ValueError("Only actual1000USDT initial wallets/portfolios may be plotted")
            width = 2.2 if label.startswith("Wavelet") else 1.15
            alpha = 1 if label.startswith("Wavelet") else 0.75
            axes[0].plot(
                nav.index, nav.to_numpy(), label=label, color=colors[label], lw=width, alpha=alpha
            )
            axes[1].plot(
                nav.index,
                100 * (nav / nav.cummax() - 1),
                color=colors[label],
                lw=width,
                alpha=alpha,
            )
        axes[0].axhline(1000, color="#909090", lw=0.7, ls=":")
        axes[0].set_ylabel("Hourly marked NAV (USDT)")
        axes[0].legend(ncol=3, frameon=False, loc="upper left", fontsize=9)
        axes[1].set_ylabel("Drawdown (%)")
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        for ax in axes:
            ax.grid(True, alpha=0.15)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle(
            f"Frozen source wavelet: complete matched cash accounts | {cost} bp per side",
            fontsize=14,
        )
        fig.text(
            0.08,
            0.02,
            "2025-01-01 to 2026-08-31 23:00 UTC | Actual500+500USDT sleeves | "
            "Hourly OPEN valuation before fills, closing-fee reserve included\n"
            "Historical OHLCV/flat-cost model; source order policies differ; independent OOS not established.",
            fontsize=9,
            color="#555555",
        )
        fig.tight_layout(rect=(0, 0.075, 1, 0.96))
        for suffix in ["png", "svg"]:
            path = HERE / f"accounts_{cost}bps.{suffix}"
            if path.exists():
                raise ValueError("Preserve existing figure")
            fig.savefig(path, dpi=160)
            receipts.append({"path": str(path), "sha256": sha(path)})
        plt.close(fig)
    (HERE / "figure_receipts.json").write_text(
        json.dumps(
            {
                "figures": receipts,
                "source_results_sha256": sha(HERE / "results_attempt2.json"),
                "source_returns_not_scaled_or_reweighted": True,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
