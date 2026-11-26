"""Audit remaining public trading prints at the unchanged historical buy limits."""

from pathlib import Path

import pandas as pd

from research.crypto.nfi40_boost_profit_tail_20261125_v1.run import checked, read, receipt

from .accounts import write_once
from .inputs import HERE
from .top_day_execution import prints


def main():
    source = receipt(HERE / "top_day_execution_result.json")
    initial = read(checked(source))
    inputs = read(checked(initial["freeze"]))
    configurations = {fee: read(checked(read(checked(ref))["account"]["effective_config"]))
                      for fee, ref in inputs["native_accounts"].items()}
    assert all(c["unfilledtimeout"]["unit"] == "minutes" and c["order_types"]["entry"] == "limit"
               for c in configurations.values())
    observed = {}
    for target in initial["targets"]:
        data = prints(target["archive"], pd.Timestamp(target["clock"]))
        by_fee = {}
        for fee, row in target["measures_by_fee"].items():
            wait = configurations[fee]["unfilledtimeout"]["entry"] * 60_000
            limit, qty = row["original_buy_limit"], row["original_quantity"]
            at_limit = data.price.le(limit)
            by_fee[fee] = {}
            for proxy in row["buy_print_price_proxies"]:
                delay = proxy["delay_seconds"]
                start = delay * 1000
                assert start + wait <= 300_000
                active = data.ms.ge(start) & data.ms.lt(start + wait)
                sales = data.loc[active & at_limit & data.seller_taker]
                traded = float(sales.amount.sum())
                by_fee[fee][str(delay)] = {
                    "limit_price": limit,
                    "lowest_print_during_timeout": float(data.loc[active, "price"].min()) if active.any() else None,
                    "prints_at_or_below_old_limit": int((active & at_limit).sum()),
                    "sell_taker_qty_over_order": traded / qty,
                    "enough_sell_taker_print_qty": traded >= qty,
                }
        observed[target["pair"]] = {"entry_clock": target["clock"], "fee": by_fee}
    write_once(HERE / "top_day_delayed_limit_result.json", {
        "source": receipt(Path(__file__)), "base_execution_evidence": source, "measurements": observed,
        "scope": "Original buy limit held fixed, after the prespecified delay up to the configured time budget. "
                 "Public prints describe quantity, not a hypothetical queue position. Ask-based live pricing may change the limit.",
        "orders_enabled": False,
    })
    print("Completed unchanged-limit coverage", len(observed), flush=True)


if __name__ == "__main__":
    main()
