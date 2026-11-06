"""Cost-scenario-only callbacks; original fixed factor/EMA methods inherited."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'native_factors_20261106/sources'))
from FixedFactorSignals import F1Momentum168Native, F2Volatility24Native, AverageRD1ControlNative
from freqtrade.enums import RunMode


class CostCallbacks:
    position_adjustment_enable=True
    max_entry_position_adjustment=0

    def bot_start(self,**kwargs):
        if self.config.get('runmode')!=RunMode.BACKTEST or not hasattr(self,'cost_model'):
            raise RuntimeError('Registered candle cost-model attachment required')

    @property
    def protections(self):return self.cost_model.plan['risk_contract']['native_protections']

    def custom_stake_amount(self,pair,current_time,current_rate,proposed_stake,min_stake,max_stake,**kwargs):
        try:
            stake=min(max_stake,self.cost_model.budget(pair,current_time,current_rate,self.factor_family))
            return stake if min_stake is None or stake>=min_stake else 0.
        except Exception as exc:
            self.cost_model.fail(exc);return 0.

    def confirm_trade_entry(self,pair,amount,rate,current_time,side,**kwargs):
        try:return side=='long' and amount*rate<=self.cost_model.budget(pair,current_time,rate,self.factor_family)+1e-8
        except Exception as exc:
            self.cost_model.fail(exc);return False

    def custom_exit(self,pair,trade,current_time,current_rate,current_profit,**kwargs):
        if self.cost_model.exit_all:return 'RD1_candle_risk_exit'
        return None

    def check_entry_timeout(self,pair,trade,order,current_time,**kwargs):
        return self.cost_model.blocked or (current_time-order.order_date_utc).total_seconds()>=60

    def adjust_trade_position(self,trade,current_time,**kwargs):
        try:
            if self.cost_model.exit_all or trade.has_open_orders or not trade.amount:return None
            target=self.cost_model.targets.get(trade.id,trade.amount)
            if target>=trade.amount-1e-12:return None
            return -trade.stake_amount*(trade.amount-target)/trade.amount,'RD1_candle_reduction'
        except Exception as exc:
            self.cost_model.fail(exc);return None


class F1CandleCost(CostCallbacks,F1Momentum168Native):factor_family='F1'
class F2CandleCost(CostCallbacks,F2Volatility24Native):factor_family='F2'
class AverageCandleControl(CostCallbacks,AverageRD1ControlNative):factor_family='CONTROL'
