"""Freeze the separate short-rebuy risk experiment against the original control."""

from pathlib import Path

from research.crypto.nfi80_online_baseline_20261125_v1.register import HERE as CAPACITY, register


HERE = Path(__file__).resolve().parent


if __name__ == "__main__":
    register(
        HERE, "FlatBoostShortRebuyCap", 12,
        "FlatTopBoost with pure short-rebuy lifetime entry requests capped at twice initial margin.",
        evidence=(CAPACITY / "baseline_short562_tail.json", CAPACITY / "source_parity.json"),
    )
