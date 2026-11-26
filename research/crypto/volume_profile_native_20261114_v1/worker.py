"""Isolated native financial worker with explicit funded-state extension class."""
import argparse,logging
from pathlib import Path
from .bootstrap import boot

def main():
    boot()
    from freqtrade.optimize import backtesting as native
    from freqtrade.optimize.volume_profile import VolumeProfileBacktesting
    from research.crypto.tsmom_risk_native_20261114_v1.native_runner import run
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--candidate',required=True);parser.add_argument('--cost-bps',type=int,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    errors=[]
    class Errors(logging.Handler):
        def emit(self,record):
            if record.levelno>=logging.ERROR:errors.append(record.getMessage())
    handler=Errors();logging.getLogger().addHandler(handler)
    original=native.Backtesting;native.Backtesting=VolumeProfileBacktesting
    try:
        result=run(args.plan,args.candidate,args.cost_bps,args.output)
        if errors:raise RuntimeError('Native callback/error log present: '+repr(errors))
        return result
    finally:native.Backtesting=original;logging.getLogger().removeHandler(handler)
if __name__=='__main__':main()
