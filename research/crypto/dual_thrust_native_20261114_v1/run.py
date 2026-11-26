"""Reuse the bounded serial controller with this study's frozen registration."""

from pathlib import Path

from research.crypto.tsmom_risk_native_20261114_v1 import run as controller

if __name__=='__main__':
    controller.HERE=Path(__file__).resolve().parent
    controller.main()
