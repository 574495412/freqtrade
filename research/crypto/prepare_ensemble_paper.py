"""Package the fixed-rule portfolio as two isolated paper controls, preserving agent dissent."""

import argparse
import shutil
from pathlib import Path

from freqtrade.research.data import read, save, sha
from freqtrade.research.engine import configuration
from freqtrade.research.workflow import source_identity
from research.crypto.prepare_candidate_paper import LAUNCHER, runtime_identity


MASTER = '''"""Run both independently funded paper accounts; stop both if either process fails."""
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / "package.json").read_text())
digest_file = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
if digest_file(Path(__file__)) != manifest["master_sha256"]:
    raise SystemExit("Reviewed portfolio launcher changed")
if manifest["mode"] != "paper_fixed_rule_control" or manifest["real_orders_enabled"]:
    raise SystemExit("This launcher is only for the fixed-rule paper control")
if any(key.startswith("FREQTRADE__") for key in os.environ):
    raise SystemExit("Portfolio paper launcher refuses environment overrides")
folders, capital, databases = [], 0.0, set()
for component in manifest["components"]:
    folder = (root / component["directory"]).resolve()
    if not folder.is_relative_to(root):
        raise SystemExit("Paper component escaped its package")
    if digest_file(folder / "package.json") != component["package_sha256"]:
        raise SystemExit("Reviewed component manifest changed")
    if digest_file(folder / "run_paper.py") != component["launcher_sha256"]:
        raise SystemExit("Reviewed component launcher changed")
    config = json.loads((folder / "settings.json").read_text())
    digest = hashlib.sha256((folder / "settings.json").read_bytes()).hexdigest()
    if config.get("dry_run") is not True or digest != component["settings_sha256"]:
        raise SystemExit("Reviewed paper component configuration changed")
    if any(config["exchange"].get(k) for k in ("key", "secret", "password", "uid")):
        raise SystemExit("Paper accounts must have no trading credentials")
    if config["db_url"] in databases:
        raise SystemExit("Paper components cannot share a trade database")
    databases.add(config["db_url"])
    capital += config["dry_run_wallet"]
    folders.append(folder)
if capital != manifest["initial_equity_usdt"]:
    raise SystemExit("Paper capital differs from the reviewed portfolio")
owner_lock = (root / ".portfolio.lock").open("a+")
try:
    fcntl.flock(owner_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("This paper portfolio is already running") from None
owner_lock.seek(0)
owner_lock.truncate()
owner_lock.write(str(os.getpid()) + "\\n")
owner_lock.flush()
sessions = root / "sessions"
sessions.mkdir(exist_ok=True)
stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
receipt = {"started_utc": datetime.now(UTC).isoformat(), "children": [],
           "master_pid": os.getpid(), "dry_run": True, "real_orders_enabled": False}
processes, logs = [], []
def request_stop(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, request_stop)
try:
    for folder in folders:
        log = (folder / "runtime.log").open("a")
        logs.append(log)
        command = [sys.executable, str(folder / "run_paper.py")]
        process = subprocess.Popen(command, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                   cwd=root, start_new_session=True,
                                   pass_fds=(owner_lock.fileno(),))
        processes.append(process)
        receipt["children"].append({"directory": folder.name, "pid": process.pid,
                                    "command": command})
    (sessions / (stamp + ".started.json")).write_text(json.dumps(receipt, indent=2))
    while all(process.poll() is None for process in processes):
        time.sleep(1)
    receipt["stop_reason"] = "component_exit"
except KeyboardInterrupt:
    receipt["stop_reason"] = "operator_interrupt"
finally:
    for process in processes:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
    for process in processes:
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    for log in logs:
        log.close()
    for child, process in zip(receipt["children"], processes, strict=True):
        child["returncode"] = process.returncode
    receipt["ended_utc"] = datetime.now(UTC).isoformat()
    (sessions / (stamp + ".ended.json")).write_text(json.dumps(receipt, indent=2))
sys.exit(0 if receipt.get("stop_reason") == "operator_interrupt" else 1)
'''


STOPPER = '''"""Stop only processes whose current command belongs to this paper package."""
import json
import os
import shlex
import signal
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
def command(pid):
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True, check=False)
    return shlex.split(result.stdout.strip()) if result.returncode == 0 else []
lock = root / ".portfolio.lock"
pid = int(lock.read_text().strip()) if lock.exists() and lock.read_text().strip() else None
args = command(pid) if pid else []
if len(args) > 1 and args[1] == str(root / "run_portfolio_paper.py"):
    os.kill(pid, signal.SIGINT)
    print({"stop_requested_for_master": pid})
else:
    sessions = sorted((root / "sessions").glob("*.started.json"))
    stopped = []
    if sessions:
        for child in json.loads(sessions[-1].read_text())["children"]:
            folder = root / child["directory"]
            args = command(child["pid"])
            native = ["-m", "freqtrade", "trade", "--config", str(folder / "settings.json")]
            if args[1:] == native or args[1:] == [str(folder / "run_paper.py")]:
                os.kill(child["pid"], signal.SIGINT)
                stopped.append(child["pid"])
    print({"stop_requested_for_verified_children": stopped})
'''


