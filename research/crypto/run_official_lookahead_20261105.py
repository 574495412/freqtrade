"""Run a fixed, restricted official Freqtrade lookahead diagnostic."""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
CZ = Path("/Users/chrise/GitHub/CZ-Agent")
PLAN = ROOT / "research/crypto/strategy_comparison_wide_20261105/lookahead-diagnostic-plan.json"
OUT = ROOT / "user_data/crypto_quant/nfi_x8_lookahead_check_20261105_v001"
DATA = ROOT / "user_data/crypto_quant/nfi40_data_v002/candles"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_engine(expected_commit):
    engine = CZ / "vendor/freqtrade"
    manifest = json.loads((CZ / "vendor/FREQTRADE_SOURCE.json").read_text())
    if manifest["commit"] != expected_commit:
        raise ValueError("Wrong official engine commit")
    for name, digest in manifest["files"].items():
        if sha(engine / name) != digest:
            raise ValueError("Official engine changed: " + name)
    expected_python = {name for name in manifest["files"] if name.endswith(".py")}
    if {str(path.relative_to(engine)) for path in engine.rglob("*.py")} != expected_python:
        raise ValueError("Unregistered official engine Python source")
    return len(manifest["files"])


def main():
    plan = json.loads(PLAN.read_text())
    engine_files = verify_engine(plan["engine_commit"])
    strategy = CZ / "vendor/nfi-x8/NostalgiaForInfinityX8.py"
    if sha(strategy) != plan["source_sha256"]:
        raise ValueError("Pinned strategy changed")
    manifest_path = ROOT / "research/crypto/strategy_comparison_wide_20261105/data-receipts.json"
    all_receipts = json.loads(manifest_path.read_text())
    selected = {name: digest for name, digest in all_receipts.items()
                if any(name.startswith("futures/" + symbol + "_USDT_USDT-")
                       for symbol in ("BTC", "ETH", "ADA"))}
    if len(selected) != 21:
        raise ValueError("Incomplete fixed diagnostic input set")
    for name, digest in selected.items():
        if sha(DATA / name) != digest:
            raise ValueError("Diagnostic input changed: " + name)
    config = ROOT / "research/crypto/strategy_comparison_wide_20261105/nfi-x8-1x.json"
    csv = OUT / "lookahead-result.csv"
    cmd = [str(CZ / ".venv-freqtrade/bin/freqtrade"), "lookahead-analysis", "--config", str(config),
           "--strategy-path", str(strategy.parent), "--strategy", plan["strategy"], "--datadir", str(DATA),
           "--userdir", str(OUT), "--timerange", plan["timerange"], "--pairs", *plan["pairs"],
           "--minimum-trade-amount", str(plan["minimum_trade_amount"]),
           "--targeted-trade-amount", str(plan["targeted_trade_amount"]),
           "--lookahead-analysis-exportfilename", str(csv)]
    env = {k: v for k, v in os.environ.items() if not k.startswith("FREQTRADE__")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    log = OUT / "lookahead.log"
    with log.open("x") as stream:
        process = subprocess.Popen(cmd, cwd=CZ, stdout=stream, stderr=subprocess.STDOUT, env=env)
        status = {"pid": process.pid, "command": cmd, "state": "RUNNING", "credentials_passed": False,
                  "official_synthetic_diagnostic": True, "orders_enabled": False}
        (OUT / "process.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        peak, reason = 0, None
        while process.poll() is None:
            try:
                rss = psutil.Process(process.pid).memory_info().rss
            except psutil.NoSuchProcess:
                break
            peak = max(peak, rss)
            if rss > int(5.5 * 1024**3) or time.monotonic() - started > 900:
                reason = "rss_guard" if rss > int(5.5 * 1024**3) else "wall_guard"
                process.terminate()
                break
            time.sleep(2)
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait()
    verify_engine(plan["engine_commit"])
    if sha(strategy) != plan["source_sha256"] or any(sha(DATA / n) != h for n, h in selected.items()):
        raise ValueError("Pinned source or data changed during diagnostic")
    status.update(state="RESOURCE_ABORTED" if reason else ("COMPLETED" if code == 0 else "FAILED"),
                  exit_code=code, reason=reason, peak_rss_bytes=peak, elapsed_seconds=time.monotonic()-started,
                  plan_sha256=sha(PLAN), config_sha256=sha(config), log_sha256=sha(log),
                  official_engine_files_verified=engine_files,
                  result_csv_sha256=sha(csv) if csv.is_file() else None,
                  source_package_has_bytecode=(strategy.parent / "__pycache__").exists())
    (OUT / "process.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status), flush=True)


if __name__ == "__main__":
    main()
