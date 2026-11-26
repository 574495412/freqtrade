"""Actual native Trade/Order dividend/cash boundaries, not historical returns."""
import sys
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import pytest
from bootstrap import boot
boot()
HERE=Path(__file__).resolve().parent
sys.path.append(str(HERE.parent/'net_nav_native_20261114_v1'))
sys.path.append(str(HERE.parent/'dated_fees_native_20261114_v1'))
from test_fees import at, config, contract, exchange, fees, fill, probe, zero_trade
from freqtrade.data.equity_dividends import CashDividend, DividendContract
from freqtrade.data.history.equity import EquityAdmissionError
from freqtrade.equity_dividend_wallets import EquityDividendWallets
from freqtrade.optimize.equity_nav import EquityMark, EquityNavSeries, capture_equity_nav
from freqtrade.persistence import LocalTrade, Trade

@pytest.fixture(autouse=True)
def isolated():
 old=Trade.use_db;Trade.use_db=False;LocalTrade.reset_trades()
 with patch.object(Trade,'commit'):yield
 LocalTrade.reset_trades();Trade.use_db=old

def event(c=None, **changes):
 c=c or contract();e=CashDividend('ordinary-1','ABC/'+c.currency,c.sessions[1].opens,
  at(1),D(10),D('.1'),at(1),'d'*64,'e'*64,at(6,12),at(2),'f'*64)
 return replace(e,**changes)

def dividend_contract(c=None, events=None, **changes):
 c=c or contract();events=(event(c),)if events is None else events
 return replace(DividendContract(c,('ABC/'+c.currency,),c.sessions[0].opens,
  c.settlement_days[-1].available_at,events,'b'*64),**changes)

def account(c=None, events=None):
 c=c or contract();a=probe(c)
 a.wallets=EquityDividendWallets(config(c.currency),exchange(c.currency),fees=fees(c,'ABC/'+c.currency),
  dividends=dividend_contract(c,events),asof=c.sessions[0].opens)
 return a

def snap(w,price=90):
 pair='ABC/'+w.contract.currency
 quotes=[EquityMark(pair,w.contract.currency,w.asof,price,'close','a'*64)]if w.get_owned(pair,'ABC')else[]
 return capture_equity_nav(w,quotes,at=w.asof,mark_kind='close')

def trace(market):
 c=contract(market);a=account(c);w=a.wallets;pair='ABC/'+c.currency
 t=zero_trade(pair,c.sessions[0].opens)
 fill(a,t,c.sessions[0].opens,9,100)
 pre=snap(w,100);assert pre.net_nav==999
 w.advance(c.sessions[1].opens)
 ex=snap(w);assert ex.net_nav==990
 assert w.get_free(c.currency)==99 and w.dividend_receivable==D(81)
 assert w.dividend_journal[0].quantity==9
 fill(a,t,c.sessions[1].opens,9,90,False)
 sold=snap(w);assert sold.dividend_receivable==D(81)
 assert sold.total_cash==pytest.approx(906.38) and sold.net_nav==pytest.approx(987.38)
 assert sold.closed_trade_net_cash_profit==D('-93.62')
 w.advance(at(6,12)-timedelta(microseconds=1));before=snap(w)
 w.advance(at(6,12));paid=snap(w)
 assert paid.net_nav==before.net_nav and paid.dividend_receivable==0 and paid.paid_dividends==81
 # XNYS sale still unsettled until Jan 6 22 UTC; XHKG until Jan 7 10 UTC.
 assert paid.free_cash==180 and paid.unsettled_receivable==pytest.approx(807.38)
 when=at(6,15 if market=='XNYS'else 2)
 if market=='XHKG':when=at(7,2) # after payment but before T+2 cash release
 second=zero_trade(pair,when)
 fill(a,second,when,1,150)
 reinvest=snap(w,150);assert reinvest.free_cash==29
 assert reinvest.paid_dividends==81 and reinvest.dividend_receivable==0
 assert reinvest.net_nav==pytest.approx(986.38)
 for _ in range(3):assert snap(w,150)==reinvest
 orders=[{'trade_id':r.id,'pair':r.pair,'order_id':o.order_id,'at':o.order_filled_utc.isoformat(),
  'side':o.ft_order_side,'quantity':str(o.safe_filled),'price':str(o.safe_price)}
  for r in Trade.get_trades_proxy()for o in r.orders if o.safe_filled]
 return {'market':market,'native_fills':3,'orders':orders,
  'snapshots':[asdict(x)for x in[pre,ex,sold,before,paid,reinvest]],
  'passed':True,'historical_strategy_accounts':0}

