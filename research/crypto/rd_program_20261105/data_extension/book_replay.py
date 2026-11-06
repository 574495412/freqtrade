"""Exact timestamp, finite-depth Binance book reconstruction; no trading model."""
from datetime import datetime,timezone
from decimal import Decimal as D
import calendar
import re


def nanoseconds(text):
    match=re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z',text)
    if not match:raise ValueError('UTC timestamp precision/format unsupported')
    seconds=calendar.timegm(datetime.strptime(match[1],'%Y-%m-%dT%H:%M:%S').timetuple())
    return seconds*10**9+int((match[2] or '').ljust(9,'0'))


def levels(rows):
    out={}
    for price,quantity in rows:
        p,q=D(price),D(quantity)
        if not p.is_finite() or not q.is_finite() or p<=0 or q<0 or p in out:
            raise ValueError('invalid/duplicate price level')
        out[p]=q
    return out


class Book:
    def __init__(self,market):
        if market not in ('spot','futures'):raise ValueError('unknown market')
        self.market=market;self.reset();self.applied=0;self.obsolete=0

    def reset(self):
        self.buffer=[];self.bids={};self.asks={};self.last=None;self.ready=False
        self.bid_boundary=None;self.ask_boundary=None;self.failed=False

    def snapshot(self,data):
        bids=levels(data['bids']);asks=levels(data['asks'])
        if not bids or not asks or any(q<=0 for q in [*bids.values(),*asks.values()]):raise ValueError('empty/zero snapshot')
        pending=self.buffer;self.buffer=[]
        self.bids=bids;self.asks=asks
        self.bid_boundary=min(bids);self.ask_boundary=max(asks)
        self.last=data['lastUpdateId'];self.ready=False;self.failed=False
        for item in pending:self.update(item)

    def update(self,data):
        if self.failed:raise ValueError('book failed; fresh snapshot required')
        if self.last is None:
            self.buffer.append(data);return False
        start,end=data['U'],data['u']
        if type(start)!=int or type(end)!=int or start>end:raise ValueError('invalid sequence')
        if end<self.last or (end==self.last and (self.market=='spot' or self.ready)):
            self.obsolete+=1;return False
        if not self.ready:
            target=self.last+(self.market=='spot')
            good=start<=target<=end
        elif self.market=='futures':good=data.get('pu')==self.last
        else:good=start<=self.last+1<=end
        if not good:
            self.failed=True;self.ready=False
            raise ValueError(f'sequence gap: previous={self.last}, U={start}, u={end}, pu={data.get("pu")}')
        b,a=levels(data['b']),levels(data['a'])
        for book,changes in [(self.bids,b),(self.asks,a)]:
            for p,q in changes.items():
                if q==0:book.pop(p,None)
                else:book[p]=q
        self.last=end;self.ready=True;self.applied+=1
        return True

    def observation(self):
        if not self.ready or self.failed or not self.bids or not self.asks:return None
        bid,ask=max(self.bids),min(self.asks)
        if bid>=ask:raise ValueError('crossed book')
        bbo_known=bid>=self.bid_boundary and ask<=self.ask_boundary
        bid_floor=bid*D('.998');ask_ceiling=ask*D('1.002')
        # Fixed initial snapshot boundaries: sparse out-of-range updates cannot
        # certify all unknown intervening levels, even if the dictionary expands.
        full_bid=bbo_known and bid_floor>=self.bid_boundary
        full_ask=bbo_known and ask_ceiling<=self.ask_boundary
        return {'last_update_id':self.last,'bid':str(bid),'ask':str(ask),
                'bbo_within_known_snapshot_region':bbo_known,
                'spread_bps':str((ask-bid)/((ask+bid)/2)*10000),
                'visible_bid_quantity_within20bps':str(sum((q for p,q in self.bids.items() if bid_floor<=p<=bid),D(0))),
                'visible_ask_quantity_within20bps':str(sum((q for p,q in self.asks.items() if ask<=p<=ask_ceiling),D(0))),
                'bid_band_complete':full_bid,'ask_band_complete':full_ask,
                'depth_values_are_lower_bounds_when_band_incomplete':True,
                'band_anchor':'bid*(1-0.002) and ask*(1+0.002); diagnostic convention only'}
