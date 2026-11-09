"""Exercise the actual isolated native schema and plugin resolver before replay."""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


HERE = Path(__file__).resolve().parent
plan = json.loads((HERE / "original-plan.json").read_text())
engine = Path(plan["engine_source"])
sys.path.insert(0, str(engine))
import freqtrade  # noqa: E402
from freqtrade.configuration.config_validation import validate_config_schema  # noqa: E402
from freqtrade.enums import RunMode  # noqa: E402
from freqtrade.resolvers import PairListResolver  # noqa: E402


if Path(freqtrade.__file__).resolve() != engine / "freqtrade/__init__.py":
    raise ValueError("Preflight loaded the wrong native package")
config = json.loads(Path(plan["candidates"][0]["config"]["path"]).read_text())
config["runmode"] = RunMode.BACKTEST
validate_config_schema(config, preliminary=True)
clock = SimpleNamespace(backtest_time=None)
plugin = PairListResolver.load_pairlist(
    "HistoricalVolumePairList",
    exchange=Mock(),
    pairlistmanager=SimpleNamespace(_dataprovider=clock),
    config=config,
    pairlistconfig=config["pairlists"][0],
    pairlist_pos=0,
)
if plugin.gen_pairlist({}) != plan["pairs"]:
    raise ValueError("Native resolver did not load complete historical union")
schedule = json.loads(Path(plan["historical_schedule"]["path"]).read_text())
for date in [
    datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
    datetime(2026, 7, 1, tzinfo=UTC),
    datetime(2026, 11, 9, tzinfo=UTC),
]:
    clock.backtest_time = date
    if plugin.gen_pairlist({}) != schedule["days"][date.date().isoformat()]["pairs"]:
        raise ValueError("Native plugin returns wrong historical boundary")
manifest = json.loads(Path(plan["engine_manifest"]["path"]).read_text())
for name, digest in manifest["files"].items():
    if hashlib.sha256((engine / name).read_bytes()).hexdigest() != digest:
        raise ValueError("Native extension manifest differs")
receipt = {
    "passed": True,
    "actual_native_schema": True,
    "actual_native_resolver": True,
    "initial_union": len(plan["pairs"]),
    "checked_historical_boundaries": 3,
    "engine_source": str(engine),
    "plugin": type(plugin).__name__,
    "orders_enabled": False,
}
(HERE / "preflight-result.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(receipt)
