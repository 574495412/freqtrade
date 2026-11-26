"""Compare finished continuous and independent native 8 bp accounts for 2025."""

import argparse
from pathlib import Path

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .recent_two_year_prefix_audit import BOUNDARY, STEP, fill_prefix, load_completed
from .recent_two_year_run import RUNS


def compare(slots):
    original = "fixed_boost_12" if slots == 12 else "boost_14"
    annual = RUNS.parent / "bounded_native_blocks" / f"annual_{original}_8bp"
    live = RUNS / f"full_{slots}_8bp"
    books = {name: load_completed(folder) for name, folder in (("annual", annual), ("continuous", live))}
    for _, _, refs in books.values():
        config = read(checked(refs[".json"]))
        assert config["fee"] == 0.0008 and config["max_open_trades"] == slots
    old_nav, old_trades, old_refs = books["annual"]
    new_nav, new_trades, new_refs = books["continuous"]
    assert len(old_nav) == 105120 and old_nav.index[-1] == BOUNDARY - STEP
    assert len(new_nav) == 199872 and new_nav.index[0] == old_nav.index[0] and BOUNDARY in new_nav.index
    cutoff = BOUNDARY - 2 * STEP
    historical_fills, continued_fills = (fill_prefix(book, cutoff) for book in (old_trades, new_trades))
    border = BOUNDARY.value // 1_000_000
    carry_old = sorted((t["pair"], t["is_short"], t["open_timestamp"])
                       for t in old_trades if t["exit_reason"] == "force_exit")
    carry_new = sorted((t["pair"], t["is_short"], t["open_timestamp"])
                       for t in new_trades if t["open_timestamp"] < border <= t["close_timestamp"])
    return {
        "checked_marks": len(old_nav.loc[:cutoff]), "checked_fills": len(historical_fills),
        "continuous_fill_count": len(continued_fills), "common_cutoff_utc": cutoff.isoformat(),
        "exact_marks": old_nav.loc[:cutoff].equals(new_nav.loc[:cutoff]),
        "exact_fills": historical_fills == continued_fills,
        "continuous_carry_matched": carry_old == carry_new,
        "annual_forced_campaigns": carry_old, "continued_campaigns": carry_new,
        "annual_native_sources": old_refs, "continuous_native_sources": new_refs,
        "source": receipt(Path(__file__)), "orders_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slots", type=int, choices=(12, 14))
    args = parser.parse_args()
    outcome = compare(args.slots)
    target = HERE / f"recent_two_year_8bp_{args.slots}_verified_prefix.json"
    if target.exists():
        archived = read(target)
        assert all(archived[k] == outcome[k] for k in (
            "checked_marks", "checked_fills", "common_cutoff_utc", "exact_marks",
            "exact_fills", "continuous_carry_matched", "annual_native_sources", "continuous_native_sources",
        ))
    else:
        write_once(target, outcome)
    assert all(outcome[k] for k in ("exact_marks", "exact_fills", "continuous_carry_matched"))
    print(args.slots, outcome["checked_marks"], outcome["checked_fills"], outcome["continued_campaigns"], flush=True)


if __name__ == "__main__":
    main()
