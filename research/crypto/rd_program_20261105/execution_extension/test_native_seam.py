"""Actual native method calls with synthetic exchange, wallets and persistence.

No FreqtradeBot/Exchange constructor, DB initialization, strategy or RPC is run.
This is an engineering seam, not a bot deployment or economic simulation.
"""
from contextlib import ExitStack
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch
import json
import tempfile
import unittest

from test_reservations import T, snapshot, entry, pair
from account_replay import AccountReplay
from lifecycle import Lifecycle
from native_evidence import CapturedOrder
from rd1 import Funding, Order, RiskError, StateError
from freqtrade.enums import TradingMode
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade


AT=T.isoformat().replace('+00:00','Z')  # exact UTC formatting, no invented time


class NativeSeamTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.directory=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # Even accidental transport use fails before sending or resolving a host.
        for name in ('socket.socket.connect','socket.socket.connect_ex','socket.create_connection',
                     'socket.getaddrinfo'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('network forbidden')))
        self.session=Mock()
        self.stack.enter_context(patch.object(Trade,'session',self.session,create=True))
        self.commit=self.stack.enter_context(patch.object(Trade,'commit'))
        self.stack.enter_context(patch.object(Trade,'delete',side_effect=AssertionError('delete forbidden')))

    def tearDown(self):self.stack.close()

    def harness(self,market='spot',status='open',filled='2',short=False,lost_reply=False):
        life=Lifecycle(self.directory/market,'synthetic-native',market,create=True)
        self.stack.callback(lambda:life.close())
        initial=snapshot(market)
        proposal=entry(market,quantity=D(-5 if short else 5))
        self.assertTrue(life.reserve('r',initial,proposal,T)['reserved'])
        tape=[];reports=[];bindings=[]
        def create_order(**kwargs):
            expected=dict(pair=pair(market),ordertype='limit',side='sell' if short else 'buy',
                          amount=5.0,rate=10.0,reduceOnly=False,time_in_force='GTC',
                          leverage=1.0,initial_order=True)
            if kwargs!=expected:raise RiskError('native request differs from reserved proposal')
            ticket=life.claim_for_replay('r',initial,T)
            bindings.append(ticket)
            tape.append(dict(kwargs))
            raw={'id':'synthetic-order','clientOrderId':ticket['client_order_id'],
                 'symbol':pair(market),'side':kwargs['side'],'type':'limit','status':status,
                 'amount':5.0,'filled':float(filled),'remaining':float(D(5)-D(filled)),
                 'price':10.0,'average':10.0,'cost':float(D(filled)*10),
                 'timestamp':int(T.timestamp()*1000),'lastTradeTimestamp':int(T.timestamp()*1000)}
            reports.append(raw)
            if lost_reply:raise TimeoutError('synthetic lost submission reply')
            return dict(raw)
        exchange=SimpleNamespace(create_order=create_order,name='synthetic',id='synthetic',
            get_fee=lambda **kw:0.001,get_pair_base_currency=lambda p:'BTC',
            get_precision_amount=lambda p:2,get_precision_price=lambda p:2,
            precisionMode=2,precision_mode_price=2,get_contract_size=lambda p:1,
            check_order_canceled_empty=lambda o:o['status'] in ('canceled','expired','rejected') and o['filled']==0,
            get_min_pair_stake_amount=lambda *a:5)
        bot=SimpleNamespace(exchange=exchange,config={'stake_currency':'USDT','timeframe':'5m'},
            strategy=SimpleNamespace(order_time_in_force={'entry':'GTC'},order_types={'entry':'limit'},
                confirm_trade_entry=lambda **kw:True,get_strategy_name=lambda:'synthetic-seam',stoploss=-0.05),
            get_valid_enter_price_and_stake=Mock(return_value=(10.0,50.0,1.0)),
            trading_mode=TradingMode.SPOT if market=='spot' else TradingMode.FUTURES,
            wallets=SimpleNamespace(update=Mock()),_notify_enter=Mock(),_notify_enter_cancel=Mock(),
            handle_order_fee=Mock(),_update_trade_after_fill=lambda trade,*args:trade,
            order_close_notify=Mock())
        for name in ('execute_entry','update_trade_state','order_obj_or_raise','handle_cancel_enter'):
            setattr(bot,name,MethodType(getattr(FreqtradeBot,name),bot))
        return life,bot,tape,reports,bindings

    def reconcile(self,life,raw,market,filled,*,status=None,cursor=2):
        """Independent synthetic fill tape -> account ledger -> RD1 snapshot."""
        if status:raw=dict(raw,status=status)
        short=raw['side']=='sell';direction=-1 if short else 1
        ledger=AccountReplay(market,sequence_basis='source_ordered',evidence_class='synthetic')
        fills=[]
        if D(filled):
            event={'event_id':'synthetic-fill','sequence':1,'event_at':AT,
                   'received_at':AT,'kind':'fill','pair':pair(market),'campaign_id':'campaign',
                   'signed_quantity':str(direction*D(filled)),'price':'10','fee_quote':str(D(filled)*D('.01'))}
            ledger.apply(event)
            fills=[{'order_id':raw['id'],'trade_id':'synthetic-fill','pair':pair(market),
                    'side':raw['side'],'quantity':filled,'price':'10','fee_quote':event['fee_quote'],
                    'at':AT,'received_at':AT}]
        if market=='futures' and D(filled):
            ledger.apply({'event_id':'funding-accrual','sequence':2,'event_at':AT,
                'received_at':AT,'kind':'funding_accrual','funding_id':'synthetic-funding','cost_quote':'.01'})
        quoted={pair(market):{'price':'10','kind':'spot_bid' if market=='spot' else 'futures_mark',
                             'event_at':AT,'received_at':AT}}
        values=ledger.value(quoted,AT,'.001',funding_complete=True)
        remaining=direction*D(str(raw['remaining']))
        orders=(Order(raw['id'],pair(market),remaining,D(10),T,True),) if raw['status']=='open' else ()
        snap=snapshot(market,cursor=cursor,balance=values['wallet'],positions=values['positions'],orders=orders,
            funding=tuple(Funding(k,v,False) for k,v in ledger.pending_funding.items()))
        # Native object is produced by actual execute_entry, or actual parser after lost reply.
        from freqtrade.persistence import Order as NativeOrder
        native=NativeOrder.parse_from_ccxt_object(raw,raw['symbol'],raw['side'])
        normalized=CapturedOrder(json.dumps(raw),T,'synthetic-native-receipt','synthetic').normalize(native)
        self.assertEqual(life.risk.equity(snap),values['equity'])
        state=life.reconcile('r',f'evidence-{cursor}',normalized,snap,T,campaign_fills=fills)
        return state,snap,values

    def test_native_entry_terminal_paths_join_account_ledger(self):
        for market,status,filled,short in [('spot','expired','2',False),('futures','closed','5',True)]:
            with self.subTest(market=market,status=status):
                life,bot,tape,reports,_=self.harness(market,status,filled,short)
                self.assertTrue(bot.execute_entry(pair(market),50,is_short=short))
                trade=self.session.add.call_args.args[0]
                self.assertEqual(D(str(trade.amount)),D(filled))
                self.assertEqual(trade.orders[0].status,status)
                state,snap,values=self.reconcile(life,reports[0],market,filled)
                self.assertEqual(state['state'],'TERMINAL_RECONCILED')
                self.assertEqual(len(tape),1)
                self.assertFalse(life.reserve('another',snap,entry(market),T)['reserved'])
                self.assertLess(values['equity'],D(1000))  # Synthetic fees/reserve, no alpha claim.
                life.close()

    def test_native_zero_rejection_does_not_create_trade(self):
        life,bot,tape,reports,_=self.harness(status='rejected',filled='0')
        self.assertFalse(bot.execute_entry(pair('spot'),50))
        self.session.add.assert_not_called()
        state,snap,_=self.reconcile(life,reports[0],'spot','0')
        self.assertEqual(state['state'],'TERMINAL_RECONCILED')
        self.assertTrue(life.reserve('next',snap,entry(),T)['reserved'])

    def test_native_cancel_race_uses_later_cumulative_fill(self):
        life,bot,tape,reports,_=self.harness()
        bot.execute_entry(pair('spot'),50)
        trade=self.session.add.call_args.args[0]
        state,_,_=self.reconcile(life,reports[0],'spot','2')
        self.assertEqual(state['state'],'OBSERVED_OPEN')
        canceled=dict(reports[0],status='canceled',filled=4.0,remaining=1.0,cost=40.0)
        bot.exchange.cancel_order_with_result=Mock(return_value=canceled)
        self.assertFalse(bot.handle_cancel_enter(trade,reports[0],trade.orders[0],'synthetic-cancel'))
        self.assertEqual(D(str(trade.amount)),D(4))
        state,_,_=self.reconcile(life,canceled,'spot','4',cursor=3)
        self.assertEqual(state['filled'],'4.0')
        self.assertEqual(state['state'],'TERMINAL_RECONCILED')
        bot.exchange.cancel_order_with_result.assert_called_once()

    def test_unknown_cancel_keeps_pending_capacity(self):
        life,bot,tape,reports,_=self.harness()
        bot.execute_entry(pair('spot'),50)
        trade=self.session.add.call_args.args[0]
        bot.exchange.cancel_order_with_result=Mock(return_value=reports[0])
        self.assertFalse(bot.handle_cancel_enter(trade,reports[0],trade.orders[0],'synthetic-cancel'))
        self.assertEqual(life.s['requests']['r']['lifecycle']['state'],'SUBMISSION_UNKNOWN')
        self.assertFalse(life.reserve('again',snapshot(),entry(),T)['reserved'])

    def test_lost_reply_restart_no_second_submit_and_late_partial_receipt(self):
        life,bot,tape,reports,_=self.harness(status='expired',filled='2',lost_reply=True)
        with self.assertRaises(TimeoutError):bot.execute_entry(pair('spot'),50)
        life.close()
        reopened=Lifecycle(self.directory/'spot','synthetic-native','spot')
        self.stack.callback(reopened.close)
        with self.assertRaises(StateError):reopened.claim_for_replay('r',snapshot(),T)
        self.assertEqual(len(tape),1)
        self.assertFalse(reopened.reserve('again',snapshot(),entry(),T)['reserved'])
        state,_,_=self.reconcile(reopened,reports[0],'spot','2')
        self.assertEqual(state['state'],'TERMINAL_RECONCILED')

    def test_native_amount_mutation_rejected_before_claim(self):
        life,bot,tape,reports,_=self.harness()
        bot.get_valid_enter_price_and_stake.return_value=(10.0,60.0,1.0)
        with self.assertRaises(RiskError):bot.execute_entry(pair('spot'),50)
        self.assertEqual(tape,[])
        self.assertNotIn('lifecycle',life.s['requests']['r'])


if __name__=='__main__':unittest.main()
