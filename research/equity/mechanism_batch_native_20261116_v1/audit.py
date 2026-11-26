"""Independent arithmetic: no Registry, candidate strategy or factor-core import."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent


def zscore(values):
    result = np.full(values.shape, np.nan)
    for i, row in enumerate(values):
        valid = np.isfinite(row)
        if valid.sum() > 1:
            mean = math.fsum(row[valid]) / valid.sum()
            sd = math.sqrt(
                math.fsum((float(v) - mean) ** 2 for v in row[valid]) / (valid.sum() - 1)
            )
            if sd:
                result[i, valid] = (row[valid] - mean) / sd
    return result


def independent_scores(folder, identities):
    price = pd.read_parquet(folder / "close.parquet").to_numpy()
    volume = pd.read_parquet(folder / "volume.parquet").to_numpy()
    opens = pd.read_parquet(folder / "open.parquet").to_numpy()
    returns = np.full(price.shape, np.nan)
    returns[1:] = price[1:] / price[:-1] - 1
    market = np.nanmean(returns[1:], axis=1)
    market = np.r_[np.nan, market]
    output = {}
    if "academic_bab" in identities:
        beta = np.full(price.shape, np.nan)
        for t in range(252, len(price)):
            x = market[t - 251 : t + 1]
            y = returns[t - 251 : t + 1]
            centered = x - x.mean()
            beta[t] = -(centered[:, None] * (y - y.mean(axis=0))).sum(axis=0) / (
                centered @ centered
            )
        output["academic_bab"] = zscore(beta)
    if "academic_illiq" in identities:
        ratio = np.full(price.shape, np.nan)
        # Source safe_div uses signed 1e-12 twice, rather than exact division.
        ratio[1:] = np.abs((price[1:] - price[:-1]) / (price[:-1] + 1e-12)) / (
            price[1:] * volume[1:] + 1e-12
        )
        mean = np.full(price.shape, np.nan)
        for t in range(21, len(price)):
            mean[t] = ratio[t - 20 : t + 1].mean(axis=0)
        output["academic_illiq"] = zscore(mean)
    if "alpha101_006" in identities:
        corr = np.full(price.shape, np.nan)
        for t in range(9, len(price)):
            x = opens[t - 9 : t + 1]
            y = volume[t - 9 : t + 1]
            x = x - x.mean(axis=0)
            y = y - y.mean(axis=0)
            den = np.sqrt((x * x).sum(axis=0) * (y * y).sum(axis=0))
            np.divide(-(x * y).sum(axis=0), den, out=corr[t], where=den > 0)
        output["alpha101_006"] = corr
    return output


def rank(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        end = i + 1
        while end < len(order) and values[order[end]] == values[order[i]]:
            end += 1
        for j in range(i, end):
            result[order[j]] = (i + 1 + end) / 2
        i = end
    return result


def spearman(left, right):
    a, b = rank(left), rank(right)
    am, bm = math.fsum(a) / len(a), math.fsum(b) / len(b)
    num = math.fsum((x - am) * (y - bm) for x, y in zip(a, b, strict=True))
    den = math.sqrt(math.fsum((x - am) ** 2 for x in a) * math.fsum((y - bm) ** 2 for y in b))
    return num / den if den else None


def diagnostic(folder):
    f = pd.read_csv(folder / "factor.csv", index_col=0, parse_dates=True)
    r = pd.read_csv(folder / "returns.csv", index_col=0, parse_dates=True)
    ic = pd.read_csv(folder / "native/ic_series.csv", index_col=0, parse_dates=True).iloc[:, 0]
    groups = pd.read_csv(folder / "native/group_equity.csv", index_col=0, parse_dates=True)
    running = [1.0, 1.0, 1.0]
    checked_ic = checked_nav = 0
    max_error = 0.0
    for at, row in f.iterrows():
        valid = row.notna() & r.loc[at].notna()
        x, y = row[valid].tolist(), r.loc[at, valid].tolist()
        expected = spearman(x, y) if len(x) >= 5 else None
        if expected is not None:
            if at not in ic.index or abs(ic.loc[at] - expected) > 1e-12:
                raise AssertionError("Independent IC differs")
            checked_ic += 1
        if len(x) < 3:
            continue
        order = sorted(range(len(x)), key=lambda i: (x[i], i))
        bins = [[], [], []]
        for k, i in enumerate(order):
            g = 0 if k <= (len(x) - 1) / 3 else (1 if k <= (len(x) - 1) * 2 / 3 else 2)
            bins[g].append(y[i])
        for g in range(3):
            running[g] *= 1 + math.fsum(bins[g]) / len(bins[g])
        err = max(abs(a - b) for a, b in zip(running, groups.loc[at], strict=True))
        max_error = max(max_error, err)
        checked_nav += 3
    if checked_ic != len(ic) or checked_nav != groups.size or max_error > 1e-9:
        raise AssertionError("Missing or divergent independent diagnostic rows")
    response = json.loads((folder / "native_response.json").read_text())
    mean = math.fsum(ic) / len(ic)
    sd = math.sqrt(math.fsum((v - mean) ** 2 for v in ic) / (len(ic) - 1))
    if response["ic_mean"] != round(mean, 6) or response["ir"] != round(mean / sd, 4):
        raise AssertionError("IC summary differs")
    return {"ic_rows": checked_ic, "group_nav_cells": checked_nav, "max_nav_error": max_error}


def main():
    native = json.loads((HERE / "native_result.json").read_text())
    result = json.loads((HERE / "predictive_result.json").read_text())
    score_checks = []
    label_checks = 0
    for cohort in native["cohorts"]:
        folder = HERE / "native" / cohort["market"]
        for identity, expected in independent_scores(folder, cohort["factors"]).items():
            actual = pd.read_parquet(folder / f"{identity}.parquet").to_numpy()
            if not np.array_equal(np.isnan(actual), np.isnan(expected)):
                raise AssertionError(f"Independent missingness differs: {identity}")
            np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-7, equal_nan=True)
            score_checks.append(
                {
                    "market": cohort["market"],
                    "factor": identity,
                    "finite_cells": int(np.isfinite(actual).sum()),
                    "max_abs_error": float(np.nanmax(np.abs(actual - expected))),
                }
            )
        opens = pd.read_parquet(folder / "open.parquet")
        for horizon in [5, 1]:
            base = HERE / "predictive" / cohort["market"] / str(horizon)
            clocks = json.loads((base / "label_clocks.json").read_text())
            first = opens.index.searchsorted(pd.Timestamp("2025-01-01", tz="UTC"))
            previous_exit = None
            for n, c in enumerate(clocks):
                i = opens.index.get_loc(pd.Timestamp(c["entry"]))
                j = opens.index.get_loc(pd.Timestamp(c["exit"]))
                if (
                    i != first + n * horizon
                    or j - i != horizon
                    or opens.index[i - 1] != pd.Timestamp(c["observation"])
                ):
                    raise AssertionError("Label anchor/horizon changed")
                if (
                    not pd.Timestamp(c["observed_close"])
                    < pd.Timestamp(c["entry_open"])
                    < pd.Timestamp(c["exit_open"])
                ):
                    raise AssertionError("Label sees future")
                if previous_exit is not None and pd.Timestamp(c["entry_open"]) < previous_exit:
                    raise AssertionError("Labels overlap")
                previous_exit = pd.Timestamp(c["exit_open"])
            for identity in cohort["factors"]:
                for split in ["all", "2025", "2026"]:
                    target = base / identity / split
                    labels = pd.read_csv(target / "returns.csv", index_col=0, parse_dates=True)
                    exposures = pd.read_csv(target / "factor.csv", index_col=0, parse_dates=True)
                    raw = pd.read_parquet(folder / f"{identity}.parquet")
                    for c in clocks:
                        at = pd.Timestamp(c["observation"])
                        if at not in labels.index:
                            continue
                        y = (
                            opens.loc[pd.Timestamp(c["exit"])] / opens.loc[pd.Timestamp(c["entry"])]
                            - 1
                        )
                        np.testing.assert_allclose(labels.loc[at], y, rtol=1e-12, atol=1e-12)
                        np.testing.assert_allclose(
                            exposures.loc[at], raw.loc[at], rtol=1e-12, atol=1e-12, equal_nan=True
                        )
                        label_checks += len(y)
    diagnostics = [diagnostic(HERE / r["diagnostic"]) for r in result["results"]]
    audit = {
        "status": "passed",
        "independent_candidate_imports": False,
        "score_checks": score_checks,
        "source_score_cells": sum(r["finite_cells"] for r in score_checks),
        "label_cells_including_overlapping_slices": label_checks,
        "diagnostic_calls": len(diagnostics),
        "ic_rows_including_overlapping_slices": sum(r["ic_rows"] for r in diagnostics),
        "group_nav_cells": sum(r["group_nav_cells"] for r in diagnostics),
        "max_group_nav_error": max(r["max_nav_error"] for r in diagnostics),
        "audited_financial_accounts": 0,
    }
    (HERE / "independent_audit.json").write_text(
        json.dumps(audit, indent=2, allow_nan=False) + "\n"
    )
    print(
        "AUDIT PASS",
        audit["source_score_cells"],
        "source cells;",
        audit["diagnostic_calls"],
        "actual FA calls",
        flush=True,
    )


if __name__ == "__main__":
    main()
