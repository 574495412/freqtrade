"""Research-only mode, snapshot and clock constraints; ordinary paths closed."""
import importlib.util
import sys
from datetime import UTC,datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT.parent/'CZ-Agent/vendor/freqtrade'))
from freqtrade.enums import RunMode,TradingMode
from freqtrade.persistence import Trade

spec=importlib.util.spec_from_file_location('matched_precision_checks',ROOT/'freqtrade/optimize/matched_price_precision.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Shell(module.MatchedPricePrecisionMixin):pass


@pytest.fixture(autouse=True)
def no_database():
    old=Trade.use_db;Trade.use_db=False
    yield
    Trade.use_db=old


def shell(mode='spot'):
    a=Shell();a.trading_mode=TradingMode(mode)
    suffix=':USDT'if mode=='futures'else''
    pairs=tuple(p+suffix for p in ['BTC/USDT','ETH/USDT'])
    a.config=dict(runmode=RunMode.BACKTEST,dry_run=True,research_matched_nfi=True,
        exchange={'pair_whitelist':list(pairs)})
    a.ticks={p:.01 for p in pairs}
    a.exchange=SimpleNamespace(precision_mode_price=4,get_precision_price=lambda p:a.ticks[p])
    return a,pairs


@pytest.mark.parametrize('mode',['spot','futures'])
def test_exact_snapshot_tick_on_both_supported_markets(mode):
    a,pairs=shell(mode);a.setup_frozen_price_precision(pairs=pairs)
    for p in pairs:assert a.get_pair_precision(p,datetime(2025,1,1,tzinfo=UTC))==(.01,4)


@pytest.mark.parametrize('fault',['live','dry','flag','database','whitelist','precision','mode','tick','clock','pair'])
def test_context_cannot_change_after_binding(fault):
    a,pairs=shell();a.setup_frozen_price_precision(pairs=pairs)
    clock=datetime(2025,1,1,tzinfo=UTC);pair=pairs[0]
    if fault=='live':a.config['runmode']=RunMode.LIVE
    if fault=='dry':a.config['dry_run']=False
    if fault=='flag':a.config['research_matched_nfi']=False
    if fault=='database':Trade.use_db=True
    if fault=='whitelist':a.config['exchange']['pair_whitelist'].reverse()
    if fault=='precision':a.exchange.precision_mode_price=2
    if fault=='mode':a.trading_mode=TradingMode.FUTURES
    if fault=='tick':a.ticks[pair]=.1
    if fault=='clock':clock=clock.replace(tzinfo=None)
    if fault=='pair':pair='OTHER/USDT'
    with pytest.raises(ValueError):a.get_pair_precision(pair,clock)


@pytest.mark.parametrize('tick',[True,0.,-1.,float('nan'),float('inf')])
def test_invalid_declared_tick_cannot_initialize_account(tick):
    a,pairs=shell();a.ticks[pairs[0]]=tick
    with pytest.raises(ValueError):a.setup_frozen_price_precision(pairs=pairs)


@pytest.mark.parametrize('pairs',[('BTC/USDT',),('BTC/USDT','BTC/USDT'),('ETH/USDT','BTC/USDT')])
def test_universe_identity_is_frozen_not_just_cardinality(pairs):
    a,_=shell()
    with pytest.raises(ValueError):a.setup_frozen_price_precision(pairs=pairs)
