"""Frozen-risk candle cost model, attached to official Backtesting callbacks.

This is NOT executable RD1: no bid/ask or depth is invented, and 5m observation
cannot certify 5s monitoring or 60s cancellation. Shared financial constraints
are applied identically to F1, F2 and the unscaled EMA control.
"""
from datetime import timedelta
import itertools
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
from freqtrade.persistence import LocalTrade

ROOT=Path(__file__).resolve().parents[3]
NATIVE=ROOT/'research/crypto/native_factors_20261106/sources'
sys.path.insert(0,str(NATIVE))
sys.path.insert(0,str(ROOT/'research/crypto/rd_program_20261105/p0/risk'))
from rd1 import RD1, Correlations, Position, Order, Snapshot, Quote, D, load_contract
from factor_inputs import effective_daily_scale

PROFILE='RD1_CANDLE_COST_MODEL_V1_NOT_EXECUTION_CERTIFIED'


def cash_value(trade,price,funding=None):
    """Original fills, actual modeled fees and remaining inventory, including partial exits."""
    orders=[o for o in trade.orders if not o.ft_is_open and o.filled]
    buys=[o for o in orders if o.ft_order_side==trade.entry_side]
    sells=[o for o in orders if o.ft_order_side!=trade.entry_side]
    buy=math.fsum(o.filled*o.safe_price for o in buys)
    sell=math.fsum(o.filled*o.safe_price for o in sells)
    qty=math.fsum(o.filled for o in buys)-math.fsum(o.filled for o in sells)
    if abs((qty-trade.amount)*price)>1e-6:raise ValueError('Native inventory differs from modeled fills')
    credit=(trade.funding_fees or 0.) if funding is None else funding
    return sell+qty*price-buy-buy*trade.fee_open-sell*trade.fee_close-qty*price*trade.fee_close+credit


