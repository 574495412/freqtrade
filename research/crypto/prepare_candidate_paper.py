"""Package a screened registered candidate as an isolated, guarded dry-run instance."""

import argparse
import platform
import shutil
from importlib.metadata import version
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity


LAUNCHER = '''"""Start only the exact reviewed paper configuration."""
import hashlib
import json
import os
import sys
import platform
from importlib.metadata import version
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / "package.json").read_text())
config_path = root / "settings.json"
config = json.loads(config_path.read_text())
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
if not config.get("dry_run") is True:
    raise SystemExit("Paper launcher requires dry_run=true")
if any(key.startswith("FREQTRADE__") for key in os.environ):
    raise SystemExit("Paper launcher refuses external configuration overrides")
if any(config["exchange"].get(key) for key in ("key", "secret", "password", "uid")):
    raise SystemExit("Paper package must contain no trading credentials")
if digest(config_path) != manifest["settings_sha256"]:
    raise SystemExit("Paper configuration changed after packaging")
for name, expected in manifest["strategy_files"].items():
    if digest(root / "strategies" / name) != expected:
        raise SystemExit("Reviewed strategy source changed")
import freqtrade
native_root = Path(freqtrade.__file__).resolve().parent
current = {str(p.relative_to(native_root)): digest(p) for p in sorted(native_root.rglob("*.py"))}
if current != manifest["native_source_identity"]:
    raise SystemExit("Native runtime source differs from the screened build")
runtime = manifest.get("runtime")
if runtime:
    if platform.python_version() != runtime["python"]:
        raise SystemExit("Paper interpreter differs from the reviewed build")
    if any(version(package) != expected for package, expected in runtime["packages"].items()):
        raise SystemExit("Paper dependency versions differ from the reviewed build")
command = [sys.executable, "-m", "freqtrade", "trade", "--config", str(config_path)]
os.execve(sys.executable, command, dict(os.environ))
'''


def runtime_identity(reference):
    packages = ("numpy", "pandas", "TA-Lib", "ccxt", "SQLAlchemy", "ft-pandas-ta", "technical")
    locked = {}
    for line in (reference / "requirements.lock.txt").read_text().splitlines():
        if "==" in line:
            name, value = line.split("==", 1)
            locked[name.lower().replace("_", "-")] = value
    versions = {name: version(name) for name in packages}
    if any(locked.get(name.lower().replace("_", "-")) != value for name, value in versions.items()):
        raise ValueError("Current dependency versions differ from native account requirements")
    return {"python": platform.python_version(), "packages": versions}


def prepare(run: Path, output: Path):
    evaluation = read(run / "evaluation.json")
    bias = read(run / "bias_result.json")
    if not evaluation["selected_historical_screen_passed"] or not bias["native_lookahead_passed"]:
        raise ValueError("Candidate has not passed the economic and leakage screens")
    prefix = read(run / "prefix_result.json")
    if not prefix["passed"]:
        raise ValueError("Candidate has not passed its native prefix checks")
    selection = read(run / "selection.json")
    chosen = selection["agent"]
    if not chosen or chosen == "cash":
        raise ValueError("No selected candidate to package")
    plan = read(run / "plan.json")
    dataset = Path(plan["dataset"])
    if sha(dataset / "receipts.json") != plan["data_receipts_sha256"]:
        raise ValueError("Screened dataset identity changed")
    reference = run / "train" / chosen / "cost_3bps"
    frozen = read(reference / "freeze.json")
    native = source_identity()
    if frozen["source_identity"] != native:
        raise ValueError("Screened native source changed")
    for record in plan["strategy_sources"]:
        if sha(reference / "strategies" / Path(record["path"]).name) != record["sha256"]:
            raise ValueError("Screened strategy source changed")
    adapted = dict(
        plan,
        candidates=[chosen],
        trading_mode="futures",
        margin_mode="isolated",
        exchange="binance",
        stake_usdt=plan["stake_amount"],
    )
    expected_settings = configuration(
        adapted, dataset, reference, plan["train_start"], plan["train_end"], 3
    )
    expected_settings.pop("strategy_list")
    expected_settings.update(
        strategy_path=str(reference / "strategies"), **plan.get("settings", {})
    )
    if read(reference / "settings.json") != expected_settings:
        raise ValueError("Screened configuration changed")
    output.mkdir(parents=True, exist_ok=False)
    strategy_dir = output / "strategies"
    shutil.copytree(reference / "strategies", strategy_dir)
    settings = read(reference / "settings.json")
    for key in (
        "strategy_list",
        "timerange",
        "backtest_cache",
        "export",
        "exportdirectory",
        "datadir",
    ):
        settings.pop(key, None)
    settings.update(
        dry_run=True,
        user_data_dir=str(output),
        strategy_path=str(strategy_dir),
        strategy=chosen,
        db_url="sqlite:///" + str(output / "paper.sqlite"),
        initial_state="running",
        bot_name="paper_" + chosen.lower(),
        force_entry_enable=False,
        cancel_open_orders_on_exit=False,
    )
    settings["exchange"]["key"] = ""
    settings["exchange"]["secret"] = ""
    save(output / "settings.json", settings)
    (output / "run_paper.py").write_text(LAUNCHER)
    manifest = {
        "schema": "crypto-quant-registered-paper.v1",
        "candidate": chosen,
        "purpose": "candidate_paper_validation",
        "dry_run": True,
        "real_orders_enabled": False,
        "production_activation_authorized": False,
        "historical_screen_passed": True,
        "independent_oos": False,
        "evaluation_sha256": sha(run / "evaluation.json"),
        "bias_sha256": sha(run / "bias_result.json"),
        "prefix_sha256": sha(run / "prefix_result.json"),
        "settings_sha256": sha(output / "settings.json"),
        "strategy_files": {p.name: sha(p) for p in strategy_dir.glob("*.py")},
        "native_source_identity": native,
        "runtime": runtime_identity(reference),
        "launcher_sha256": sha(output / "run_paper.py"),
        "command": [
            str(Path(__file__).resolve().parents[2] / ".venv/bin/python"),
            str(output / "run_paper.py"),
        ],
    }
    save(output / "package.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.run.resolve(), args.output.resolve())
    print({"candidate": result["candidate"], "command": result["command"], "dry_run": True})
