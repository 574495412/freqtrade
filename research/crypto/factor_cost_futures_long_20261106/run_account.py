"""Long-window registration using byte-identical completed futures S adapters."""
import importlib.util
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE=HERE.parent/'factor_cost_futures_20261106'
sys.path.insert(0,str(SOURCE))
spec=importlib.util.spec_from_file_location('frozen_futures_worker',SOURCE/'run_account.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
worker.runner.HERE=HERE;worker.runner.ROOT=ROOT
if __name__=='__main__':worker.runner.main()
