"""Inventory-only forced exit through the existing native exit journal.

No equity, cash balance, mark, funding update or entry permission is invented.
Only a previously latched RD1 full exit may use this synthetic local adapter.
The normal full-account reconciliation path remains mandatory after submission.
"""
import copy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import sys

OLD = Path(__file__).resolve().parents[1] / 'native_factors_20261106'
sys.path.insert(0, str(OLD / 'sources'))

from freqtrade.enums import ExitCheckTuple, ExitType
from freqtrade.freqtradebot import FreqtradeBot
from freqtrade.persistence import Trade
from freqtrade.wallets import Wallets
from native_account import event_ns
from native_evidence import decimal_text
from native_exits import LocalExitExchange, _ExitBot
from native_snapshot import EPS
from native_strategy_exits import ExitRate
from native_wallets import _LocalWalletSource
from rd1 import RiskError, StateError, aware, dec, digest, primitive, stamp


@dataclass(frozen=True)
class InventoryBalances:
    account_id: str
    source_id: str
    evidence_class: str
    at: datetime
    received_at: datetime
    # Base inventory only. Missing quote cash remains unknown, never zero.
    balances: dict


class _NoValuationWallet:
    def __getattr__(self, name):
        raise RiskError('Quantity exit must not read an unverified valuation wallet: ' + name)


class _InventoryWallets(Wallets):
    def _known(self, currency):
        if currency not in self._wallets:
            raise RiskError('Inventory receipt does not contain this currency: ' + currency)

    def get_free(self, currency):
        self._known(currency)
        return super().get_free(currency)

    def get_used(self, currency):
        self._known(currency)
        return super().get_used(currency)

    def get_total(self, currency):
        self._known(currency)
        return super().get_total(currency)

    def get_starting_balance(self):
        raise RiskError('Inventory-only wallet cannot supply capital')

    def get_total_stake_amount(self):
        raise RiskError('Inventory-only wallet cannot authorize stake')

    def get_available_stake_amount(self):
        raise RiskError('Inventory-only wallet cannot authorize stake')


class _BotView:
    def __init__(self, bot, wallets):
        self.bot, self.wallets = bot, wallets

    def __getattr__(self, name):
        return getattr(self.bot, name)


def _spot_wallet(controller, evidence, balances, now):
    if (type(balances) is not InventoryBalances or balances.account_id != evidence.account_id
            or not balances.source_id or balances.evidence_class != 'synthetic'
            or aware(balances.at) != aware(evidence.snapshot.orders_at)
            or not aware(balances.at) <= aware(balances.received_at) <= aware(evidence.snapshot.at)
            or not 0 <= (now-aware(balances.at)).total_seconds() <= 15):
        raise RiskError('Fresh explicit spot inventory balances are required')
    expected = {}
    for position in evidence.snapshot.positions:
        currency = controller.wallet.source.get_pair_base_currency(position.pair)
        if currency in expected:
            raise RiskError('Ambiguous base inventory')
        expected[currency] = dec(position.quantity)
    if set(balances.balances) != set(expected):
        raise RiskError('Spot inventory currency coverage differs from positions')
    parsed = {}
    for currency, quantity in expected.items():
        values = balances.balances[currency]
        if not isinstance(values, dict) or set(values) != {'free', 'used', 'total'}:
            raise RiskError('Explicit base free/used/total required')
        free, used, total = [dec(values[k]) for k in ['free', 'used', 'total']]
        if free != quantity or used != 0 or total != quantity:
            raise RiskError('Spot exit inventory is reserved or differs from matched quantity')
        parsed[currency] = {k: float(dec(v)) for k, v in values.items()}
    source = _LocalWalletSource(list(controller.wallet.source.markets.values()), 'spot', controller.wallet.clock)
    source.frame = dict(balances=parsed, positions=[])
    source.now = now
    try:
        wallet = _InventoryWallets(dict(dry_run=False, dry_run_wallet={}, trading_mode='spot'), source)
    except Exception:
        source.close()
        raise
    return wallet, source