@pytest.mark.parametrize('market',['XNYS','XHKG'])
def test_real_native_credit_entitlement_and_reinvestment(market):
 trace(market)

@pytest.mark.parametrize('at_cutoff',[True,False])
def test_ex_open_buy_does_not_receive_dividend(at_cutoff):
 a=account();w=a.wallets;e=w.dividends.events[0]
 when=e.ex_open if at_cutoff else e.ex_open+timedelta(minutes=1)
 t=zero_trade(when=when);fill(a,t,when,2,100)
 assert not w.dividend_journal and w.dividend_receivable==0
 w.advance(at(6,12));assert w.paid_dividends==0

def test_pre_ex_dca_and_partial_sale_freeze_actual_quantity():
 a=account();w=a.wallets;t=zero_trade(when=at(2))
 fill(a,t,at(2),2,100);fill(a,t,at(2,16),3,100);fill(a,t,at(2,17),1,100,False)
 w.advance(w.dividends.events[0].ex_open)
 assert w.dividend_receivable==36 and w.dividend_journal[0].trade_quantities==((t.id,D(4)),)
 fill(a,t,at(3,15),2,100)
 assert t.amount==6 and w.dividend_receivable==36

def test_unknown_payment_keeps_claim_without_invented_cash():
 e=event(available_at=None,payment_known_at=None,payment_source_sha256=None)
 a=account(events=(e,));t=zero_trade();fill(a,t,at(2),2,100)
 a.wallets.advance(at(10,22));s=snap(a.wallets,100)
 assert s.free_cash==799 and s.dividend_receivable==18 and s.paid_dividends==0
 assert s.net_nav==1017

def test_late_payment_notice_is_not_visible_in_earlier_snapshot():
 e=event(payment_known_at=at(6,11))
 a=account(events=(e,));t=zero_trade();fill(a,t,at(2),2,100)
 a.wallets.advance(at(3));assert a.wallets.dividend_journal[0].available_at is None
 a.wallets.advance(at(6,11));assert a.wallets.dividend_journal[0].available_at==at(6,12)
 assert a.wallets.paid_dividends==0

def test_future_terms_perturbation_and_truncation_leave_prior_cash_exact():
 observations=[]
 for amount in (D(1),D(999),None):
  LocalTrade.reset_trades()
  later=event(identity='later',ex_open=contract().sessions[4].opens,gross_per_share=amount or D(1),
   available_at=None,payment_known_at=None,payment_source_sha256=None)
  a=account(events=(event(),later)if amount is not None else(event(),));t=zero_trade()
  fill(a,t,at(2),2,100);a.wallets.advance(at(3))
  observations.append(asdict(snap(a.wallets,100)))
 assert observations[0]==observations[1]==observations[2]

@pytest.mark.parametrize('fault',['missing','price','quantity','clock','side'])
def test_observed_history_change_rejects_without_credit_or_nav_mutation(fault):
 a=account();w=a.wallets;t=zero_trade();o=fill(a,t,at(2),2,100)
 w.advance(at(3));old=(w._wallets,w.dividend_journal,w.paid_dividends,w.dividend_receivable)
 if fault=='missing':LocalTrade.reset_trades()
 elif fault=='price':o.average=101
 elif fault=='quantity':o.filled=1
 elif fault=='clock':o.order_filled_date=at(2,16)
 else:o.ft_order_side='sell'
 with pytest.raises(EquityAdmissionError):w.advance(at(6,12))
 assert w.asof==at(3) and old==(w._wallets,w.dividend_journal,w.paid_dividends,w.dividend_receivable)

@pytest.mark.parametrize('fault',['amount','rate','nan','float','source','tax','announce','pay_clock','bundle','kind','round'])
def test_invalid_event_rejects(fault):
 changes={'amount':{'gross_per_share':D(0)},'rate':{'withholding_rate':D(2)},
  'nan':{'gross_per_share':D('NaN')},'float':{'gross_per_share':1.0},'source':{'source_sha256':''},
  'tax':{'tax_known_at':at(3,14)},'announce':{'announced_at':at(3,14)},
  'pay_clock':{'payment_known_at':at(7)},'bundle':{'available_at':None},
  'kind':{'kind':'stock'},'round':{'rounding':'DOWN'}}
 with pytest.raises(EquityAdmissionError):event(**changes[fault])

