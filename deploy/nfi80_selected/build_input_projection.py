"""Materialize the small pinned input contract; never part of live refresh."""

from deploy.nfi80_selected import selector


def build():
    policy = selector.read(selector.HERE / "policy.json")
    seed = selector.read(selector.checked(policy["seed_schedule"]))["daily_inputs"]
    markets = selector.read(selector.checked(policy["market_identity_source"]))["markets"]
    return {
        "schema": "nfi-selected-input-projection.v1",
        "policy_sha256": selector.sha(selector.HERE / "policy.json"),
        "source_sha256": {
            name: policy[name]["sha256"]
            for name in ["rulebook", "seed_schedule", "market_identity_source"]
        },
        "daily_inputs": {p: seed[p] for p in policy["candidates"]},
        "markets": {
            p: {
                "id": markets[p]["id"],
                "info": {k: markets[p]["info"][k] for k in ["onboardDate", "baseAsset"]},
            }
            for p in policy["candidates"]
        },
    }


if __name__ == "__main__":
    selector.save(selector.HERE / "input-projection.json", build())
