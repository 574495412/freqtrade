"""Plot complete audited native wallets and actual500+500 combinations."""

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

HERE = Path(__file__).resolve().parent
BASE = HERE.with_name("nfi_carry_matched_native_20261115_v1")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    results = json.loads((HERE / "results.json").read_text())
    plan = json.loads((HERE / "plan_attempt3.json").read_text())
    prior = json.loads((BASE / "plan.json").read_text())
    receipts = []
    for scenario, cost in [("conservative", 15), ("lower", 8)]:
        path = Path(prior["output_root"]) / f"nfi_futures_1000_{scenario}_{cost}bps/hourly_equity.parquet"
        curves = [("NFI", path, "#626b79", "-")]
        for policy, label, color, style in [
            ("before_orders", "before funding orders", "#1262c4", "-"),
            ("after_orders", "after funding orders", "#008a90", "--"),
        ]:
            name = f"hawkes_{policy}_1000_{scenario}"
            path = Path(plan["output_root"]) / (name + f"_{cost}bps") / "hourly_equity.parquet"
            curves.append(("Hawkes: " + label, path, color, style))
            combo = next(r for r in results["portfolios"] if r["cost_bps"] == cost and r["funding_ownership"] == policy)
            path = Path(combo["curve"]["path"])
            assert sha(path) == combo["curve"]["sha256"]
            curves.append(("Hawkes + NFI: " + label, path,
                           "#ae5b19" if policy == "before_orders" else "#8a4d7c", style))
        fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
        for label, path, color, style in curves:
            nav = pd.read_parquet(path).set_index("date").hourly_mark_equity
            assert len(nav) == 14592 and nav.iloc[0] == 1000
            assert nav.index[0] == pd.Timestamp(plan["start"])
            assert nav.index[-1] == pd.Timestamp(plan["comparison_end"])
            axes[0].plot(nav.index, nav.to_numpy(), label=label, color=color, ls=style, lw=1.6)
            axes[1].plot(nav.index, 100 * (nav / nav.cummax() - 1), color=color, ls=style, lw=1.6)
            receipts.append({"curve": str(path), "sha256": sha(path)})
        axes[0].legend(ncol=2, frameon=False, fontsize=9, loc="lower left")
        axes[0].axhline(1000, color="#909090", lw=0.7, ls=":")
        axes[0].set_ylabel("Hourly marked NAV (USDT)")
        axes[1].set_ylabel("Drawdown (%)")
        axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        for ax in axes:
            ax.grid(True, alpha=0.15)
            ax.spines[["top", "right"]].set_visible(False)
        fig.suptitle(f"Source Hawkes / NFI: BTC + ETH perpetual 1x | {cost} bp per side", fontsize=14)
        fig.text(0.08, 0.02,
                 "2025-01-01 to 2026-08-31 23:00 UTC | Actual 500+500 USDT wallets | Hourly OPEN before events\n"
                 "Both funding-ownership assumptions; historical OHLCV / synthetic cost model, no independent OOS proof.",
                 fontsize=9, color="#555555")
        fig.tight_layout(rect=(0, 0.075, 1, 0.96))
        for suffix in ["png", "svg"]:
            path = HERE / f"accounts_{cost}bps.{suffix}"
            assert not path.exists()
            fig.savefig(path, dpi=160)
            receipts.append({"figure": str(path), "sha256": sha(path)})
        plt.close(fig)
    (HERE / "figure_receipts.json").write_text(json.dumps({
        "source_results_sha256": sha(HERE / "results.json"), "files": receipts,
        "native_account_returns_not_rescaled": True}, indent=2) + "\n")


if __name__ == "__main__":
    main()
