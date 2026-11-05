"""Validate or run one frozen, credential-free official spot lookahead test.

No worker starts in --validate-only mode. Running this historical diagnostic
must be coordinated after other Freqtrade financial/backtest workers finish.
"""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]
CZ = Path("/Users/chrise/GitHub/CZ-Agent")
PLAN = ROOT / "research/crypto/spot_baseline_20261105_v003/lookahead-diagnostic-plan-spot-x8.json"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def checked(record):
    path = Path(record["path"])
    if path.is_symlink() or not path.is_file() or sha(path) != record["sha256"]:
        raise ValueError(f"Frozen diagnostic input changed: {path}")
    return path

def validate():
    plan = json.loads(PLAN.read_text())
    if sha(Path(__file__).resolve()) != plan["runner_sha256"]:
        raise ValueError("Diagnostic runner differs from registration")
    if plan["mode"] != "spot" or plan["pairs"] != ["ADA/USDT", "ETH/USDT"] or plan["timerange"] != "20250101-20250401":
        raise ValueError("Unexpected registered spot contract")
    manifest = json.loads(checked(plan["engine_manifest"]).read_text())
    if manifest["commit"] != plan["engine_commit"]:
        raise ValueError("Official engine commit differs")
    engine = Path(plan["engine_source"])
    for name, digest in manifest["files"].items():
        if sha(engine / name) != digest:
            raise ValueError(f"Official engine changed: {name}")
    expected_python = {name for name in manifest["files"] if name.endswith(".py")}
    if {str(p.relative_to(engine)) for p in engine.rglob("*.py")} != expected_python:
        raise ValueError("Unexpected official engine Python source")
    strategy = checked(plan["strategy_source"])
    if (strategy.parent / "__pycache__").exists():
        raise ValueError("Unregistered bytecode in pinned strategy directory")
    config_path = checked(plan["config"])
    config = json.loads(config_path.read_text())
    if (config.get("trading_mode") != "spot" or config.get("margin_mode") not in (None, "") or
        config.get("dry_run") is not True or config.get("exchange", {}).get("name") != "binance" or
        any(config.get("exchange", {}).get(k) for k in ("key", "secret", "password")) or
        config.get("api_server", {}).get("enabled") or config.get("telegram", {}).get("enabled")):
        raise ValueError("Config is not an uncredentialed Binance spot dry-run")
    receipts_path = checked(plan["data_receipts"])
    all_receipts = json.loads(receipts_path.read_text())
    expected_names = {f"{coin}_USDT-{tf}.feather" for coin in ("BTC", "ADA", "ETH") for tf in ("5m", "15m", "1h", "4h", "1d")}
    if set(plan["selected_data"]) != expected_names:
        raise ValueError("Incomplete or extra diagnostic data")
    data = Path(plan["datadir"])
    for name in sorted(expected_names):
        if all_receipts.get(name) != plan["selected_data"][name] or sha(data / name) != plan["selected_data"][name]:
            raise ValueError(f"Spot history changed: {name}")
    for path in (plan["historical_spot_plan"], plan["observed_futures_diagnostic"]):
        checked(path)
    return plan, strategy, config_path, len(manifest["files"])

def main(validate_only):
    plan, strategy, config, engine_count = validate()
    if validate_only:
        print(json.dumps({"state":"VALIDATED_NOT_STARTED", "plan_sha256":sha(PLAN),
                          "selected_spot_files":len(plan["selected_data"]),
                          "official_engine_files_verified":engine_count,
                          "orders_enabled":False}, indent=2))
        return
    out = Path(plan["output_directory"])
    out.mkdir(parents=True, exist_ok=False)
    csv = out / "lookahead-result.csv"
    cmd = [str(CZ / ".venv-freqtrade/bin/freqtrade"), "lookahead-analysis", "--config", str(config),
           "--strategy-path", str(strategy.parent), "--strategy", plan["strategy"], "--datadir", plan["datadir"],
           "--userdir", str(out), "--timerange", plan["timerange"], "--pairs", *plan["pairs"],
           "--minimum-trade-amount", str(plan["minimum_trade_amount"]),
           "--targeted-trade-amount", str(plan["targeted_trade_amount"]),
           "--lookahead-analysis-exportfilename", str(csv)]
    # An explicit environment allowlist prevents API credentials and Freqtrade
    # environment overrides from reaching the diagnostic subprocess.
    env = {"PATH":os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE":"1",
           "PYTHONNOUSERSITE":"1", "LANG":"en_US.UTF-8", "TZ":"UTC"}
    status = {"command":cmd, "state":"STARTING", "plan_sha256":sha(PLAN), "credentials_passed":False,
              "environment_keys":sorted(env), "orders_enabled":False, "synthetic_diagnostic_only":True}
    (out / "process.json").write_text(json.dumps(status,indent=2)+"\n")
    started = time.monotonic()
    peak, reason = 0, None
    log = out / "lookahead.log"
    with log.open("x") as stream:
        process = subprocess.Popen(cmd, cwd=CZ, stdout=stream, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        status.update(pid=process.pid,state="RUNNING")
        (out / "process.json").write_text(json.dumps(status,indent=2)+"\n")
        while process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                rss = sum(p.memory_info().rss for p in [parent, *parent.children(recursive=True)] if p.is_running())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                rss = 0
            peak = max(peak, rss)
            if rss > plan["memory_guard_bytes"] or time.monotonic()-started > plan["wall_guard_seconds"]:
                reason = "rss_guard" if rss > plan["memory_guard_bytes"] else "wall_guard"
                os.killpg(process.pid, signal.SIGTERM)
                break
            time.sleep(2)
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait()
    validate()
    status.update(state="RESOURCE_ABORTED" if reason else ("COMPLETED" if code == 0 else "FAILED"),
                  exit_code=code, reason=reason, peak_rss_bytes=peak, elapsed_seconds=time.monotonic()-started,
                  official_engine_files_verified=engine_count, log_sha256=sha(log),
                  result_csv_sha256=sha(csv) if csv.is_file() else None,
                  strategy_source_bytecode_exists=(strategy.parent/"__pycache__").exists())
    (out / "process.json").write_text(json.dumps(status,indent=2)+"\n")
    print(json.dumps(status,indent=2))
    if code != 0 or reason:
        raise SystemExit(code or 1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only",action="store_true")
    args = parser.parse_args()
    main(args.validate_only)
