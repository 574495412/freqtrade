"""Capture exact selected indicator values from the official recursive analysis."""
import hashlib
import json
import math
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
CZ = Path("/Users/chrise/GitHub/CZ-Agent")
OUT = ROOT / "user_data/crypto_quant/nfi_x8_recursive_check_20261105_v002"
PLAN = ROOT / "research/crypto/strategy_comparison_wide_20261105/recursive-diagnostic-plan.json"
STRATEGY = CZ / "vendor/nfi-x8/NostalgiaForInfinityX8.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def conditions(value):
    if value is None:
        return {"gt_zero": None, "lt_zero": None, "lt_50": None}
    return {"gt_zero": value > 0, "lt_zero": value < 0, "lt_50": value < 50}


def main():
    if any(k.startswith("FREQTRADE__") for k in os.environ):
        raise ValueError("Diagnostic refuses Freqtrade environment overrides")
    plan = json.loads(PLAN.read_text())
    if sha(STRATEGY) != plan["source_sha256"]:
        raise ValueError("Pinned strategy changed")
    import freqtrade
    if not Path(freqtrade.__file__).resolve().is_relative_to(CZ / "vendor/freqtrade"):
        raise ValueError("Wrong Freqtrade engine")
    from freqtrade.optimize.analysis.recursive import RecursiveAnalysis
    from freqtrade.main import main as official_main
    original = RecursiveAnalysis.analyze_indicators

    def capture(analysis):
        pair = analysis.pair_to_used
        full = analysis.full_varHolder.indicators[pair].iloc[-1]
        selected = ("OBV_change_pct", "OBV_change_pct_15m")
        partials = {str(item.startup_candle): item.indicators[pair].iloc[-1]
                    for item in analysis.partial_varHolder_array}
        result = {"pair": pair, "at": str(full["date"]), "strategy_source_sha256": sha(STRATEGY),
                  "diagnostic_plan_sha256": sha(PLAN), "values": {},
                  "note": "Observer records official recursive-analysis indicator outputs only. No entry/exit signal or fill comparison was performed.",
                  "orders_enabled": False}
        for name in selected:
            baseline = numeric(full[name])
            rows = {startup: {"value": numeric(row[name]), "conditions": conditions(numeric(row[name]))}
                    for startup, row in partials.items()}
            result["values"][name] = {"full_history_value": baseline,
                                       "full_history_conditions": conditions(baseline), "partial": rows}
        with (OUT / "exact-values.json").open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
        original(analysis)

    RecursiveAnalysis.analyze_indicators = capture
    args = ["recursive-analysis", "--config", str(ROOT / "research/crypto/strategy_comparison_wide_20261105/nfi-x8-1x.json"),
            "--strategy-path", str(STRATEGY.parent), "--strategy", "NostalgiaForInfinityX8",
            "--datadir", str(ROOT / "user_data/crypto_quant/nfi40_post_source_data_20261105_v002/candles"),
            "--userdir", str(OUT), "--timerange", "20261001-20261101", "--pairs", plan["pair"],
            "--startup-candle", *[str(x) for x in plan["startup_candles"]]]
    official_main(args)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        OUT = Path(sys.argv[1]).resolve()
    sys.exit(main())