def reference_settings(component):
    run = Path(component["run"])
    plan = read(run / "plan.json")
    if sha(run / "plan.json") != component["plan_sha256"]:
        raise ValueError("A component protocol changed")
    if (
        not read(run / "prefix_result.json")["passed"]
        or not read(run / "bias_result.json")["native_lookahead_passed"]
    ):
        raise ValueError("A component has not passed its scoped leakage diagnostics")
    reference = run / "train" / component["name"] / "cost_3bps"
    if read(reference / "freeze.json")["source_identity"] != source_identity():
        raise ValueError("Native source changed")
    adapted = dict(
        plan,
        candidates=[component["name"]],
        trading_mode="futures",
        margin_mode="isolated",
        exchange="binance",
        stake_usdt=plan["stake_amount"],
    )
    expected = configuration(
        adapted, Path(plan["dataset"]), reference, plan["train_start"], plan["train_end"], 3
    )
    expected.pop("strategy_list")
    expected.update(strategy_path=str(reference / "strategies"), **plan.get("settings", {}))
    if read(reference / "settings.json") != expected:
        raise ValueError("A component configuration changed after evaluation")
    for source in plan["strategy_sources"]:
        if sha(reference / "strategies" / Path(source["path"]).name) != source["sha256"]:
            raise ValueError("A component strategy changed after evaluation")
    return reference, expected


def prepare(run, output):
    plan = read(run / "plan.json")
    evaluation = read(run / "evaluation.json")
    audit = read(run / "portfolio_audit.json")
    selection = read(run / "selection.json")
    if not evaluation["all_historical_economic_screens_passed"] or not audit["passed"]:
        raise ValueError("Portfolio economic and accounting checks have not passed")
    if selection["control"] != plan["candidate"]:
        raise ValueError("This is not the frozen fixed-rule control")
    for row in audit["accounts"]:
        file = run / row["phase"] / plan["candidate"] / f"cost_{row['cost']}bps/result.json"
        if sha(file) != row["portfolio_result_sha256"]:
            raise ValueError("An audited portfolio result changed")
    checked = [(component, *reference_settings(component)) for component in plan["components"]]
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "mode": "paper_fixed_rule_control",
        "candidate": plan["candidate"],
        "purpose": "prospective paper control and runtime validation",
        "initial_equity_usdt": plan["initial_equity_usdt"],
        "maximum_open_positions": plan["maximum_open_positions"],
        "agent_recommendation": selection["agent"],
        "agent_endorsed": selection["agent"] == plan["candidate"],
        "historical_economic_screens_passed": True,
        "all_component_future_outcomes_already_observed": True,
        "independent_oos": False,
        "production_eligible": False,
        "real_orders_enabled": False,
        "production_activation_authorized": False,
        "evaluation_sha256": sha(run / "evaluation.json"),
        "audit_sha256": sha(run / "portfolio_audit.json"),
        "selection_sha256": sha(run / "selection.json"),
        "components": [],
    }
    for index, (component, reference, settings) in enumerate(checked):
        directory = "nfi" if index == 0 else "trend"
        folder = output / directory
        folder.mkdir()
        shutil.copytree(
            reference / "strategies",
            folder / "strategies",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copyfile(reference / "requirements.lock.txt", folder / "requirements.lock.txt")
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
            user_data_dir=str(folder),
            strategy=component["name"],
            strategy_path=str(folder / "strategies"),
            db_url="sqlite:///" + str(folder / "paper.sqlite"),
            initial_state="running",
            bot_name="fixed_portfolio_" + directory,
            force_entry_enable=False,
            cancel_open_orders_on_exit=False,
        )
        settings["exchange"].update(key="", secret="")
        save(folder / "settings.json", settings)
        (folder / "run_paper.py").write_text(LAUNCHER)
        child = {
            "candidate": component["name"],
            "paper_role": "fixed_rule_portfolio_component",
            "dry_run": True,
            "real_orders_enabled": False,
            "production_eligible": False,
            "agent_recommendation": selection["agent"],
            "individual_historical_screen_passed": read(Path(component["run"]) / "evaluation.json")[
                "selected_historical_screen_passed"
            ],
            "portfolio_historical_screen_passed": True,
            "settings_sha256": sha(folder / "settings.json"),
            "native_source_identity": source_identity(),
            "runtime": runtime_identity(reference),
            "strategy_files": {p.name: sha(p) for p in (folder / "strategies").glob("*.py")},
            "launcher_sha256": sha(folder / "run_paper.py"),
        }
        save(folder / "package.json", child)
        manifest["components"].append(
            {
                "directory": directory,
                "candidate": component["name"],
                "settings_sha256": child["settings_sha256"],
                "package_sha256": sha(folder / "package.json"),
                "launcher_sha256": child["launcher_sha256"],
            }
        )
    (output / "run_portfolio_paper.py").write_text(MASTER)
    (output / "stop_portfolio_paper.py").write_text(STOPPER)
    manifest["master_sha256"] = sha(output / "run_portfolio_paper.py")
    manifest["command"] = [
        str(Path(__file__).resolve().parents[2] / ".venv/bin/python"),
        str(output / "run_portfolio_paper.py"),
    ]
    manifest["stop_command"] = [manifest["command"][0], str(output / "stop_portfolio_paper.py")]
    save(output / "package.json", manifest)
    print(
        {
            "paper_package": str(output),
            "agent_recommendation": selection["agent"],
            "real_orders_enabled": False,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare(args.run.resolve(), args.output.resolve())
