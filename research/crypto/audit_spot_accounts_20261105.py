"""Check spot-only contract, then apply independent original fill/NAV audit."""
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("independent_wide_audit", ROOT / "research/crypto/audit_wide_account_20261105.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

def main(root, selected=None, selected_cost=None):
    plan = json.loads((root / "registered_plan.json").read_text())
    for candidate in plan["candidates"]:
        for cost in plan["total_cost_bps_per_side"]:
            if selected is not None and (candidate["name"] != selected or cost != selected_cost):
                continue
            folder = root / (candidate["name"] + f"-cost{cost}")
            config = json.loads((folder / "effective_config.json").read_text())
            if config["trading_mode"] != "spot" or config.get("margin_mode") not in (None, ""):
                raise ValueError("Non-spot account")
            archive, = (folder / "native_results").glob("backtest-result-*.zip")
            with zipfile.ZipFile(archive) as z:
                filename, = [n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json")]
                trades = json.loads(z.read(filename))["strategy"][candidate["strategy"]]["trades"]
            if any(t["is_short"] or t["funding_fees"] not in (0, 0.0) for t in trades):
                raise ValueError("Unexpected spot short or funding")
            audit.main(candidate["name"], root, cost)

if __name__ == "__main__":
    if len(sys.argv) not in (2, 4):
        raise SystemExit("usage: audit_spot_accounts_20261105.py ROOT [CANDIDATE COST_BPS]")
    main(Path(sys.argv[1]).resolve(), sys.argv[2] if len(sys.argv) == 4 else None,
         int(sys.argv[3]) if len(sys.argv) == 4 else None)
