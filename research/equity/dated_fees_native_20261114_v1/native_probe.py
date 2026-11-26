"""Actual native synthetic fills plus independent Decimal cash-flow audit."""
import hashlib
import json
import sys
from decimal import Decimal as D, ROUND_UP, ROUND_HALF_UP
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from test_fees import probe, zero_trade, contract, at, fill
from freqtrade.persistence import Trade,LocalTrade

def snapshot(a,t,when):
    w=a.wallets;c=w.contract.currency
    return {'at':when.isoformat(),'currency':c,'free':w.get_free(c),'locked':w.get_used(c),'cash_total_not_NAV':w.get_total(c),'held_quantity':w.get_owned(t.pair,t.pair.split('/')[0]),'native_trade_record_amount':t.amount,'native_remaining_cost_basis':t.stake_amount,'total_execution_fees':str(w.total_execution_fees),'unsettled_net_sale_proceeds':w.cash_restriction.unsettled_sale_proceeds}

def main():
    old=Trade.use_db;Trade.use_db=False
    scenarios=[]
    try:
        with patch.object(Trade,'commit'):
            for market in ('XNYS','XHKG'):
                LocalTrade.reset_trades();c=contract(market);hour=15 if market=='XNYS' else 2
                a=probe(c);t=zero_trade('ABC/'+c.currency,at(2,hour));snaps=[];orders=[]
                sequence=[(at(2,hour),2,100,True),(at(3,hour),3,110,True),(at(6,hour),2,120,False),(at(7,hour),3,90,False)]
                expected_cash=D(1000);fees=D(0);hold=D(0);receivables=[]
                for when,qty,price,entry in sequence:
                    o=fill(a,t,when,qty,price,entry);gross=D(qty)*D(price)
                    commission=max(D(1),gross*D('.001')).quantize(D('.01'),rounding=ROUND_HALF_UP)
                    tax=D(0)if entry else(gross*D('.002')).quantize(D('.01'),rounding=ROUND_UP)
                    fee=commission+tax;fees+=fee;hold+=D(qty)if entry else-D(qty)
                    expected_cash+=(-gross-fee)if entry else(gross-fee)
                    if not entry:receivables.append((c.release_for_fill(when).available_at,gross-fee))
                    locked=sum((v for clock,v in receivables if when<clock),D(0));expected_free=expected_cash-locked
                    s=snapshot(a,t,when)
                    assert abs(D(str(s['cash_total_not_NAV']))-expected_cash)<D('.00000001')
                    assert abs(D(str(s['free']))-expected_free)<D('.00000001')
                    assert abs(D(str(s['locked']))-locked)<D('.00000001')
                    assert D(str(s['held_quantity']))==hold and a.wallets.total_execution_fees==fees
                    snaps.append(s);orders.append({'id':o.order_id,'pair':t.pair,'at':o.order_filled_utc.isoformat(),'side':o.ft_order_side,'quantity':o.safe_filled,'price':o.safe_price,'independent_fee':str(fee),'native_fee_open':t.fee_open,'native_fee_close':t.fee_close})
                final=c.settlement_days[-1].available_at;a.wallets.advance(final);snaps.append(snapshot(a,t,final));assert abs(D(str(a.wallets.get_free(c.currency)))-expected_cash)<D('.00000001')
                scenarios.append({'market':market,'orders':orders,'snapshots':snaps,'independent_audit_passed':True,'native_trade_PnL_is_gross':True})
    finally:LocalTrade.reset_trades();Trade.use_db=old
    result={'stage':'synthetic native component; no Configuration/IStrategy/Backtesting.start history','native_fills':sum(len(s['orders'])for s in scenarios),'snapshots':sum(len(s['snapshots'])for s in scenarios),'actual_historical_strategy_accounts':0,'return_metrics':None,'scenarios':scenarios}
    (HERE/'native_synthetic_trace.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k!='scenarios'},indent=2))
if __name__=='__main__':main()
