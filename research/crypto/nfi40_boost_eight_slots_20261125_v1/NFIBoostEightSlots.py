"""Keep the fixed Top Coins stake policy with the frozen eight-slot wallet rule."""

from pathlib import Path

from freqtrade.resolvers.iresolver import PathModifier

with PathModifier(Path(__file__).resolve().parent):
    from NFIEightSlots import NFIEightSlots
    from StrengthAllocation import FlatTopBoost


class NFIBoostEightSlots(NFIEightSlots, FlatTopBoost):
    pass