def dispatch_quantity_exit(controller, trade, evidence, exchange, bot, now, *,
                           exit_rate, complete_sequence, inventory_balances=None):
    """Submit one latched full exit; neither manufacture NAV nor clear entry barriers.

    Caller supplies a fresh complete order/position inventory. Known financial
    events are matched to native history, but missing cash/mark/funding coverage
    stays unknown. Independent execution bid is still required for a limit exit.
    """
    now = aware(now)
    evidence = copy.deepcopy(evidence)
    with controller._mutex:
        controller._ready(now)
        gate, wallet, provider, risk = controller.gate, controller.wallet, controller.provider, controller.risk
        wallet._ready(now)
        provider._ready()
        gate.require_account_batch_clear()
        gate.require_research_driver(now, 'dispatch')
        binding = gate.s.get('required_research_driver')
        if binding is not None and binding.get('policy') != 'registered_quantity_exit_v1':
            raise StateError('Quantity exits require the separately registered quantity driver policy')
        # A failed inventory observation must not leave a previous entry grant.
        wallet._checked_evidence = None
        wallet.s['state'] = 'QUANTITY_RECONCILIATION_PENDING'
        wallet._persist()
        if (evidence.evidence_class != 'synthetic' or evidence.source_id != provider.source_id
                or type(exchange) is not LocalExitExchange or exchange.controller is not controller
                or bot.wallets is not wallet.wallets):
            raise RiskError('Quantity exit needs its existing local source/controller/bot bindings')
        if (not trade.is_open or trade.amount <= 0 or trade.is_short or trade.leverage != 1
                or trade.has_open_orders or trade.open_sl_orders or Trade.session.get(Trade, trade.id) is not trade):
            raise RiskError('Quantity exit needs one committed native 1x position without orders')
        if evidence.snapshot.orders:
            raise RiskError('Cancel and reconcile open orders before an inventory-only exit')
        if any(item.get('lifecycle', {}).get('state') != 'TERMINAL_RECONCILED'
               for item in gate.lifecycle.s['requests'].values()):
            raise RiskError('Initial submission/fill effects remain unresolved')
        gate.after_fill_guard.require_entry_clear()
        snap = provider.bridge.validate(evidence, now, valuation_required=False)
        if (type(complete_sequence) is not int or complete_sequence != provider.ledger.last_sequence
                or provider.s['last_cursor'] is None or snap.cursor <= provider.s['last_cursor']):
            raise RiskError('New inventory cursor and exact known event watermark required')
        if provider.ledger.last_event_ns is not None and event_ns(snap.orders_at) < provider.ledger.last_event_ns:
            raise RiskError('Inventory predates known financial events')
        campaigns = provider._native_events()
        expected = {p['campaign_id']: (pair, p) for pair, p in provider.ledger.positions.items()}
        if set(expected) != {p.position_id for p in snap.positions}:
            raise RiskError('Inventory does not cover known financial positions')
        for position in snap.positions:
            pair, known = expected[position.position_id]
            if (position.pair != pair or abs(dec(position.quantity)-known['quantity']) > EPS
                    or abs(dec(position.entry_price)-known['entry_price']) > EPS
                    or campaigns.get(position.position_id) != evidence.position_bindings[position.position_id]):
                raise RiskError('Inventory differs from known financial/native position history')
        if (type(exit_rate) is not ExitRate or exit_rate.pair != trade.pair
                or exit_rate.kind != 'long_exit_bid' or not exit_rate.source_id
                or exit_rate.evidence_class != 'synthetic'
                or not aware(exit_rate.at) <= aware(exit_rate.received_at) <= now
                or not 0 <= (now-aware(exit_rate.at)).total_seconds() <= 15 or dec(exit_rate.price) <= 0):
            raise RiskError('A fresh independent execution bid is required; mark is not a substitute')
        if any(item['state'] != 'TERMINAL_RECONCILED' for item in controller.s['actions'].values()):
            raise StateError('An earlier exit needs query and complete account reconciliation')
        source_id = next((pid for pid, tid in evidence.position_bindings.items() if tid == trade.id), None)
        decision = risk.evaluate(snap, now)
        action = next((a for a in decision.actions if a['kind'] == 'EXIT_POSITION' and a['target'] == source_id), None)
        if action is None or not action['claimable']:
            raise RiskError('No previously latched, claimable RD1 full-position exit')
        if decision.entry_allowed:
            raise RiskError('Quantity-only dispatch cannot retain entry permission')
        amount, rate = float(trade.amount), float(dec(exit_rate.price))
        if (exchange.amount_to_precision(trade.pair, amount) != amount
                or exchange.price_to_precision(trade.pair, rate) != rate):
            raise RiskError('Native precision would change the explicit quantity or bid')
        old = controller.s['actions'].get(action['id'])
        if old and (not old['requires_further_exit'] or snap.cursor <= old['reconciled_cursor']):
            raise StateError('Completed exit cannot be repeated without new remaining inventory')
        prior_inventory = controller.s.get('quantity_inventory_watermark')
        if prior_inventory and snap.cursor <= prior_inventory['cursor']:
            raise StateError('Quantity inventory cursor must advance after an attempted exit')
        temporary_source = None
        quantity_wallet = _NoValuationWallet()
        if risk.market == 'spot':
            quantity_wallet, temporary_source = _spot_wallet(controller, evidence, inventory_balances, now)
        try:
            ordinal = 1 if old is None else old['attempt']+1
            client_key = dict(account=risk.s['identity'], action=action['id'])
            if ordinal > 1:
                client_key['attempt'] = ordinal
            prior_funding = sum((dec(decimal_text(o.funding_fee)) for o in trade.orders if o.funding_fee is not None), D(0))
            total_funding = D(0) if trade.funding_fees is None else dec(decimal_text(trade.funding_fees))
            before_funding = trade.funding_fees
            ticket = dict(state='SUBMISSION_UNKNOWN', action=risk.claim_action(action['id'], now), attempt=ordinal,
                prior_attempts=[] if old is None else old.get('prior_attempts', [])+[
                    {k: copy.deepcopy(v) for k, v in old.items() if k != 'prior_attempts'}],
                claimed_at=stamp(now), trade_id=trade.id, quantity_before=decimal_text(trade.amount),
                request=dict(pair=trade.pair, ordertype='limit', side='sell', amount=amount, rate=rate,
                    leverage=1, reduceOnly=risk.market == 'futures', time_in_force='GTC', initial_order=False),
                source_position_id=source_id, client_order_id='rd1x-'+digest(client_key)[:28],
                running_funding_credit=str(total_funding-prior_funding), last_filled_at=stamp(trade.date_last_filled_utc),
                native_binding=None, exit_type=ExitType.FORCE_EXIT.value, strategy_evaluation=None,
                target_remaining_quantity='0', reduction_evaluation=None,
                checkpoint_hash=digest(asdict(evidence)), quantity_only=True,
                inventory_receipt=primitive(asdict(evidence)), execution_bid=primitive(asdict(exit_rate)),
                inventory_balances=None if inventory_balances is None else primitive(asdict(inventory_balances)),
                known_event_sequence=complete_sequence, account_valuation_verified=False,
                funding_scope='preserved known native value; not a new complete funding observation')
            controller.s['actions'][action['id']] = ticket
            controller.s['quantity_inventory_watermark'] = dict(cursor=snap.cursor,
                at=stamp(snap.at), orders_at=stamp(snap.orders_at), receipt_hash=digest(asdict(evidence)))
            controller._persist()
            token = exchange.ticket.set(ticket)
            try:
                proxy = _ExitBot(_BotView(bot, quantity_wallet), exchange, controller, action['id'])
                result = FreqtradeBot.execute_trade_exit(proxy, trade, rate, ExitCheckTuple(ExitType.FORCE_EXIT),
                    exit_tag=ticket['client_order_id'], ordertype='limit', skip_custom_exit_price=True)
            finally:
                exchange.ticket.reset(token)
            if not result:
                Trade.session.rollback()
                raise RiskError('Native quantity exit remains submission-unknown')
            if trade.funding_fees != before_funding:
                raise RiskError('Quantity dispatch unexpectedly changed known funding')
            controller.bind(action['id'], now)
            return action['id']
        finally:
            if temporary_source is not None:
                temporary_source.close()
