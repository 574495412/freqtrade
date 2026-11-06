"""Separately registered synthetic driver with an inventory-only risk exit.

Ordinary observations and dispatch reuse the frozen native driver. This policy
must be installed when creating a new isolated account; old accounts cannot be
silently upgraded or reopened with the old policy.
"""
import copy

from quantity_exit import dispatch_quantity_exit
from native_factor_loop import NativeFactorLoop
from native_research_driver import NativeResearchDriver
from native_research_startup import NativeResearchStartup
from rd1 import RiskError, StateError, aware, parse


class QuantityResearchDriver(NativeResearchDriver):
    def __init__(self, startup, loop):
        if (type(startup) is not NativeResearchStartup or type(loop) is not NativeFactorLoop
                or startup.gate is not loop.gate or startup.wallet is not loop.wallet or not startup.started):
            raise RiskError('Quantity driver needs the started account and its native loop')
        self.startup, self.loop = startup, loop
        self.gate, self.wallet = loop.gate, loop.wallet
        self.closed = False
        self.binding = dict(policy='registered_quantity_exit_v1',
            startup=copy.deepcopy(startup.binding), loop=copy.deepcopy(self.gate.s['factor_loop_binding']))
        if self.gate.s.get('required_research_driver') not in (None, self.binding):
            raise StateError('Registered research driver binding changed; create a new isolated account')
        self.gate.s['required_research_driver'] = copy.deepcopy(self.binding)
        self.gate._persist()
        self.gate.research_driver = self

    def quantity_exit(self, trade, evidence, exchange, bot, now, *, exit_rate,
                      complete_sequence, inventory_balances=None):
        self._attached(now, bot)
        with self._scope(now, 'dispatch'):
            return dispatch_quantity_exit(self.loop.exits, trade, evidence, exchange, bot, now,
                exit_rate=exit_rate, complete_sequence=complete_sequence, inventory_balances=inventory_balances)

    def observe(self, batch_id, evidence, receipt, quotes, reports, events, bot, now, *,
                complete_sequence, complete_from_inception, funding_complete):
        self._attached(now, bot)
        watermark = self.loop.exits.s.get('quantity_inventory_watermark')
        if watermark and (evidence.snapshot.cursor <= watermark['cursor']
                or aware(evidence.snapshot.at) < parse(watermark['at'])
                or aware(evidence.snapshot.orders_at) < parse(watermark['orders_at'])):
            raise RiskError('Full account observation must follow the quantity inventory watermark')
        return super().observe(batch_id, evidence, receipt, quotes, reports, events, bot, now,
            complete_sequence=complete_sequence, complete_from_inception=complete_from_inception,
            funding_complete=funding_complete)
