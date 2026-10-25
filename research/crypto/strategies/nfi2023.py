"""Immutable 2023 NFI logic, plus an explicitly named leverage-only control."""

from NostalgiaForInfinityX4 import NostalgiaForInfinityX4


class NFI2023Reference(NostalgiaForInfinityX4):
    """Keep the published 5x normal / 3x rebuy behavior and original long-only flag."""


class NFI2023OneX(NostalgiaForInfinityX4):
    """Change leverage only; retain entry, exits and native position management."""

    futures_mode_leverage = 1.0
    futures_mode_leverage_rebuy_mode = 1.0
