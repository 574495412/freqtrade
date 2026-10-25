"""Check stored scorecards against immutable native archives using scalar calculations."""

import argparse
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

from freqtrade.research.data import read, save, sha
from freqtrade.research.workflow import source_identity


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def sample_sharpe(values):
    if len(values) < 2 or statistics.stdev(values) <= 1e-15:
        return None
    return statistics.mean(values) / statistics.stdev(values) * math.sqrt(365)


def audit(folder, freeze):
    document = read(folder / "scorecards.json")
    contract = read(folder / "contract.json")
    executed = folder / "sources/performance_scorecard.py"
    require(
        sha(executed) == document["metric_source_sha256"], "Metric or source identity check failed"
    )
    require(
        contract["metric_source_sha256"] == document["metric_source_sha256"],
        "Metric or source identity check failed",
    )
    require(
        sha(folder / "contract.json") == document["contract_sha256"],
        "Metric or source identity check failed",
    )
    require(
        sha(Path("freqtrade/data/metrics.py")) == document["native_metric_source_sha256"],
        "Metric or source identity check failed",
    )
    identity = source_identity()
    require(read(freeze)["source_identity"] == identity, "Frozen native engine changed")
    errors, observations = ({}, [])

    def equal(field, observed, expected):
        if observed is None or expected is None:
            require(observed is expected, (field, observed, expected))
            return
        difference = abs(observed - expected)
        errors[field] = max(errors.get(field, 0), difference)
        require(
            math.isclose(observed, expected, rel_tol=1e-09, abs_tol=1e-08),
            (field, observed, expected),
        )

    for record in document["accounts"]:
        account = Path(record["source_result"]).parent
        result, plan = (read(account / "result.json"), read(account / "plan.json"))
        archive = next(account.glob("backtest-result-*.zip"))
        for key, path in [
            ("result_sha256", account / "result.json"),
            ("marked_equity_sha256", account / "hourly_equity.parquet"),
            ("native_zip_sha256", archive),
        ]:
            require(
                sha(path) == record["input_identities"][key],
                "Metric or source identity check failed",
            )
        with ZipFile(archive) as native:
            names = [
                n
                for n in native.namelist()
                if n.endswith(".json") and (not n.endswith("_config.json"))
            ]
            require(len(names) == 1, "Metric or source identity check failed")
            stats = json.loads(native.read(names[0]))["strategy"][record["candidate"]]
        for key, value in record["native_closed_trade_fields_unchanged"].items():
            require(value == stats.get(key), key)
        initial = plan["initial_equity_usdt"]
        start, end = (
            pd.Timestamp(record["start"], tz="UTC"),
            pd.Timestamp(record["end"], tz="UTC"),
        )
        hourly = pd.read_parquet(account / "hourly_equity.parquet").set_index("date").equity
        require(stats["starting_balance"] == initial, "Metric or source identity check failed")
        require(
            not any(t["open_timestamp"] < start.value // 1000000 for t in stats["trades"]),
            "Metric or source identity check failed",
        )
        if start not in hourly.index:
            hourly = pd.concat([pd.Series([initial], index=pd.DatetimeIndex([start])), hourly])
        require(
            hourly.index.equals(pd.date_range(start, end, freq="h")),
            "Metric or source identity check failed",
        )
        require(
            all(math.isfinite(x) and x > 0 for x in hourly),
            "Metric or source identity check failed",
        )
        key = (
            f"{account.parents[2].name}__{record['phase']}__{record['candidate']}__"
            f"{record['extra_cost_bps_per_side']}bps.parquet"
        )
        daily = pd.read_parquet(folder / key)
        require(
            list(daily.date) == list(pd.date_range(start + pd.Timedelta(days=1), end, freq="D")),
            "Metric or source identity check failed",
        )
        previous, pnls, returns = (initial, [], [])
        for row in daily.itertuples(index=False):
            current = float(hourly.loc[row.date])
            pnls.append(current - previous)
            returns.append(current / previous - 1)
            equal("daily_pnl", row.pnl_usdt, pnls[-1])
            equal("nav_return", row.nav_return, returns[-1])
            equal("fixed_base_roi", row.fixed_base_roi, pnls[-1] / initial)
            previous = current
        peak, maximum_drawdown = (initial, 0)
        for value in hourly:
            peak = max(peak, value)
            maximum_drawdown = max(maximum_drawdown, (peak - value) / peak)
        marked = record["marked_account"]
        equal("calendar_days", marked["calendar_days"], len(daily))
        equal("net_profit_usdt", math.fsum(pnls), result["net_profit_usdt"])
        equal("marked_profit_usdt", marked["net_profit_usdt"], result["net_profit_usdt"])
        equal("hourly_marked_mdd", marked["hourly_marked_mdd"], maximum_drawdown)
        equal(
            "binance_sharpe_proxy",
            marked["binance_no_transfer_sharpe_proxy"],
            sample_sharpe([p / initial for p in pnls]),
        )
        equal("nav_sharpe", marked["nav_daily_sharpe_sample_365"], sample_sharpe(returns))
        downside = math.sqrt(math.fsum(min(r, 0) ** 2 for r in returns) / len(returns))
        equal(
            "sortino_rms",
            marked["nav_daily_sortino_semideviation_365"],
            statistics.mean(returns) / downside * math.sqrt(365) if downside > 1e-15 else None,
        )
        cagr = (float(hourly.iloc[-1]) / initial) ** (365 / len(daily)) - 1
        equal("cagr_365", marked["cagr_365"], cagr)
        equal(
            "cagr_over_mdd",
            marked["cagr_over_hourly_marked_mdd"],
            cagr / maximum_drawdown if maximum_drawdown > 1e-15 else None,
        )
        require(
            marked["positive_days"] + marked["negative_days"] + marked["flat_days"] == len(daily),
            "Metric or source identity check failed",
        )
        trades = stats["trades"]
        wins = [t["profit_abs"] for t in trades if t["profit_abs"] > 0]
        losses = [t["profit_abs"] for t in trades if t["profit_abs"] < 0]
        whole = record["whole_campaigns"]
        equal("whole_winrate", whole["net_win_rate"], len(wins) / len(trades))
        equal(
            "profit_factor",
            whole["profit_factor"],
            math.fsum(wins) / -math.fsum(losses) if losses else None,
        )
        equal("expectancy_usdt", whole["net_expectancy_usdt"], math.fsum(pnls) / len(trades))
        observations.append(
            {
                "candidate": record["candidate"],
                "phase": record["phase"],
                "cost_bps": record["extra_cost_bps_per_side"],
                "daily_observations": len(daily),
                "input_hashes_unchanged": True,
            }
        )
    require(
        len(observations) == document["account_count"] == 25,
        "Metric or source identity check failed",
    )
    return {
        "validated_utc": datetime.now(UTC).isoformat(),
        "passed": True,
        "scorecards_sha256": sha(folder / "scorecards.json"),
        "audit_source_sha256": sha(Path(__file__)),
        "accounts_checked": len(observations),
        "native_source_files_unchanged": len(identity),
        "scalar_maximum_absolute_errors": errors,
        "accounts": observations,
        "scope": (
            "Independent scalar metric and source checks; earlier Decimal fill and "
            "campaign risk audits remain separate. This is not alpha or live evidence."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.folder / "independent_metric_audit.json"
    if output.exists():
        raise ValueError("Preserve previous audit")
    result = audit(args.folder, args.freeze)
    save(output, result)
    print({k: result[k] for k in ["passed", "accounts_checked", "native_source_files_unchanged"]})
