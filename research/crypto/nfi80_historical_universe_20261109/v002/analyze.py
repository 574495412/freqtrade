"""Apply the frozen native evidence auditor to the new technical output version."""

from pathlib import Path

from research.crypto.nfi80_historical_universe_20261109 import analyze as audit
from research.crypto.nfi80_historical_universe_20261109.prepare import OUT


if __name__ == "__main__":
    audit.HERE = Path(__file__).resolve().parent
    audit.OUT = OUT / "accounts-v002"
    audit.main()
