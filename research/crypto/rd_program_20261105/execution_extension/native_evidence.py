"""Strict evidence boundary around native CCXT objects, without database access."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import math
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'p0/risk'))
from rd1 import RiskError, aware, stamp, dec


def decimal_text(value):
    if isinstance(value,bool) or value is None:raise RiskError('missing/boolean number')
    if isinstance(value,float):
        if not math.isfinite(value):raise RiskError('nonfinite native number')
        # Native float -> shortest decimal representation, not recovered wire precision.
        value=str(value)
    return str(dec(value))


@dataclass(frozen=True)
class CapturedOrder:
    payload_json: str
    received_at: datetime
    source_id: str
    evidence_class: str

    def normalize(self,native_order=None):
        at=aware(self.received_at)
        if not self.source_id or self.evidence_class not in ('synthetic','captured'):
            raise RiskError('explicit source identity/class required')
        raw=json.loads(self.payload_json)
        required=('id','clientOrderId','symbol','side','status','amount','filled','remaining','price')
        if not isinstance(raw,dict) or any(raw.get(k) is None for k in required):
            raise RiskError('incomplete original CCXT envelope')
        for key in ('id','clientOrderId','symbol'):
            if not isinstance(raw[key],str) or not raw[key]:raise RiskError('invalid order identity')
        if raw['side'] not in ('buy','sell'):raise RiskError('invalid side')
        report={k:raw[k] for k in required}
        for k in ('amount','filled','remaining','price'):report[k]=decimal_text(raw[k])
        if native_order is not None:
            # Native conversion intentionally omits clientOrderId and reception time.
            # Match retained fields to the captured envelope; never invent omitted fields.
            native=native_order.to_ccxt_object()
            for k in ('id','symbol','side','status'):
                if native.get(k)!=raw[k]:raise RiskError('native/envelope identity mismatch: '+k)
            for k in ('amount','filled','remaining','price'):
                if dec(decimal_text(native.get(k)))!=dec(report[k]):raise RiskError('native/envelope quantity mismatch: '+k)
        report.update(observed_at=stamp(at),source_id=self.source_id,evidence_class=self.evidence_class,
                      captured_payload_sha256=hashlib.sha256(self.payload_json.encode()).hexdigest(),
                      numeric_scope='native JSON numeric representation; does not certify exchange wire decimal precision')
        return report


def replay_campaign(fills,entry_order_id,pair,direction):
    """Reconstruct signed inventory and cash flow from all fills of one campaign.

    No funding/NAV claim. Caller must reconcile cumulative entry fills and current
    position to the report/snapshot; the function cannot certify source completeness.
    Fees must already be in quote currency (no assumed conversion or missing zero).
    """
    if direction not in (-1,1) or not entry_order_id:raise RiskError('invalid campaign identity')
    seen={};entry_total=Decimal(0);quantity=Decimal(0);fees=Decimal(0);cash=Decimal(0)
    last=None;duplicates=0
    for fill in fills:
        ident=(fill['order_id'],fill['trade_id'])
        if not all(isinstance(x,str) and x for x in ident):raise RiskError('missing fill identity')
        if ident in seen:
            if seen[ident]!=fill:raise RiskError('conflicting fill ID')
            duplicates+=1;continue
        if fill['pair']!=pair:raise RiskError('campaign pair mismatch')
        at=aware(datetime.fromisoformat(fill['at']));received=aware(datetime.fromisoformat(fill['received_at']))
        if received<at or (last is not None and at<last):raise RiskError('fill time order/receipt mismatch')
        last=at
        q,p,fee=(dec(fill[k]) for k in ('quantity','price','fee_quote'))
        if q<=0 or p<=0 or fee<0:raise RiskError('invalid fill or unsupported negative fee')
        is_entry=fill['order_id']==entry_order_id
        signed=direction if is_entry else -direction
        if fill['side']!=('buy' if signed>0 else 'sell'):raise RiskError('fill side does not match campaign')
        if is_entry:entry_total+=q;quantity+=q
        else:quantity-=q
        if quantity<0:raise RiskError('exit exceeds accumulated entry')
        fees+=fee;cash-=signed*q*p+fee
        seen[ident]=dict(fill)
    return {'entry_filled':str(entry_total),'signed_remaining_quantity':str(direction*quantity),
            'trade_cash_flow_quote':str(cash),'fees_quote':str(fees),
            'unique_fills':len(seen),'identical_duplicates_ignored':duplicates,
            'funding_included':False,'full_account_NAV_verified':False}
