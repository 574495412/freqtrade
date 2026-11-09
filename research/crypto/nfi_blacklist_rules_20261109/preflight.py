"""Validate the actual isolated native schema and historical plugin resolver."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


HERE = Path(__file__).resolve().parent
first = json.loads((HERE / "structural-plan.json").read_text())
engine = Path(first["engine_source"])
sys.path.insert(0, str(engine))

import pandas as pd  # noqa: E402

import freqtrade  # noqa: E402
from freqtrade.configuration.config_validation import validate_config_schema  # noqa: E402
from freqtrade.enums import CandleType, RunMode  # noqa: E402
from freqtrade.resolvers import PairListResolver  # noqa: E402


if Path(freqtrade.__file__).resolve() != engine / "freqtrade/__init__.py":
    raise ValueError("Incorrect native package in preflight")
results = {}
for name in ["structural", "native_risk"]:
    path = HERE / (name + "-plan.json")
    if not path.exists():
        continue
    plan = json.loads(path.read_text())
    config = json.loads(Path(plan["candidates"][0]["config"]["path"]).read_text())
    config.update(runmode=RunMode.BACKTEST, candle_type_def=CandleType.FUTURES)
    validate_config_schema(config, preliminary=True)
    exchange = Mock()
    exchange.ohlcv_candle_limit.return_value = 1500
    clock = SimpleNamespace(backtest_time=None)
    plugin = PairListResolver.load_pairlist(
        "HistoricalVolumePairList",
        exchange=exchange,
        pairlistmanager=SimpleNamespace(_dataprovider=clock),
        config=config,
        pairlistconfig=config["pairlists"][0],
        pairlist_pos=0,
    )
    if plugin.gen_pairlist({}) != plan["pairs"]:
        raise ValueError("Native union differs")
    expected = json.loads(Path(plan["historical_schedule"]["path"]).read_text())["days"]
    for date in ["2026-01-01", "2026-06-01", "2026-11-09"]:
        clock.backtest_time = pd.Timestamp(date, tz="UTC").to_pydatetime()
        if plugin.gen_pairlist({}) != expected[date]["pairs"]:
            raise ValueError("Native day selection differs")
    results[name] = {
        "schema_passed": True,
        "actual_native_resolver": True,
        "checked_boundary_days": 3,
        "union": len(plan["pairs"]),
    }
(HERE / "preflight-result.json").write_text(
    json.dumps({"passed": True, "policies": results, "orders_enabled": False}, indent=2) + "\n"
)
print(results)
