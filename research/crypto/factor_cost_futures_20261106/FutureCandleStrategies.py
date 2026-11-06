"""Futures lot/minimum routing; frozen factors and risk quantities are unchanged."""
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'factor_cost_scenario_20261106'))
from CandleFactorStrategies import F1CandleCost,F2CandleCost,AverageCandleControl
from freqtrade.exchange import amount_to_contract_precision
from freqtrade.util import FtPrecise


class TradableReduction:
    def adjust_trade_position(self,trade,current_time,current_rate,min_stake=None,**kwargs):
        try:
            model=self.cost_model
            if model.exit_all or trade.has_open_orders or not trade.amount:return None
            target=model.targets.get(trade.id,trade.amount)
            if target>=trade.amount-1e-12:return None
            if target<0 or not math.isfinite(target) or current_rate<=0:raise ValueError('Invalid reduction target/rate')
            precision=(trade.amount_precision,trade.precision_mode,trade.contract_size)
            remaining=amount_to_contract_precision(target,*precision)
            if remaining<=0:return -trade.stake_amount,'RD1_minimum_full_exit'
            stake=math.nextafter(-trade.stake_amount*(trade.amount-remaining)/trade.amount,-math.inf)
            # Reproduce the official conversion before returning a native request.
            actual=amount_to_contract_precision(abs(float(FtPrecise(stake)*FtPrecise(trade.amount)/FtPrecise(trade.stake_amount))),*precision)
            remaining=trade.amount-actual
            minimum=float(min_stake or 0.)
            if (actual<=0 or remaining<0 or remaining>target+1e-12
                    or (remaining and remaining*current_rate<minimum)
                    or actual*current_rate<minimum):
                return -trade.stake_amount,'RD1_minimum_full_exit'
            return stake,'RD1_candle_reduction'
        except Exception as exc:
            self.cost_model.fail(exc);return None


class F1FuturesCandleCost(TradableReduction,F1CandleCost):pass
class F2FuturesCandleCost(TradableReduction,F2CandleCost):pass
class AverageFuturesCandleControl(TradableReduction,AverageCandleControl):pass
