"""Long-window registration, executing the unchanged short-window runner/model."""
import importlib.util
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE=HERE.parent/'factor_cost_scenario_20261106'
sys.path.insert(0,str(SOURCE))
spec=importlib.util.spec_from_file_location('frozen_candle_account_runner',SOURCE/'run_account.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.HERE=HERE
runner.ROOT=ROOT

if __name__=='__main__':runner.main()
