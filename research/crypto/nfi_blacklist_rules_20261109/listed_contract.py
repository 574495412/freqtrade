"""Use actual contract onboarding instead of manufacturing prelisting candles."""

import sys

import pandas as pd

from research.crypto.nfi_blacklist_rules_20261109.inputs import OUT, PLAN, prior, read, rebind, save


def main(pair):
    rebind()
    market = read(PLAN["original_full_market_snapshot"]["path"])["markets"][pair]
    onboard = pd.to_datetime(int(market["info"]["onboardDate"]), unit="ms", utc=True)
    if onboard >= pd.Timestamp(PLAN["start"]):
        raise ValueError("Post-start listing requires an explicit funding-window extension")
    original_warm = prior.base.WARM
    prior.base.WARM = max(original_warm, onboard.ceil("5min"))
    row = prior.execution_pair(pair)
    symbol = pair.split(":")[0].replace("/", "")
    row["listing_calendar"] = {
        "onboard_utc": str(onboard),
        "actual_5m_warm_start": str(prior.base.WARM),
        "earlier_placeholder_candles_excluded": True,
        "frozen_market_source": PLAN["original_full_market_snapshot"],
    }
    save(OUT / "raw" / symbol / "admitted.json", row)
    print({"pair": pair, "onboard": str(onboard), "accepted": True}, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