@pytest.mark.parametrize('fault',['currency','duplicate','duplicate_ex','clock','basis','empty'])
def test_incomplete_contract_rejects(fault):
 changes={'currency':{'pairs':('ABC/HKD',)},'duplicate':{'events':(event(),event())},
  'duplicate_ex':{'events':(event(),event(identity='alias'))},
  'clock':{'events':(event(ex_open=at(3)),)},'basis':{'price_basis':'adjusted'},'empty':{'pairs':()}}
 with pytest.raises(EquityAdmissionError):dividend_contract(**changes[fault])

def test_coverage_and_contract_switch_rollback():
 a=account();w=a.wallets;old=w.asof
 with pytest.raises(EquityAdmissionError):w.advance(at(11))
 assert w.asof==old
 w.dividends=replace(w.dividends)
 with pytest.raises(EquityAdmissionError):w.update()

def test_cent_rounding_is_explicit_not_a_claim_about_statutory_tax():
 e=event(gross_per_share=D('.015'),withholding_rate=D('.5'))
 assert e.amounts(D(1))==(D('.02'),D('.01'),D('.01'))

def test_fee_failure_reverts_newly_paid_dividend_state():
 a=account();w=a.wallets;t=zero_trade();fill(a,t,at(2),2,100)
 w.advance(at(3));old=(w._wallets,w.dividend_journal,w.paid_dividends,w.dividend_receivable)
 t.fee_close=.01
 with pytest.raises(EquityAdmissionError):w.advance(at(6,12))
 assert old==(w._wallets,w.dividend_journal,w.paid_dividends,w.dividend_receivable)

def test_empty_dividend_overlay_matches_original_native_wallet():
 rows=[]
 for dividend in (False,True):
  LocalTrade.reset_trades();a=account(events=())if dividend else probe();t=zero_trade()
  for when,q,p,entry in[(at(2),2,100,True),(at(3),1,110,False)]:fill(a,t,when,q,p,entry)
  rows.append(asdict(snap(a.wallets,110)))
 assert rows[0]==rows[1]

def test_native_complete_declared_nav_grid_transfers_claim_to_cash_only_once():
 a=account();w=a.wallets
 s=EquityNavSeries(w,expected_clocks=[w.asof,at(3,14),at(6,12)],mark_kind='close')
 s.capture([]);t=zero_trade(when=at(2));fill(a,t,at(2),2,100)
 w.advance(at(3,14));s.capture([EquityMark(t.pair,'USD',w.asof,100,'close','a'*64)])
 w.advance(at(6,12));s.capture([EquityMark(t.pair,'USD',w.asof,100,'close','a'*64)])
 r=s.component_result()
 assert s.snapshots[-1].net_nav==s.snapshots[-2].net_nav==1017
 assert r['paid_dividends']=='18.00' and r['terminal_dividend_receivable']=='0'
 assert r['historical_strategy_accounts']==0 and r['terminal']=='observed holdings mark'

def test_declared_grid_terminal_unpaid_receivable_is_in_nav_not_spendable_cash():
 a=account();w=a.wallets
 s=EquityNavSeries(w,expected_clocks=[w.asof,at(3,14)],mark_kind='close')
 s.capture([]);t=zero_trade();fill(a,t,at(2),2,100)
 w.advance(at(3,14));s.capture([EquityMark(t.pair,'USD',w.asof,100,'close','a'*64)])
 r=s.component_result()
 assert r['terminal_dividend_receivable']=='18.00' and r['paid_dividends']=='0'
 assert s.snapshots[-1].free_cash==799 and r['final_net_nav']==1017

def test_dividend_credit_still_requires_fee_inclusive_native_cash():
 from test_fees import pending_order
 a=account();w=a.wallets;t=zero_trade();fill(a,t,at(2),9,100)
 w.advance(at(6,15));assert w.get_free('USD')==180
 o=pending_order(t,1);o.ft_price=o.price=o.average=180;o.order_date=at(6,15)
 with pytest.raises(EquityAdmissionError,match='including dated fees'):
  a._try_close_open_order(o,t,at(6,15),[at(6,15),180,181,179,180])
 assert o.ft_is_open and not o.filled and w.paid_dividends==81

@pytest.mark.parametrize('field',['events','pairs'])
def test_mutable_contract_collections_reject(field):
 d=dividend_contract()
 with pytest.raises(EquityAdmissionError):replace(d,**{field:list(getattr(d,field))})

def test_paid_event_after_coverage_end_stays_receivable_at_terminal():
 e=event(available_at=at(13),payment_known_at=at(2))
 a=account(events=(e,));t=zero_trade();fill(a,t,at(2),2,100)
 a.wallets.advance(at(10,22));assert a.wallets.dividend_receivable==18 and a.wallets.paid_dividends==0
