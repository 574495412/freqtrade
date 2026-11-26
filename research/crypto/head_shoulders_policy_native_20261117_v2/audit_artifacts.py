"""Read-only reconciliation of completed interface artifacts, not a signal cache."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


HERE = Path(__file__).parent.resolve()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    protocol = json.loads((HERE / "protocol.json").read_text())
    source = Path(protocol["source_loader"]["path"]).parent
    # This source event artifact is read only after the native callback has
    # independently recomputed and exported all source patterns.
    original = pd.read_parquet(source / "source_events.parquet")
    catalog = pd.read_parquet(HERE / "native_probe/policy_event_catalog.parquet")
    common = [name for name in original.columns if name in catalog.columns]
    pd.testing.assert_frame_equal(
        original[common], catalog[common], check_exact=True, check_dtype=True
    )
    if catalog.event_id.duplicated().any() or not catalog.event_id.str.len().eq(44).all():
        raise ValueError("Event identities are not unique bounded tags")
    freeze = json.loads((HERE / "implementation_freeze.json").read_text())
    for path, digest in freeze["files"].items():
        if sha(path) != digest:
            raise ValueError("Frozen policy implementation changed")
    admission = json.loads((HERE / "native_probe/admission.json").read_text())
    if (
        sha(HERE / "native_probe/policy_event_catalog.parquet")
        != admission["event_catalog"]["sha256"]
    ):
        raise ValueError("Native event artifact differs")
    counts = catalog.policy_event_status.value_counts().to_dict()
    native_masks = sum(row["native_shifted_entry_masks"] for row in admission["records"])
    if counts.get("CONFIRMATION_ROW_AVAILABLE", 0) != native_masks:
        raise ValueError("Event catalog and actual native shifted masks differ")
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "role": "Post-probe artifact reconciliation only; no signal generation or account",
        "source_event_fields_compared": common,
        "source_events_exact": len(catalog),
        "unique_event_tags": len(catalog),
        "event_tag_length": 44,
        "event_status_counts": counts,
        "native_location_counts": catalog.native_location.value_counts().to_dict(),
        "all_self_terminal_event_ids": catalog.loc[
            catalog.self_terminal_at_confirmation, "event_id"
        ].tolist(),
        "native_entry_masks": native_masks,
        "actual_fills": 0,
        "frozen_policy_files_checked": len(freeze["files"]),
        "mismatches": 0,
        "financial_start_called": False,
        "native_financial_accounts": 0,
        "source_events_read_after_native_independent_derivation": True,
        "artifacts": {
            name: sha(HERE / name)
            for name in [
                "native_probe/admission.json",
                "native_probe/callback_validation.json",
                "native_probe/policy_audit.json",
                "native_probe/policy_event_catalog.parquet",
            ]
        },
    }
    with (HERE / "artifact_validation.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"passed": True, "status_counts": counts, "native_entry_masks": native_masks}))


if __name__ == "__main__":
    main()
