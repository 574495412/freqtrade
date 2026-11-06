"""Check observed funding-to-mark timestamp joins without inventing absent events."""
import json
from pathlib import Path

import pandas as pd

from audit_native import HERE, ROOT, sha


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    checks = []
    for window in ("L", "S"):
        run = next(r for r in plan["runs"] if r["market"] == "futures" and r["window"] == window)
        start, end = [pd.Timestamp(d, tz="UTC") for d in plan["windows"][window]]
        hashes = json.loads((ROOT / run["data_receipt"]["path"]).read_text())
        for pair in plan["markets"]["futures"]["pairs"]:
            stem = pair.replace("/", "_").replace(":", "_")
            frames = {}
            for kind in ("funding_rate", "mark"):
                name = f"futures/{stem}-1h-{kind}.feather"
                path = ROOT / run["datadir"] / name
                assert sha(path) == hashes[name]
                frames[kind] = pd.read_feather(path)
            fund = frames["funding_rate"]
            fund = fund.loc[(fund.date >= start) & (fund.date < end)]
            missing = pd.DatetimeIndex(fund.date).difference(pd.DatetimeIndex(frames["mark"].date))
            checks.append({"window": window, "pair": pair, "funding_events": len(fund),
                           "funding_events_without_exact_mark": len(missing)})
    for name in ("futures_base_native_fix1", "futures_stress_native_fix1"):
        cfg = json.loads((HERE / "configs" / f"{name}.json").read_text())
        assert cfg.get("futures_funding_rate") is None
    out = {"scope": "Exact timestamp join of observed funding events and mark candles; missing funding events cannot be inferred without historical exchange schedules",
           "configured_fallback_rate": None, "checks": checks}
    target = HERE / "funding_join_audit.json"
    if target.exists():
        assert json.loads(target.read_text()) == out
    else:
        target.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"funding_events": sum(r["funding_events"] for r in checks),
                      "without_mark": sum(r["funding_events_without_exact_mark"] for r in checks)}))


if __name__ == "__main__":
    main()
