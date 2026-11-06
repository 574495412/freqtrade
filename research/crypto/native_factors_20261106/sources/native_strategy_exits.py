"""Fixed F1/F2/control native exits using explicitly closed-bar assumptions.

No strategy startup permission, historical publication timestamps or exchange
receipts are invented. Missing signal bars do not disable native stop/ROI checks.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from types import SimpleNamespace
import hashlib

import numpy as np
import pandas as pd
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from native_snapshot import AccountEvidence
from native_evidence import decimal_text
from rd1 import RiskError, aware, dec, primitive, stamp

TIMING = 'BAR_CLOSE_NEXT_5M_ASSUMPTION_NOT_OBSERVED_RECEIPT'
NAMES = ('F1Momentum168Native', 'F2Volatility24Native', 'AverageRD1ControlNative')
COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume']


@dataclass(frozen=True)
class ExitRate:
    pair: str
    price: str
    at: datetime
    received_at: datetime
    source_id: str
    evidence_class: str
    kind: str = 'long_exit_bid'


@dataclass(frozen=True)
class ExitBarInputs:
    main: pd.DataFrame | None
    hourly: dict[str, pd.DataFrame]
    source_id: str
    timing: str = TIMING
    exit_rate: ExitRate | None = None


def _closed(frame, minutes, now):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    if not set(COLUMNS) <= set(frame):
        raise RiskError('Signal source needs explicit OHLCV columns')
    dates = pd.DatetimeIndex(frame.date)
    if dates.tz is None:
        raise RiskError('Signal bars need aware opening timestamps')
    dates = dates.tz_convert('UTC')
    keep = dates + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)
    result = frame.loc[keep, COLUMNS].copy()
    dates = dates[keep]
    if dates.has_duplicates or not dates.is_monotonic_increasing or not (dates == dates.floor(f'{minutes}min')).all():
        raise RiskError('Closed signal bars are duplicated, unordered or off grid')
    result['date'] = dates
    values = result[COLUMNS[1:]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any()
            or (result.high < result[['open','low','close']].max(axis=1)).any()
            or (result.low > result[['open','high','close']].min(axis=1)).any()):
        raise RiskError('Closed signal OHLCV values are invalid')
    return result.reset_index(drop=True)


def _frame_evidence(frame):
    return dict(rows=len(frame), sha256=hashlib.sha256(frame.to_json(orient='records', date_format='iso', double_precision=15).encode()).hexdigest(),
                first=None if frame.empty else frame.date.iloc[0].isoformat(),
                last=None if frame.empty else frame.date.iloc[-1].isoformat())


def evaluate_fixed_exit(strategy, trade, evidence: AccountEvidence, inputs, now):
    """Evaluate official signal/stop/ROI ordering on the real attached native Trade."""
    now = aware(now)
    if type(inputs) is not ExitBarInputs or not inputs.source_id or inputs.timing != TIMING:
        raise RiskError('Explicit source and frozen closed-bar assumption required')
    name = type(strategy).__name__
    f1 = name == NAMES[0]
    if (name not in NAMES or dec(decimal_text(strategy.stoploss)) != dec('-.05') or strategy.use_custom_stoploss
            or strategy.trailing_stop or strategy.order_types.get('stoploss_on_exchange')
            or strategy.timeframe != ('5m' if f1 else '4h')
            or strategy.minimal_roi != ({} if f1 else {0:.5})
            or not strategy.use_exit_signal or strategy.exit_profit_only or strategy.ignore_roi_if_entry_signal):
        raise RiskError('Strategy exit settings differ from fixed F1/F2/control contract')
    if not f1 and (strategy.buy_range_short.value != 8 or strategy.buy_range_long.value != 21):
        raise RiskError('Official EMA parameters changed')
    if (not trade.is_open or trade.amount <= 0 or trade.has_open_orders or trade.is_short or trade.leverage != 1
            or Trade.session.get(Trade, trade.id) is not trade):
        raise RiskError('Strategy exit needs a reconciled native long position')
    if trade.stop_loss is None or dec(decimal_text(trade.stop_loss)) < dec(decimal_text(trade.open_rate))*dec('.95'):
        raise RiskError('Native position has no verified common stop floor')
    quote = next((q for q in evidence.snapshot.quotes if q.pair == trade.pair), None)
    if quote is None or not 0 <= (now-aware(quote.at)).total_seconds() <= 15:
        raise RiskError('Native exit evaluation needs a fresh explicit quote')
    if inputs.exit_rate is None:
        if quote.kind != 'spot_bid':
            raise RiskError('Futures mark is not an exit rate; explicit independent exit bid required')
        rate = dec(quote.price)
        rate_proof = dict(basis='checked_account_spot_bid',price=str(rate),at=stamp(quote.at))
    else:
        r = inputs.exit_rate
        if (type(r) is not ExitRate or r.pair != trade.pair or r.kind != 'long_exit_bid'
                or not r.source_id or r.evidence_class != evidence.evidence_class
                or not 0 <= (now-aware(r.received_at)).total_seconds() <= 15
                or not 0 <= (aware(r.received_at)-aware(r.at)).total_seconds() <= 15
                or dec(r.price) <= 0):
            raise RiskError('Independent exit rate identity, timing or price invalid')
        rate = dec(r.price); rate_proof = asdict(r)
    universe = tuple(strategy.config['exchange']['pair_whitelist'])
    if trade.pair not in universe:
        raise RiskError('Trade outside strategy universe')
    minutes = 5 if f1 else 240
    main = _closed(inputs.main, minutes, now)
    hours = {p:_closed(inputs.hourly.get(p),60,now) for p in universe} if f1 else {}
    enter = exit_ = False; tag = None; ready = False
    expected = pd.Timestamp(now).floor(f'{minutes}min')-pd.Timedelta(minutes=minutes)
    if not main.empty and main.date.iloc[-1] == expected:
        # Native EMA needs uninterrupted primary bars; do not bridge a missing candle.
        consecutive = len(main) <= 1 or (main.date.diff().iloc[1:] == pd.Timedelta(minutes=minutes)).all()
        if f1 or (consecutive and len(main) >= 21):
            had_dp = hasattr(strategy,'dp'); old_dp = getattr(strategy,'dp',None)
            strategy.dp = SimpleNamespace(get_pair_dataframe=lambda *,pair,timeframe: hours.get(pair,pd.DataFrame(columns=COLUMNS)))
            try:
                frame = strategy.populate_indicators(main.copy(), {'pair':trade.pair})
                frame = strategy.populate_entry_trend(frame, {'pair':trade.pair})
                frame = strategy.populate_exit_trend(frame, {'pair':trade.pair})
                enter,exit_,tag = strategy.get_exit_signal(trade.pair,strategy.timeframe,frame,is_short=False)
                ready = bool(frame.factor_universe_valid.iloc[-1]) if f1 else True
            finally:
                if had_dp: strategy.dp = old_dp
                else: del strategy.dp
    exits = IStrategy.should_exit(strategy,trade,float(rate),now,enter=bool(enter),exit_=bool(exit_))
    # Official should_exit updates extrema and may initialize stop state. Persist
    # its actual native effects, never a shadow Trade used only for evaluation.
    Trade.commit()
    proof = primitive(dict(strategy=name,trade_id=trade.id,at=stamp(now),source_id=inputs.source_id,timing=inputs.timing,
        quote_price=str(quote.price),quote_at=stamp(quote.at),native_exit_rate=rate_proof,
        signal_ready=ready,enter=bool(enter),exit=bool(exit_),exit_tag=tag,
        primary=_frame_evidence(main),hourly={p:_frame_evidence(f) for p,f in hours.items()},
        ordered_exits=[dict(type=x.exit_type.value,reason=x.exit_reason) for x in exits]))
    return exits, proof
