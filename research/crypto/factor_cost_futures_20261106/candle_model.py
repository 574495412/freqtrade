"""Same financial arithmetic with a distinct futures minimum-routing profile."""
import importlib.util
from pathlib import Path

SOURCE=Path(__file__).resolve().parent.parent/'factor_cost_scenario_20261106/candle_model.py'
spec=importlib.util.spec_from_file_location('frozen_v1_candle_model',SOURCE)
frozen=importlib.util.module_from_spec(spec);spec.loader.exec_module(frozen)
CandleModel=frozen.CandleModel
PROFILE='RD1_CANDLE_COST_FUTURES_MINIMUM_REMAINDER_V1_NOT_EXECUTION_CERTIFIED'
