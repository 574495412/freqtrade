"""Register and prepare the 2024 window with the existing monthly archive pipeline."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt, save

from . import annual_archives as collector
from . import annual_inputs as normalizer
from .accounts import write_once
from .inputs import HERE, OUT


DEST = OUT / "annual_2024"
SOURCE_2025 = OUT / "annual_2025"
DAILY = SOURCE_2025 / "factor_2024/input.json"
PROTOCOL = HERE / "annual_2024_protocol.json"


def register():
    policy = read(PROTOCOL)
    start, end = map(pd.Timestamp, policy["window"])
    daily = read(DAILY)
    checked(daily["source_builder"])
    checked(daily["source_collection"])
    assert not daily["missing_rank_dates"]
    calendar = pd.date_range(start, end, freq="D", inclusive="left")
    days = {}
    for clock in calendar:
        key = clock.date().isoformat()
        group = daily["days"][key]
        assert len(group["pairs"]) == len(set(group["pairs"])) == 80
        assert pd.Timestamp(group["latest_input_close"]) < clock
        days[key] = group
    universe = sorted(set().union(*(set(group["pairs"]) for group in days.values())))
    inputs = read(checked(daily["source_collection"]))
    checked(inputs["market_snapshot"])
    cases, daily_sources = {}, {}
    for pair in universe:
        origin = inputs["cases"][pair]
        snapshot = daily["daily_sources"][pair]
        rows = read(checked(snapshot))
        assert rows
        daily_sources[pair] = snapshot
        oldest_observed = pd.Timestamp(int(rows[0][0]), unit="ms", tz="UTC")
        # The input listing metadata and the two exact daily sources can disagree.
        listing = min(pd.Timestamp(origin["onboard"]), pd.Timestamp(origin["daily_first"]), oldest_observed)
        jobs = []
        for family, interval, tag, stop in (
            ("klines", "5m", "5m", end),
            ("markPriceKlines", "5m", "mark_and_funding", end),
            ("fundingRate", None, "mark_and_funding", end),
            ("klines", "4h", "4h", pd.Timestamp(policy["warmup"]["5m"])),
            ("klines", "1d", "1d", end),
        ):
            first = max(listing, pd.Timestamp(policy["warmup"][tag]))
            last = stop - pd.Timedelta(seconds=1)
            if first <= last:
                jobs.extend([family, str(month), interval] for month in pd.period_range(
                    first.tz_localize(None), last.tz_localize(None), freq="M",
                ))
        cases[pair] = {
            "symbol": origin["symbol"], "onboard": origin["onboard"],
            "daily_first": min(oldest_observed, pd.Timestamp(origin["daily_first"])).isoformat(),
            "sources": jobs,
        }
    schedule = write_once(DEST / "schedule.json", {"days": days, "union": universe,
                                                   "daily_sources": daily_sources})
    return write_once(DEST / "raw_plan.json", {
        "protocol": receipt(PROTOCOL), "schedule": schedule, "ranking_source": receipt(DAILY),
        "parent": daily["source_collection"], "market_snapshot": inputs["market_snapshot"],
        "cases": cases, "collector": receipt(Path(collector.__file__)),
        "normalizer": receipt(Path(normalizer.__file__)), "orders_enabled": False,
    })


def inventory(plan, available):
    counts = Counter()
    identity_collisions = 0
    for case in plan["cases"].values():
        for family, month, interval in case["sources"]:
            name = f"{case['symbol']}-{interval or family}-{month}.zip"
            known = available.get((family, name), ())
            counts["exact_local_identity" if known else "public_fetch_required"] += 1
            if len({row[2] for row in known}) > 1:
                identity_collisions += 1
    return {**dict(counts), "local_identity_conflicting_checksums": identity_collisions,
            "total_archives": sum(counts.values())}


def capture(plan, reference, known, pairs):
    assert shutil.disk_usage(DEST).free > collector.MIN_FREE
    (DEST / "pairs").mkdir(exist_ok=True)
    records = {}
    finished, requested = 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        work = {
            pool.submit(collector.collect_pair, (pair, {
                **plan["cases"][pair], "sources": [tuple(job) for job in plan["cases"][pair]["sources"]],
            }), known): pair for pair in pairs
        }
        for job in as_completed(work):
            path, valid, total = job.result()
            records[work[job]] = path
            finished += valid
            requested += total
            if len(records) % 5 == 0 or len(records) == len(pairs):
                save(DEST / "intake_progress.json", {
                    "raw_plan": reference, "contracts_finished": len(records), "target_contracts": len(pairs),
                    "valid_archive_count": finished, "attempted_archive_count": requested,
                    "already_normalized": False, "pair_receipts": records, "orders_enabled": False,
                })
                print("coverage", len(records), len(pairs), finished, requested, flush=True)


def normalize_sample(plan, reference, pairs):
    schedule = read(checked(plan["schedule"]))
    policy = read(checked(plan["protocol"]))
    (DEST / "candles/futures").mkdir(parents=True, exist_ok=True)
    (DEST / "funding_clocks").mkdir(exist_ok=True)
    records = {}
    for pair in pairs:
        try:
            records[pair] = normalizer.prepare(pair, plan["cases"][pair], schedule, policy, {})
        except (AssertionError, ValueError, KeyError, OSError) as error:
            records[pair] = {"state": "blocked", "reason": str(error)}
    save(DEST / "normalization_preflight.json", {
        "raw_plan": reference, "records": records,
        "normalized_contracts": sum(row["state"] == "normalized" for row in records.values()),
        "native_wallets_run": 0, "funded_test_ready": False, "orders_enabled": False,
    })
    print("normalization", {pair: row["state"] for pair, row in records.items()}, flush=True)


def normalize_all(plan, reference, explicit_boundaries):
    completed = read(DEST / "intake_progress.json")
    assert completed["raw_plan"] == reference
    assert set(completed["pair_receipts"]) == set(plan["cases"])
    if explicit_boundaries is None:
        explicit_boundaries = checked(write_once(DEST / "source_boundaries.json", {}))
    boundaries = receipt(explicit_boundaries)
    exceptions = read(checked(boundaries))
    schedule = read(checked(plan["schedule"]))
    policy = read(checked(plan["protocol"]))
    (DEST / "candles/futures").mkdir(parents=True, exist_ok=True)
    (DEST / "funding_clocks").mkdir(exist_ok=True)
    summary = DEST / "normalization.json"
    records = {}
    for prior in (DEST / "normalization_preflight.json", summary):
        if not prior.is_file():
            continue
        previous = read(prior)
        assert previous["raw_plan"] == reference
        previous_proofs = (read(checked(previous["boundary_evidence"]))
                           if "boundary_evidence" in previous else {})
        for pair, row in previous["records"].items():
            if row["state"] != "normalized" or previous_proofs.get(pair) != exceptions.get(pair):
                continue
            checked(row["archive_index"])
            checked(row["funding_original_clock"])
            if "boundary_adapter" in row:
                checked(row["boundary_adapter"])
            for key, digest in row["files"].items():
                checked({"path": DEST / "candles" / key, "sha256": digest})
            records[pair] = row

    def checkpoint():
        ready = {pair: row for pair, row in records.items() if row["state"] == "normalized"}
        all_ready = set(ready) == set(plan["cases"])
        save(summary, {"raw_plan": reference, "records": records, "boundary_evidence": boundaries,
                       "normalizer": plan["normalizer"], "normalized_pairs": len(ready),
                       "native_inputs_admitted": all_ready, "orders_enabled": False})
        if all_ready:
            files = {key: digest for row in ready.values() for key, digest in row["files"].items()}
            assert len(files) == 8 * len(ready)
            save(DEST / "normalized-receipts.json", files)
        print("native normalization", len(ready), len(plan["cases"]), "admitted", all_ready, flush=True)

    for pair, case in plan["cases"].items():
        if pair in records:
            continue
        if shutil.disk_usage(DEST).free < collector.MIN_FREE:
            checkpoint()
            raise OSError("Disk reserve reached during 2024 native input normalization")
        checked(completed["pair_receipts"][pair])
        try:
            proof = exceptions.get(pair, {})
            if "public_funding_probe" in proof or "daily_gap_public_probe" in proof:
                from .verified_input_boundaries import prepare as prepare_verified_boundary
                records[pair] = prepare_verified_boundary(DEST, pair, case, schedule, policy, proof)
            else:
                records[pair] = normalizer.prepare(pair, case, schedule, policy, exceptions)
        except (AssertionError, ValueError, KeyError, OSError) as error:
            records[pair] = {"state": "blocked", "reason": str(error)}
        if len(records) % 10 == 0:
            checkpoint()
    checkpoint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("inventory", "capture", "normalize-sample", "normalize", "register-accounts"))
    parser.add_argument("--pair", action="append")
    parser.add_argument("--boundary-evidence", type=Path)
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    # These archive helpers run in this research subprocess only. The older
    # 2025 plans and every trading service retain their existing paths.
    collector.DEST = normalizer.DEST = DEST
    reference = register()
    plan = read(checked(reference))
    pairs = args.pair or list(plan["cases"])
    assert len(pairs) == len(set(pairs)) and set(pairs) <= set(plan["cases"])
    if args.stage == "normalize-sample":
        assert args.pair, "Name the preflight contracts explicitly"
        normalize_sample(plan, reference, pairs)
        return
    if args.stage == "normalize":
        assert args.pair is None
        normalize_all(plan, reference, args.boundary_evidence)
        return
    if args.stage == "register-accounts":
        assert args.pair is None
        from .annual_register import main as register_native_accounts
        register_native_accounts(DEST)
        return
    known = collector.local_archives()
    counts = inventory(plan, known)
    save(DEST / "source_inventory.json", {
        "raw_plan": reference, "days": len(read(checked(plan["schedule"]))["days"]),
        "contracts": len(plan["cases"]), **counts,
        "input_count_status": "Local name and declared checksum census; byte admission occurs during capture",
        "orders_enabled": False,
    })
    print("source inventory", len(plan["cases"]), counts, flush=True)
    if args.stage == "capture":
        capture(plan, reference, known, pairs)


if __name__ == "__main__":
    main()
