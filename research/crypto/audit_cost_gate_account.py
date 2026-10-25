"""Verify executed entry gates separately from the existing cash and risk replay."""

from pathlib import Path

import pandas as pd

from freqtrade.research.data import read, save, sha
from research.crypto.audit_btc_residual import campaign_audit as risk_audit
from research.crypto.audit_ensemble import raw_trades


def campaign_audit(folder, name):
    result = risk_audit(folder, name)
    plan, trades = read(folder / "plan.json"), raw_trades(folder, name)
    predictions = {}
    for trade in trades:
        symbol = trade["pair"].split("/")[0] + "USDT"
        if symbol not in predictions:
            path = Path(plan["settings"]["research_entry_predictions"]) / f"{symbol}.parquet"
            if sha(path) != plan["settings"]["research_entry_prediction_hashes"][symbol]:
                raise ValueError("Executed forecast data changed")
            predictions[symbol] = pd.read_parquet(path).set_index("date")
        entry = pd.to_datetime(trade["open_timestamp"], unit="ms", utc=True)
        row = predictions[symbol].loc[entry - pd.Timedelta(minutes=5)]
        if not row.model_ready or row.opportunity_side != (-1 if trade["is_short"] else 1):
            raise ValueError("Executed entry is outside the common forecast population")
        if trade["enter_tag"] != row.opportunity_sleeve + "_residual":
            raise ValueError("Executed entry changed the preselected layer")
        if name != "ResidualOpportunityControl" and row.predicted_net_horizon_return <= 0:
            raise ValueError("Negative forecast admitted by a positive-net gate")
        if name == "ResidualWinNet" and row.predicted_horizon_positive_probability < 0.8:
            raise ValueError("Horizon probability gate violated")
    receipt = {
        "passed": True,
        "actual_positions_checked": len(trades),
        "empty_account_is_profit_evidence": False,
        "audit_source_sha256": sha(Path(__file__)),
        "scope": "Executed entry membership and forecast thresholds, not forecast calibration "
        "or actual whole-position win probability.",
    }
    save(folder / "entry_gate_audit.json", receipt)
    result["entry_gate_audit"] = receipt
    return result
