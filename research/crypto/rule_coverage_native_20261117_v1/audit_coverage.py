"""Fresh source-identity and full frozen-universe rule coverage review."""

import base64
import gzip
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D
from email.utils import parsedate_to_datetime
from itertools import pairwise
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode(raw, headers):
    return gzip.decompress(raw) if headers.get("content-encoding") == "gzip" else raw


def extract(payload, pairs):
    symbols = {row["symbol"]: row for row in payload["symbols"]}
    assert len(symbols) == len(payload["symbols"])
    result = {}
    for pair in pairs:
        base = pair.split("/")[0]
        item = symbols[base + "USDT"]
        assert (
            item["baseAsset"],
            item["quoteAsset"],
            item["marginAsset"],
            item["contractType"],
        ) == (base, "USDT", "USDT", "PERPETUAL")
        filters = {f["filterType"]: f for f in item["filters"]}
        assert len(filters) == len(item["filters"])
        row = {
            "tickSize": filters["PRICE_FILTER"]["tickSize"],
            "minPrice": filters["PRICE_FILTER"]["minPrice"],
            "maxPrice": filters["PRICE_FILTER"]["maxPrice"],
            "stepSize": filters["LOT_SIZE"]["stepSize"],
            "minQty": filters["LOT_SIZE"]["minQty"],
            "maxQty": filters["LOT_SIZE"]["maxQty"],
            "minNotional": filters["MIN_NOTIONAL"]["notional"],
            "status": item["status"],
        }
        assert all(D(v).is_finite() and D(v) > 0 for k, v in row.items() if k != "status")
        result[pair] = row
    return result


def main():
    protocol = read(HERE / "protocol.json")
    for path, digest in protocol["inputs"].items():
        assert sha(path) == digest, path
    pairs = protocol["pairs"]
    assert len(pairs) == len(set(pairs)) == 40
    receipts = read(ROOT / "research/crypto/historical_rule_archive_20261106/snapshot_result.json")
    points, source_hashes = [], {}
    for r in receipts["rows"]:
        if r["market"] != "futures":
            continue
        path = ROOT / r["path"]
        raw = path.read_bytes()
        assert sha(path) == r["sha256"] and len(raw) == r["bytes"]
        body = decode(raw, r["headers"])
        digests = [
            base64.b32encode(hashlib.sha1(b, usedforsecurity=False).digest()).decode()
            for b in (raw, body)
        ]
        assert r["digest"] in digests
        stamp = parsedate_to_datetime(r["headers"]["memento-datetime"]).astimezone(UTC)
        assert stamp.strftime("%Y%m%d%H%M%S") == r["timestamp"]
        extracted = extract(json.loads(body), pairs)
        for pair, values in extracted.items():
            assert all(values[k] == r["complete_rules"][pair][k] for k in values)
        points.append(
            {
                "captured_at": stamp.isoformat(),
                "path": str(path),
                "sha256": sha(path),
                "rules": extracted,
                "scope": "Captured point only;no continuous effective interval",
            }
        )
        source_hashes[str(path)] = sha(path)
    r = read(HERE / "baseline_replay_receipt.json")
    q = read(HERE / "baseline_replay_protocol.json")
    raw = Path(r["path"]).read_bytes()
    assert (
        r["archive_identity_passed"] and r["http_status"] == 200 and sha(r["path"]) == r["sha256"]
    )
    body = decode(raw, r["headers"])
    assert hashlib.sha256(body).hexdigest() == r["decoded_sha256"]
    assert q["case"]["digest"] in [
        base64.b32encode(hashlib.sha1(b, usedforsecurity=False).digest()).decode()
        for b in (raw, body)
    ]
    stamp = parsedate_to_datetime(r["headers"]["memento-datetime"]).astimezone(UTC)
    assert stamp.strftime("%Y%m%d%H%M%S") == q["case"]["timestamp"]
    points.append(
        {
            "captured_at": stamp.isoformat(),
            "path": r["path"],
            "sha256": r["sha256"],
            "rules": extract(json.loads(body), pairs),
            "scope": "Real pre-window point,not a validity interval extending into2025",
        }
    )
    points.sort(key=lambda r: r["captured_at"])
    transitions = []
    for a, b in pairwise(points):
        changed = {}
        for pair in pairs:
            fields = {
                key: [a["rules"][pair][key], b["rules"][pair][key]]
                for key in a["rules"][pair]
                if key != "status" and D(a["rules"][pair][key]) != D(b["rules"][pair][key])
            }
            if fields:
                changed[pair] = fields
        transitions.append(
            {
                "before": a["captured_at"],
                "after": b["captured_at"],
                "changes": changed,
                "precise_activation_unknown": True,
            }
        )
    indexes = []
    for name in ("cdx_receipt.json", "cdx_prewindow_receipt.json"):
        receipt = read(HERE / name)
        assert receipt.get("status", receipt.get("http_status")) == 200
        assert sha(receipt["path"]) == receipt["sha256"]
        data = read(receipt["path"])
        assert data[0] == ["timestamp", "original", "statuscode", "mimetype", "digest"]
        assert len(data) - 1 < 1000
        indexes.append(
            {
                "receipt": name,
                "rows": len(data) - 1,
                "resume_key_observed": False,
                "query_limit": 1000,
                "scope": ("Returned CDX prefix index;not proof that no other source exists"),
            }
        )
    screen = read(HERE / "initial_order_screen.json")
    screen_protocol = read(HERE / "initial_order_screen_protocol.json")
    for case in screen_protocol["accounts"]:
        assert sha(case["archive"]) == case["sha256"]
    assert sum(r["narrow_count"] for r in screen["rows"]) == 0
    assert sum(r["broad_initial_count"] for r in screen["rows"]) == 14
    assert sum(r["DCA_fill_only_sensitivity_count"] for r in screen["rows"]) == 2
    doc = read(HERE / "vendor_doc_receipt.json")
    assert doc["status"] == 200 and sha(doc["path"]) == doc["sha256"]
    assert "best-effort" in Path(doc["path"]).read_text()
    result = {
        "audited_at": datetime.now(UTC).isoformat(),
        "passed_source_and_arithmetic_checks": True,
        "original_ordered_pairs": pairs,
        "source_points": points,
        "symbol_point_observations": sum(len(r["rules"]) for r in points),
        "point_to_point_differences": transitions,
        "index_queries": indexes,
        "certified_continuous_rule_intervals": 0,
        "full_window_native_account_admission": False,
        "narrow_initial_order_below_old_limit_count": 0,
        "broad_initial_SOL_sensitivity_count": 14,
        "DCA_fill_only_SOL_sensitivity_count": 2,
        "no_relabel_as_rule_violations": True,
        "old_snapshot_source_hashes": source_hashes,
        "Tardis_API_retried": False,
        "new_historical_strategy_accounts": 0,
        "corrected_returns": None,
        "orders_enabled": False,
        "production_eligible": False,
        "goal_complete": False,
    }
    (HERE / "coverage_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "Verified3real40pair points,120observations;0continuous intervals;"
        "14initial SOL sensitivities"
    )


if __name__ == "__main__":
    main()
