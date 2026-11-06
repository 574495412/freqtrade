from test_reservations import T,snapshot,entry,pair
from native_evidence import CapturedOrder,replay_campaign
from rd1 import RiskError,stamp
from lifecycle import Lifecycle
from freqtrade.persistence import Order as NativeOrder
from pathlib import Path
import tempfile
import unittest
import json
from datetime import timedelta


def raw_order(**updates):
    d={'id':'order1','clientOrderId':'client1','symbol':'BTC/USDT','side':'buy','status':'canceled',
       'amount':5.0,'filled':2.0,'remaining':3.0,'price':10.0,'timestamp':int(T.timestamp()*1000),
       'lastTradeTimestamp':int(T.timestamp()*1000),'type':'limit'}
    d.update(updates);return d


def fill(order_id,trade_id,side,quantity,price='10',fee='0'):
    return {'order_id':order_id,'trade_id':trade_id,'pair':pair('spot'),'side':side,'quantity':quantity,
            'price':price,'fee_quote':fee,'at':stamp(T),'received_at':stamp(T)}


class EvidenceTests(unittest.TestCase):
    def test_real_native_object_roundtrip_retained_fields(self):
        raw=raw_order()
        native=NativeOrder.parse_from_ccxt_object(raw,raw['symbol'],'buy')
        report=CapturedOrder(json.dumps(raw),T,'synthetic-1','synthetic').normalize(native)
        self.assertEqual(report['filled'],'2.0')
        self.assertNotIn('clientOrderId',native.to_ccxt_object())
        self.assertNotIn('received_at',native.to_ccxt_object())
        self.assertEqual(report['clientOrderId'],'client1')

    def test_minified_native_export_is_insufficient(self):
        raw=raw_order();native=NativeOrder.parse_from_ccxt_object(raw,raw['symbol'],'buy')
        with self.assertRaises(RiskError):CapturedOrder(json.dumps(native.to_json('buy',minified=True)),T,'legacy','captured').normalize()

    def test_native_order_mismatch_rejected(self):
        raw=raw_order();native=NativeOrder.parse_from_ccxt_object(raw,raw['symbol'],'buy')
        native.filled=1
        with self.assertRaises(RiskError):CapturedOrder(json.dumps(raw),T,'x','synthetic').normalize(native)

    def test_receipt_time_cannot_be_inferred_from_order(self):
        with self.assertRaises((RiskError,ValueError)):CapturedOrder(json.dumps(raw_order()),None,'x','synthetic').normalize()

    def test_partial_entry_and_exits_whole_campaign(self):
        fills=[fill('entry','a','buy','2','10','.02'),fill('entry','b','buy','3','11','.033'),
               fill('exit','c','sell','1','12','.012'),fill('exit2','d','sell','4','13','.052')]
        r=replay_campaign(fills,'entry',pair('spot'),1)
        self.assertEqual(r['entry_filled'],'5');self.assertEqual(r['signed_remaining_quantity'],'0')
        self.assertEqual(r['trade_cash_flow_quote'],'10.883')

    def test_short_campaign_cash_flow_sign(self):
        r=replay_campaign([fill('entry','a','sell','2','10'),fill('exit','b','buy','2','8')],'entry',pair('spot'),-1)
        self.assertEqual(r['trade_cash_flow_quote'],'4')
        self.assertEqual(r['signed_remaining_quantity'],'-0')

    def test_duplicate_fill_idempotence_and_conflict(self):
        a=fill('entry','a','buy','2')
        r=replay_campaign([a,a],'entry',pair('spot'),1)
        self.assertEqual(r['entry_filled'],'2');self.assertEqual(r['identical_duplicates_ignored'],1)
        with self.assertRaises(RiskError):replay_campaign([a,dict(a,quantity='3')],'entry',pair('spot'),1)

    def test_exit_before_entry_and_wrong_side_fail(self):
        for fills in [[fill('exit','x','sell','1')],[fill('entry','x','sell','1')]]:
            with self.assertRaises(RiskError):replay_campaign(fills,'entry',pair('spot'),1)

    def test_lifecycle_accepts_full_fill_ledger_with_early_exit(self):
        with tempfile.TemporaryDirectory() as path:
            e=Lifecycle(Path(path),'test','spot',create=True)
            try:
                e.reserve('r',snapshot(),entry(),T);ticket=e.claim_for_replay('r',snapshot(),T)
                raw=raw_order(clientOrderId=ticket['client_order_id'])
                native=NativeOrder.parse_from_ccxt_object(raw,raw['symbol'],'buy')
                report=CapturedOrder(json.dumps(raw),T,'synthetic-replay','synthetic').normalize(native)
                fills=[fill('order1','a','buy','2'),fill('exit','b','sell','2')]
                old=[dict(f,at=stamp(T-timedelta(seconds=1))) for f in fills]
                with self.assertRaises(RiskError):e.reconcile('r','old',report,snapshot(cursor=2),T,campaign_fills=old)
                result=e.reconcile('r','event',report,snapshot(cursor=2),T,campaign_fills=fills)
                self.assertEqual(result['state'],'TERMINAL_RECONCILED')
                self.assertEqual(result['campaign_fill_reconstruction']['signed_remaining_quantity'],'0')
            finally:e.close()


if __name__=='__main__':unittest.main()
