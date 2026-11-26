"""Read-only initial-order diagnostics, separate from DCA submission unknowns."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent


def main():
    plan = json.loads((HERE / "initial_order_screen_protocol.json").read_text())
    rows = []
    for case in plan["accounts"]:
        path = Path(case["archive"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
        with ZipFile(path) as z:
            assert z.testzip() is None
            payloads = [
                json.loads(z.read(n), parse_float=D)
                for n in z.namelist()
                if n.endswith(".json") and not n.endswith("_config.json")
            ]
        stats = next(iter(next(p["strategy"] for p in payloads if "strategy" in p).values()))
        for screen in plan["screens"]:
            start = int(datetime.fromisoformat(screen["conservative_start"]).timestamp() * 1000)
            stop = int(datetime.fromisoformat(screen["transition_start"]).timestamp() * 1000)
            broad, narrow, dca = [], [], []
            for index, trade in enumerate(stats["trades"]):
                if trade["pair"] != screen["pair"]:
                    continue
                orders = [(j, o) for j, o in enumerate(trade["orders"]) if o["ft_is_entry"]]
                for k, (j, order) in enumerate(orders):
                    filled = int(order["order_filled_timestamp"])
                    amount, price = D(str(order["amount"])), D(str(order["safe_price"]))
                    value = amount if screen["field"] == "minimum_amount" else amount * price
                    if filled >= stop or value >= D(screen["old_value"]):
                        continue
                    record = {
                        "trade_index": index,
                        "order_array_index": j,
                        "campaign_open_ms": trade["open_timestamp"],
                        "filled_ms": filled,
                        "is_short": trade["is_short"],
                        "amount": str(amount),
                        "fill_price": str(price),
                        "value": str(value),
                        "initial_entry": k == 0,
                    }
                    if k == 0:
                        broad.append(record)
                        if start <= int(trade["open_timestamp"]) < stop and start <= filled < stop:
                            narrow.append(record)
                    else:
                        dca.append(record)
            rows.append(
                {
                    "account": case["account"],
                    "pair": screen["pair"],
                    "field": screen["field"],
                    "narrow_count": len(narrow),
                    "narrow": narrow,
                    "broad_initial_count": len(broad),
                    "broad_initial": broad,
                    "DCA_fill_only_sensitivity_count": len(dca),
                    "DCA_fill_only_sensitivity": dca,
                }
            )
    output = {
        "scope": "Diagnostic only;not repriced returns or certified rule violations",
        "rows": rows,
        "new_financial_accounts": 0,
        "historical_rule_admission": False,
    }
    (HERE / "initial_order_screen.json").write_text(json.dumps(output, indent=2) + "\n")
    print("Rebuilt6native account initial/DCA diagnostics without financial replay")


if __name__ == "__main__":
    main()
