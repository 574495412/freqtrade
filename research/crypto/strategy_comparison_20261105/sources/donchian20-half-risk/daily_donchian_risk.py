"""One uniform training-only risk calibration; signal and exit definitions are inherited."""

from daily_donchian import Donchian20, Donchian55


class Donchian20HalfRisk(Donchian20):
    risk_fraction_per_trade = 0.0025


class Donchian55HalfRisk(Donchian55):
    risk_fraction_per_trade = 0.0025