class CandleModel:
    def __init__(self,account,market,scenario,start,end,hourly,prices,*,marks=None):
        self.account=account;self.market=market;self.scenario=scenario
        self.plan,self.contract_hash=load_contract();self.r=self.plan['risk_contract']
        self.pairs=tuple(self.plan['markets'][market]['pairs']);self.universe=set(self.pairs)
        self.cost=float(next(x['total_bps_per_side'] for x in self.plan['markets'][market]['cost_scenarios'] if x['id']==scenario))/10000
        self.start=pd.Timestamp(start);self.end=pd.Timestamp(end)
        if self.start!=self.start.normalize():raise ValueError('Model inception must be UTC midnight')
        if self.start.tz is None or self.start.utcoffset()!=timedelta(0):raise ValueError('Explicit UTC model dates required')
        if set(hourly)!=self.universe or set(prices)!=self.universe:raise ValueError('Complete frozen universe required')
        self.hourly={p:f.set_index('date',drop=False).sort_index() for p,f in hourly.items()}
        self.prices=prices;self.marks=marks
        if market=='futures' and (marks is None or set(marks)!=self.universe):raise ValueError('Explicit 5m mark coverage required')
        self.peak=1000.;self.day=None;self.day_equity=None;self.hard=False;self.warn=False;self.day_stop=None
        self.rows=[];self.decisions=[];self.daily={};self.now=None;self.error=None;self.corr_day=None;self.corr=None
        self.targets={};self.exit_all=False;self.blocked=False

    def funding(self,trade,now):
        if self.market!='futures' or not trade.amount:return 0.
        # Same native funding model; no guessed settlement or reception time.
        allocated=math.fsum(o.funding_fee or 0. for o in trade.orders)
        running=self.account.exchange.calculate_funding_fees(self.account.futures_data[trade.pair],
            amount=trade.amount,is_short=False,open_date=trade.date_last_filled_utc,close_date=now)
        return allocated+running

    def price(self,pair,now):
        values=self.prices if self.market=='spot' else self.marks
        value=float(values[pair].loc[pd.Timestamp(now)])
        if not math.isfinite(value) or value<=0:raise ValueError('Missing/nonpositive synchronous model price')
        return value

    def correlations(self,now):
        day=pd.Timestamp(now).normalize()
        if self.corr_day==day:return self.corr
        self.corr_day=day;self.corr=None
        # Exactly 720 closed hourly returns, frozen daily refresh; missing -> one cluster.
        need=pd.date_range(end=day-pd.Timedelta(hours=1),periods=721,freq='h')
        frames=[]
        for pair in self.pairs:
            f=self.hourly[pair].reindex(need)
            if f.close.isna().any() or (f.close<=0).any():return None
            frames.append(np.diff(np.log(f.close.to_numpy())))
        matrix=np.corrcoef(np.array(frames))
        if not np.isfinite(matrix).all():return None
        edges=tuple((a,b,D(str(float(matrix[i,j])))) for i,a in enumerate(self.pairs) for j,b in enumerate(self.pairs) if j>i)
        self.corr=Correlations(day.to_pydatetime(),720,edges)
        return self.corr

    def state(self,now):
        trades=list(LocalTrade.bt_trades_open)
        quotes={p:self.price(p,now) for p in self.pairs}
        equity=1000.+LocalTrade.bt_total_profit+math.fsum(cash_value(t,quotes[t.pair],self.funding(t,now)) for t in trades)
        positions=tuple(Position(str(t.id),t.pair,D(str(t.amount)),D(str(t.open_rate)),D(str(t.amount*quotes[t.pair]*self.cost)))
                        for t in trades if t.amount>0)
        orders=tuple(Order(str(o.id),t.pair,D(str(o.remaining)),D(str(o.price)),o.order_date_utc,
                           o.ft_order_side==t.entry_side,o.ft_order_side!=t.entry_side)
                     for t in trades for o in t.orders if o.ft_is_open and o.remaining)
        snap=SimpleNamespace(positions=positions,orders=orders,quotes=tuple(Quote(p,D(str(v)),now,
            'spot_bid' if self.market=='spot' else 'futures_mark') for p,v in quotes.items()),at=now,correlations=self.correlations(now))
        # Quote.kind describes the risk arithmetic role only; source remains
        # candle OPEN (spot) or mark OPEN (futures), never certified bid/depth.
        exp=RD1.exposure(self,snap,D(str(equity))) if equity>0 else None
        return equity,trades,quotes,snap,exp

    def clusters(self,snap):return RD1.clusters(self,snap)

    def update_limits(self,now,equity,trades,exp):
        day=pd.Timestamp(now).normalize()
        if self.day!=day:
            if pd.Timestamp(now)!=day:raise ValueError('Missing exact midnight account anchor')
            self.day=day;self.day_equity=equity
        self.peak=max(self.peak,equity)
        dd=1-equity/self.peak
        self.hard=self.hard or equity<=float(self.r['absolute_equity_floor_usdt']) or equity<=self.peak*(1-float(self.r['hwm_halt_fraction']))
        self.warn=self.warn or equity<=self.peak*(1-float(self.r['hwm_warning_fraction']))
        if equity<=self.day_equity*(1-float(self.r['daily_loss_fraction'])):self.day_stop=day
        if self.day_stop is not None and day>self.day_stop and not trades and not self.hard:self.day_stop=None
        self.exit_all=self.hard or self.day_stop is not None
        self.blocked=self.exit_all or self.warn or exp is None or bool(exp['breaches'])

    def allocations(self,now,equity,trades,quotes,snap,exp):
        groups,_=self.clusters(snap)
        held={t.pair:t for t in trades if t.amount>0}
        ordered=[p for p in self.pairs if p in held]
        caps={p:min(held[p].amount*quotes[p],equity*float(self.r['pair_exposure_cap'])) for p in ordered}
        for p in ordered[int(self.r['max_open_positions']):]:caps[p]=0.
        for group in set(groups.values()):
            names=[p for p in ordered if groups[p]==group];total=math.fsum(caps[p] for p in names)
            ratio=min(1.,equity*float(self.r['correlation_cluster_cap'])/total) if total>0 else 1.
            for p in names:caps[p]*=ratio
        gross=min(float(self.r['gross_exposure_cap']),float(self.r['net_absolute_exposure_cap']),
            float(self.r['hwm_warning_gross_target']) if self.warn else 1.)
        total=math.fsum(caps.values());ratio=min(1.,equity*gross/total) if total>0 else 1.
        caps={p:v*ratio for p,v in caps.items()}
        if self.account.strategy.factor_family=='F2':
            for p,t in held.items():
                key=(t.id,pd.Timestamp(now).date().isoformat())
                if key not in self.daily and (now.hour,now.minute,now.second)==(0,5,0) and not snap.orders:
                    scale=self.scale(p,now)
                    other_gross=sum(x.amount*quotes[x.pair] for x in trades if x.id!=t.id)
                    other_cluster=sum(x.amount*quotes[x.pair] for x in trades if x.id!=t.id and groups[x.pair]==groups[p])
                    base=max(0.,min(equity*float(self.r['pair_exposure_cap']),
                        equity*float(self.r['trade_risk_fraction'])/(float(self.r['stop_price_distance_fraction'])+2*self.cost),
                        equity*gross-other_gross,equity*float(self.r['correlation_cluster_cap'])-other_cluster))
                    if math.isfinite(scale) and 0<scale<=1:self.daily[key]=base*scale/quotes[p]
                if key in self.daily:caps[p]=min(caps[p],self.daily[key]*quotes[p])
        return {held[p].id:max(0.,caps[p]/quotes[p]) for p in caps}

    def observe(self,now):
        if self.error:raise ValueError(self.error)
        now=pd.Timestamp(now).to_pydatetime()
        equity,trades,quotes,snap,exp=self.state(now)
        self.update_limits(now,equity,trades,exp)
        self.targets={} if self.exit_all or equity<=0 else self.allocations(now,equity,trades,quotes,snap,exp)
        self.now=now
        self.rows.append(dict(date=now,equity=equity,drawdown=1-equity/self.peak,
            gross=None if exp is None else float(exp['gross']),open_trades=len(trades),
            native_closed_profit=LocalTrade.bt_total_profit,hard_halt=self.hard,day_halt=self.day_stop is not None,
            warning=self.warn,correlation_complete=False if exp is None else exp['correlation_complete']))

    def scale(self,pair,now):
        return effective_daily_scale(self.hourly[pair].reset_index(drop=True),now)

    def budget(self,pair,now,rate,family):
        if self.error:raise ValueError(self.error)
        now=pd.Timestamp(now).to_pydatetime()
        boundary=(now.minute,now.second,now.microsecond)==(5,0,0) and (now.hour==0 if family=='F1' else now.hour%4==0)
        if not boundary or self.now!=now or self.blocked:return 0.
        equity,trades,quotes,snap,exp=self.state(now)
        if equity<=0 or exp['breaches'] or exp['position_count']>=int(self.r['max_open_positions']):return 0.
        if any(t.pair==pair for t in trades):return 0.
        groups,_=self.clusters(snap);price=max(float(rate),quotes[pair])
        h=pd.Timestamp(now).floor('h')-pd.Timedelta(hours=1)
        try:volume=float(self.hourly[pair].loc[h,'volume'])
        except KeyError:return 0.
        if not math.isfinite(volume) or volume<=0:return 0.
        budget=max(0.,min(equity*float(self.r['trade_risk_fraction'])/(float(self.r['stop_price_distance_fraction'])+2*self.cost),
            equity*float(self.r['pair_exposure_cap']),equity*float(self.r['gross_exposure_cap'])-float(exp['gross']),
            equity*float(self.r['net_absolute_exposure_cap'])-abs(float(exp['net'])),
            equity*float(self.r['correlation_cluster_cap'])-float(exp['by_cluster'].get(groups[pair],0)),
            volume*float(self.r['liquidity']['max_fraction_previous_hour_base_volume'])*price,
            self.account.wallets.get_available_stake_amount()/(1+self.cost)))
        scale=self.scale(pair,now) if family=='F2' else 1.
        return budget*float(rate)/price*scale if math.isfinite(scale) and 0<scale<=1 else 0.

    def attach(self):
        original=self.account.time_pair_generator
        def generator(*args,**kwargs):
            next_at=self.start
            for item in original(*args,**kwargs):
                current=pd.Timestamp(item[0])
                while next_at<=min(current,self.end-pd.Timedelta(minutes=5)):
                    # A skipped detail instant is only valid when flat; otherwise
                    # silently interpolating state would invent an execution path.
                    if next_at<current and LocalTrade.bt_trades_open:
                        raise ValueError('Native nonflat 5m event gap')
                    self.observe(next_at);next_at+=pd.Timedelta(minutes=5)
                yield item
            while next_at<self.end:
                if LocalTrade.bt_trades_open:raise ValueError('Native nonflat trailing event gap')
                self.observe(next_at);next_at+=pd.Timedelta(minutes=5)
        self.account.time_pair_generator=generator

    def fail(self,exc):
        self.error=f'{type(exc).__name__}: {exc}'
