"""Independent Decimal forecast-dollar ranking reconstruction and full actual-position audit."""

from collections import Counter
from decimal import Decimal
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_ensemble import raw_trades
from research.crypto.audit_intrabar_small_account import campaign_audit as original_audit


def campaign_audit(folder, name):  # noqa: C901 - independent selection and position invariants
    report = original_audit(folder, name)
    plan, result = read(folder / "plan.json"), read(folder / "result.json")
    policy = next(c["policy"] for c in plan["candidates"] if c["name"] == name)
    source = plan["settings"]["research_first_hit_panels"][policy]
    frame = pd.read_parquet(source["ranking_path"])
    if sha(Path(source["ranking_path"])) != source["ranking_sha256"]:
        raise ValueError("Ranking input changed")
    adapter = read(folder / "runtime_adapter.json")
    if sha(folder / "ranking_decisions.json") != adapter["ranking_decisions_sha256"]:
        raise ValueError("Ranking log changed")
    decisions = read(folder / "ranking_decisions.json")
    calls = pd.read_parquet(folder / "precision_calls.parquet")
    ranked_mode = next(c["ranked"] for c in plan["candidates"] if c["name"] == name)
    if not ranked_mode:
        if decisions:
            raise ValueError("Unranked control made ranking decisions")
        report.update(
            ranking_audit_passed=True,
            ranking_mode="pair_order_control",
            ranking_snapshots_checked=0,
            ranking_decisions_sha256=sha(folder / "ranking_decisions.json"),
            ranking_audit_source_sha256=sha(Path(__file__)),
        )
        save(folder / "campaign_audit.json", report)
        return report
    index = {}
    fee = Decimal(str(plan["fee_per_side"])) + Decimal(result["extra_cost_bps_per_side"]) / 10000
    ref = Decimal(str(plan["initial_equity_usdt"])) * Decimal(".99")
    for d in decisions:
        clock = pd.Timestamp(d["clock"])
        source_date = pd.Timestamp(d["source_date"])
        if source_date != clock - pd.Timedelta(minutes=5):
            raise ValueError("Ranking saw an unavailable forecast clock")
        observed = calls.loc[calls.date.eq(clock) & calls.pair.eq(d["trigger_pair"])]
        if len(observed) != 1:
            raise ValueError("Ranking lacks one actual native state observation")
        actual = observed.iloc[0]
        layer = d["tag"].removesuffix("_first_hit")
        if (
            sorted(actual.open_pairs) != d["open_pairs"]
            or actual.open_total != d["open_total"]
            or actual[f"open_{layer}"] != d["open_layer"]
        ):
            raise ValueError("Ranking used a different open account")
        slots = max(0, min(2 - d["open_layer"], 4 - d["open_total"]))
        pool = frame.loc[frame.date.eq(source_date) & frame.forecast_sleeve.eq(layer)]
        ranked = []
        for row in pool.itertuples(index=False):
            pair = row.symbol.removesuffix("USDT") + "/USDT:USDT"
            if pair in d["open_pairs"]:
                continue
            small = layer == "small"
            floor, multiple = (".008", 2) if small else (".015", 6)
            distance = max(Decimal(floor), multiple * Decimal(str(row.known_atr_fraction)))
            loss = distance + fee * (2 - Decimal(int(row.forecast_side)) * distance)
            risk, margin = (
                (Decimal(".001"), Decimal(".04")) if small else (Decimal(".005"), Decimal(".20"))
            )
            notional = min(Decimal(".99") * ref * risk / loss, 2 * ref * margin)
            score = (Decimal(str(row.predicted_net)) * notional).quantize(Decimal(".0000000001"))
            ranked.append((score, row.symbol, int(row.row_id), pair, int(row.forecast_side)))
        ranked = sorted(ranked, key=lambda r: (-r[0], r[1]))[:slots]
        if slots != d["slots"] or [r[2] for r in ranked] != [r["row_id"] for r in d["allowed"]]:
            raise ValueError("Independent Decimal risk/dollar ranking differs")
        key = (clock, d["tag"])
        if key in index:
            raise ValueError("Admission snapshot created twice")
        index[key] = ({r[3]: r[4] for r in ranked}, slots)
    counts = Counter()
    for trade in raw_trades(folder, name):
        clock = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        key = (clock, trade["enter_tag"])
        if key not in index:
            raise ValueError("Actual entry lacks causal ranking decision")
        allowed, slots = index[key]
        if allowed.get(trade["pair"]) != (-1 if trade["is_short"] else 1):
            raise ValueError("Actual entry was not admitted")
        counts[key] += 1
        if counts[key] > slots:
            raise ValueError("Actual same-clock entries exceeded reserved slots")
    report.update(
        ranking_audit_passed=True,
        ranking_snapshots_checked=len(decisions),
        ranking_decisions_sha256=sha(folder / "ranking_decisions.json"),
        ranking_audit_source_sha256=sha(Path(__file__)),
    )
    save(folder / "campaign_audit.json", report)
    return report
