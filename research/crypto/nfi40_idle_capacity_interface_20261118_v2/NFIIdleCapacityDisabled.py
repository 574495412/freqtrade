"""Supported IStrategy inheritance control; auxiliary execution is hard disabled."""

import hashlib
import importlib.util
import sys
from pathlib import Path

import freqtrade


SOURCE = Path(
    "/Users/chrise/GitHub/freqtrade/research/crypto/strategy_comparison_20261105/sources/nfi-x8-1x/NostalgiaForInfinityX8.py"
)
SOURCE_SHA = "fc5cb221f29ebca5f336429d97920eb93917cdd87f9769381b23b467ca6d5bf3"
VENDOR = Path("/Users/chrise/GitHub/CZ-Agent/vendor/freqtrade")


def load_source():
    if Path(freqtrade.__file__).resolve() != (VENDOR / "freqtrade/__init__.py").resolve():
        raise ValueError("Use the pinned native vendor, not another installed Freqtrade")
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError("Pinned NFI source hash changed")
    name = "_idle_capacity_pinned_nfi_x8"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SOURCE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].NostalgiaForInfinityX8


PinnedNFI = load_source()


class NFIIdleCapacityDisabled(PinnedNFI):
    """Inherit every source callback unchanged; only research-mode admission differs.

    This class does not implement the enabled combination. No event/tag injection,
    source filtering, wallet partition, callback replacement or priority scheduler
    exists in this control. Full-account parity still needs a future native run.
    """

    def __init__(self, config):
        if config.get("research_idle_aux_enabled") is not False:
            raise ValueError("Auxiliary must be explicitly disabled; enabled route is not admitted")
        mode = config.get("runmode")
        if getattr(mode, "value", mode) != "backtest" or not config.get("dry_run"):
            raise ValueError("This preparation class is backtest research only")
        if "available_capital" in config:
            raise ValueError("available_capital is not a shared-wallet allocator")
        super().__init__(config)
