"""Factorial native diagnostics: same monthly core, three small policies, two admission rules."""

from research.crypto.strategies.intrabar_small import IntrabarSmallBase
from research.crypto.strategies.ranked_first_hit import RankedFirstHitBase


class JointRiskRankedBase(RankedFirstHitBase, IntrabarSmallBase):
    pass


class JointIntrabarLinear(IntrabarSmallBase):
    policy = "LinearStatic"


class JointIntrabarStatic(IntrabarSmallBase):
    policy = "BoostStatic"


class JointIntrabarMonthly(IntrabarSmallBase):
    policy = "BoostMonthly"


class JointRankedLinear(JointRiskRankedBase):
    policy = "LinearStatic"


class JointRankedStatic(JointRiskRankedBase):
    policy = "BoostStatic"


class JointRankedMonthly(JointRiskRankedBase):
    policy = "BoostMonthly"
