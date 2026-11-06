"""Disjoint 2026 accounts with unchanged A05 trading and sizing rules."""

import sys
from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.listing_reversal_20261107 import audit, replay
from research.crypto.listing_reversal_20261107.catalog import HERE, OUT, now


REGISTRATION = HERE / "reset_2026_registration.json"
STAGE = OUT / "reset_2026"


def register():
    if REGISTRATION.exists() or STAGE.exists():
        raise FileExistsError("2026 restart already registered")
    save(
        REGISTRATION,
        {
            "registered_utc": now(),
            "replay_sha256": sha(Path(replay.__file__)),
            "audit_sha256": sha(Path(audit.__file__)),
            "reset_code_sha256": sha(Path(__file__)),
            "parent_plan_sha256": sha(replay.PLAN),
            "parent_result_sha256": sha(HERE / "result.json"),
            "intake_sha256": sha(OUT / "admission.json"),
            "common_start": "2026-01-01",
            "split": "2026-05-01",
            "common_end": "2026-09-06",
            "policy": (
                "2026 launches only, independent cash reset because all four original "
                "accounts halted in 2025. Register before looking at any 2026 reset economics. "
                "Report the same four cases, risks, costs, inputs and original failed gates. "
                "Both time periods remain historical."
            ),
        },
    )


def run():
    protocol = read(REGISTRATION)
    bound = {
        "replay_sha256": Path(replay.__file__),
        "audit_sha256": Path(audit.__file__),
        "reset_code_sha256": Path(__file__),
        "parent_plan_sha256": replay.PLAN,
        "parent_result_sha256": HERE / "result.json",
        "intake_sha256": OUT / "admission.json",
    }
    for key, source in bound.items():
        if protocol[key] != sha(source):
            raise ValueError(f"Frozen reset input changed: {source}")
    parent = read(replay.PLAN)
    plan = {**parent, **{k: protocol[k] for k in ("common_start", "split", "common_end")}}
    intake = read(OUT / "admission.json")
    start = replay.bar_number(pd.Timestamp(plan["common_start"], tz="UTC"))
    market = {item.name: item for item in replay.market_data(plan, intake) if item.launch >= start}
    panel = audit.audit_sources()
    STAGE.mkdir()
    audit.OUT = STAGE
    result = {}
    verified = {}
    for case in plan["cases"]:
        for cost in plan["cost_bps"]:
            key = f"{case}_{cost}bps"
            path = STAGE / key
            path.mkdir()
            account, opportunities = replay.simulate(plan, market, case, cost)
            nav, result[key] = replay.summarize(plan, account, opportunities)
            nav.to_parquet(path / "nav.parquet", index=False)
            save(path / "events.json", account.events)
            save(path / "closed.json", account.finished)
            verified[key] = audit.audit_account(plan, panel, key, result[key])
            print(key, result[key], flush=True)
    save(
        HERE / "reset_2026_result.json",
        {
            "registration_sha256": sha(REGISTRATION),
            "admitted_2026": len(market),
            "accounts": result,
            "verification": verified,
            "original_gate_changed": False,
            "paper_eligible": False,
        },
    )


if __name__ == "__main__":
    {"register": register, "run": run}[sys.argv[1]]()
