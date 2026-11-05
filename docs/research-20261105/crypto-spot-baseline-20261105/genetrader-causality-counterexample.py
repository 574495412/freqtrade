"""Reproduce the archived GeneTrader BTC index-alignment expressions only.

This does not import or execute the third-party strategy or backtest a wallet.
The deliberately offset candle starts make the dependency explicit; they do
not assert that such an offset occurred in the author's historical report.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

SOURCE = Path("/Users/chrise/GitHub/TradingAgents/data/research/github_genetrader_source_20261028_v001/GeneTrader_gen10_1734895087_6007.py")
OUT = Path(__file__).with_name("genetrader-causality-counterexample.json")

source_text = SOURCE.read_text()
expected = ("informative_btc = informative.copy().shift(1)",
            "dataframe['btc_close'] = informative_btc['close']",
            "dataframe['btc_close'].rolling(24).max() >= (dataframe['btc_close'] * 1.03 )")
if not all(text in source_text for text in expected):
    raise ValueError("Archived expressions differ")

decision = pd.Timestamp("2025-01-01T02:00:00Z")
pair = pd.DataFrame({"date": pd.date_range("2025-01-01T00:00:00Z", periods=25, freq="5min")})
btc_a = pd.DataFrame({"date": pd.date_range("2025-01-01T01:00:00Z", periods=30, freq="5min"), "close": [100.0] * 30})
btc_b = btc_a.copy()
btc_b.loc[23, "close"] = 80.0
if not btc_a.loc[btc_a.date <= decision].equals(btc_b.loc[btc_b.date <= decision]):
    raise ValueError("Inputs through decision changed")

def evaluate(btc):
    dataframe = pair.copy()
    informative_btc = btc.copy().shift(1)
    dataframe["btc_close"] = informative_btc["close"]
    predicate = dataframe["btc_close"].rolling(24).max() >= (dataframe["btc_close"] * 1.03)
    return {"decision_btc_close":float(dataframe.loc[24,"btc_close"]), "btc_dump_predicate":bool(predicate.loc[24])}

a, b = evaluate(btc_a), evaluate(btc_b)
if a["btc_dump_predicate"] == b["btc_dump_predicate"]:
    raise ValueError("Counterexample did not change the BTC predicate")
evidence = {"schema":"genetrader-btc-index-prefix-counterexample.v1", "source_path":str(SOURCE),
            "source_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "decision_time_utc":decision.isoformat(), "pair_start_utc":str(pair.date.iloc[0]),
            "btc_start_utc":str(btc_a.date.iloc[0]), "changed_btc_candle_time_utc":str(btc_a.loc[23,"date"]),
            "input_values_through_decision_identical":True, "btc_future_close_a":100.0, "btc_future_close_b":80.0,
            "case_a":a, "case_b":b, "future_values_change_entry_predicate":True,
            "scope":"Synthetic offset RangeIndex inputs show a conditional future dependency in the BTC gate only; other entry conditions are not evaluated. Does not establish occurrence in author report or current continuous dataset.",
            "source_strategy_executed":False, "economic_account_run":False, "orders_enabled":False}
OUT.write_text(json.dumps(evidence, indent=2)+"\n")
print(json.dumps(evidence, indent=2))
