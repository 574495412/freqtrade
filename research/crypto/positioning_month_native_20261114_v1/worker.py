"""Unmodified native account runner plus rejection of suppressed callback errors."""
import argparse
import json
import logging
import traceback
from pathlib import Path
from research.crypto.tsmom_risk_native_20261114_v1 import native_runner as parent

class CallbackErrors(logging.Handler):
    def __init__(self):
        super().__init__();self.errors=[]
    def emit(self,record):
        if record.name=='freqtrade.strategy.strategy_wrapper' and record.levelno>=logging.WARNING:
            self.errors.append(record.getMessage())

def run(plan,name,cost,output):
    catcher=CallbackErrors();logging.getLogger().addHandler(catcher)
    try:
        result=parent.run(plan,name,cost,output)
        parent.save(output/'callback_audit.json',{'passed':not catcher.errors,'suppressed_callback_errors':catcher.errors})
        if catcher.errors:raise RuntimeError('Native callback exception was suppressed; account rejected')
        return result
    finally:logging.getLogger().removeHandler(catcher)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--candidate',required=True);parser.add_argument('--cost-bps',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:run(args.plan,args.candidate,args.cost_bps,args.output)
    except BaseException as exc:
        if args.output.exists():parent.save(args.output/'failure.json',{'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc(),'orders_enabled':False})
        raise
