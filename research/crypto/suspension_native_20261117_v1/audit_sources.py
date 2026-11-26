"""Independent Decimal reconciliation of archive trades, klines and connector."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(path):
    checksum = Path(str(path) + ".CHECKSUM").read_text().split()[0]
    assert sha(path) == checksum
    with ZipFile(path) as z:
        assert z.testzip() is None and len(z.namelist()) == 1
        return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))


def main():
    results = []
    for symbol, start in (("CRVUSDT", 1777444200000), ("GALAUSDT", 1777444800000)):
        source = HERE / "sources"
        trades = rows(source / f"{symbol}-aggTrades-2026-04-29.zip")
        klines = rows(source / f"{symbol}-1m-2026-04-29.zip")
        assert len(klines) == 1440
        assert [int(r["open_time"]) for r in klines] == list(
            range(1777420800000, 1777507200000, 60000)
        )
        selected = [r for r in trades if start <= int(r["transact_time"]) < start + 60000]
        candle = next(r for r in klines if int(r["open_time"]) == start)
        connector = json.loads((source / f"{symbol}_connector.json").read_text())
        observed = next(r for r in connector["structuredContent"]["result"] if r[0] == start)
        volume = sum((D(r["quantity"]) for r in selected), D(0))
        count = sum(int(r["last_trade_id"]) - int(r["first_trade_id"]) + 1 for r in selected)
        prices = [D(r["price"]) for r in selected]
        assert volume == D(candle["volume"]) == D(observed[5])
        assert count == int(candle["count"]) == observed[8]
        assert (
            [prices[0], max(prices), min(prices), prices[-1]]
            == [D(candle[c]) for c in ("open", "high", "low", "close")]
            == [D(x) for x in observed[1:5]]
        )
        ids = [int(r["agg_trade_id"]) for r in selected]
        assert ids == list(range(ids[0], ids[-1] + 1))
        result = {
            "symbol": symbol,
            "nominal_block_start": datetime.fromtimestamp(start / 1000, UTC).isoformat(),
            "aggregate_records": len(selected),
            "underlying_trades": count,
            "base_volume": str(volume),
            "first_trade": datetime.fromtimestamp(
                int(selected[0]["transact_time"]) / 1000, UTC
            ).isoformat(),
            "last_trade": datetime.fromtimestamp(
                int(selected[-1]["transact_time"]) / 1000, UTC
            ).isoformat(),
            "archive_connector_minute_agree": True,
            "selected_trades": selected,
        }
        results.append(result)
    receipts = json.loads((HERE / "archive_receipts.json").read_text())
    for row in receipts:
        assert sha(row["path"]) == row["sha256"] and row["http_status"] == 200
    output = {
        "audited_at": datetime.now(UTC).isoformat(),
        "passed": True,
        "results": results,
        "conclusion": (
            "Whole nominal minute cannot be modeled as zero matching;"
            "new/cancel/amend permissions and pending matching require separate contracts"
        ),
        "limits": (
            "Same exchange sources,not independent authorities;current archive version does not "
            "certify first-seen or exact rule activation. Market trades do not prove "
            "a hypothetical client order would fill."
        ),
        "new_financial_accounts": 0,
        "goal_complete": False,
    }
    (HERE / "source_audit.json").write_text(json.dumps(output, indent=2) + "\n")
    print("Checked4ZIPchecksums;two paused minutes contain10and11actual trades")


if __name__ == "__main__":
    main()
