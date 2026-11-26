"""Report the completed 15bp wallet; no account calculations or strategy selection."""

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
    observation = json.loads((HERE / "first_completed_account_observation.json").read_text())
    contract_path = HERE.parent / "mainline_comparison_contract_20261117_v1/contract.json"
    contract = json.loads(contract_path.read_text())
    baseline = next(row for row in contract["baselines"] if row["cost_bps_per_side"] == 15)
    old = next(row for row in baseline["old_native_outputs_rechecked"] if row["path"].endswith("hourly_equity.parquet"))
    plan = json.loads((HERE / "plan.json").read_text())
    actual_path = Path(plan["output_root"]) / observation["candidate"] / "hourly_equity.parquet"
    sources = [(Path(old["path"]), old["sha256"]), (actual_path, observation["curve_sha256"])]
    frames = []
    for path, expected in sources:
        if sha(path) != expected:
            raise ValueError("Recorded curve changed: " + str(path))
        frame = pd.read_parquet(path)
        expected_dates = pd.date_range(plan["start"], plan["comparison_end"], freq="h")
        if len(frame) != 14592 or not pd.DatetimeIndex(frame.date).as_unit("ns").equals(expected_dates.as_unit("ns")):
            raise ValueError("Incomplete comparison calendar")
        frames.append(frame)

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.25]})
    colors = ["#2563a8", "#dc7435"]
    labels = ["Original 1,000 USDT wallet", "Actual 500 USDT wallet (new; audit pending)"]
    metrics = []
    for frame, label, color in zip(frames, labels, colors, strict=True):
        nav = frame.hourly_mark_equity
        returns = (nav / nav.iloc[0] - 1) * 100
        drawdown = (nav / nav.cummax() - 1) * 100
        axes[0].plot(frame.date, returns, color=color, label=f"{label}: {returns.iloc[-1]:+.2f}%", linewidth=1.5)
        axes[1].plot(frame.date, drawdown, color=color, linewidth=1.1)
        metrics.append({"label": label, "cumulative_return_pct": float(returns.iloc[-1]), "maximum_hourly_drawdown_pct": float(-drawdown.min())})
    axes[0].set_title("NFI X8 | original 40-pair pool | 15bp per side", loc="left", fontsize=14, fontweight="bold")
    axes[0].set_ylabel("Cumulative net return (%)")
    axes[0].legend(loc="upper left", frameon=False)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for axis in axes:
        axis.grid(axis="y", alpha=0.18)
        axis.axhline(0, color="#64748b", linewidth=0.6)
        axis.margins(x=0)
    fig.text(0.09, 0.055, "2025-01-01 to 2026-08-31 23:00 UTC; all 14,592 hourly observations. Cumulative, not annualized.", fontsize=9, color="#334155")
    fig.text(0.09, 0.030, "Conditional old execution model; historical precision/rule gaps retained. Independent 500-wallet funding audit pending.", fontsize=9, color="#334155")
    fig.subplots_adjust(left=0.09, right=0.98, top=0.92, bottom=0.13, hspace=0.12)
    outputs = []
    for extension in ("png", "svg"):
        path = HERE / ("first_completed_15bp_nav_comparison." + extension)
        if path.exists():
            raise FileExistsError(path)
        fig.savefig(path, dpi=160)
        outputs.append({"path": str(path), "sha256": sha(path)})
    plt.close(fig)
    receipt = {"scope": "REPORT_ONLY_ONE_NEW_COMPLETED_WALLET", "sources": [{"path": str(p), "sha256": d} for p, d in sources], "metrics": metrics, "outputs": outputs, "financial_reruns": 0, "independent_audit_pass_claimed": False, "goal_complete": False}
    with (HERE / "first_completed_15bp_plot_receipt.json").open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
