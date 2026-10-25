"""Validate bounded, nontruncated official public funding-history receipts."""

import json

import numpy as np


def funding_receipt(receipt, symbols):  # noqa: C901
    if receipt.get("schema") != "crypto-quant-binance-funding-receipt.v1":
        raise ValueError("Expected explicit public funding query receipt")
    output = {}
    for query in receipt["queries"]:
        symbol = query["symbol"]
        if symbol in output or symbol not in symbols:
            raise ValueError("Funding symbol set duplicated or changed")
        if query["tool"] != "mcp__codex_apps__binance_get_futures_usds_funding_rate_history":
            raise ValueError("Unexpected funding provider")
        if any(
            type(query[name]) is not int
            for name in ["startTime", "endTime", "limit", "response_received_ms"]
        ):
            raise ValueError("Exact integer query clocks and page limit required")
        start, end, limit = query["startTime"], query["endTime"], query["limit"]
        raw = query["raw_result"]
        if raw.get("isError") or query["response_received_ms"] < end or not 0 <= start <= end:
            raise ValueError("Funding response failed or precedes requested coverage")
        if not (1 <= limit <= 1000):
            raise ValueError("Unexpected public history page limit")
        body = raw.get("structuredContent")
        if body is None:
            texts = [item["text"] for item in raw.get("content", []) if item.get("type") == "text"]
            if len(texts) != 1:
                raise ValueError("Unparseable funding response")
            body = json.loads(texts[0])
        rows = body.get("result") if isinstance(body, dict) else body
        if not isinstance(rows, list) or len(rows) >= limit:
            raise ValueError("Unknown or possibly truncated funding coverage")
        events = []
        for row in rows:
            if row["symbol"] != symbol or row.get("rateType", "Regular") != "Regular":
                raise ValueError("Unexpected funding symbol or event type")
            if type(row["fundingTime"]) is not int:
                raise ValueError("Exact original millisecond funding clock required")
            time, rate = int(row["fundingTime"]), float(row["fundingRate"])
            if not np.isfinite(rate) or not start <= time <= end:
                raise ValueError("Invalid or out-of-coverage funding event")
            if events and time <= events[-1]["time_ms"]:
                raise ValueError("Duplicate or unordered funding events")
            events.append({"time_ms": time, "rate": rate})
        output[symbol] = {
            "events": events,
            "coverage": {"complete": True, "start_ms": start, "end_ms": end},
        }
    if set(output) != set(symbols):
        raise ValueError("Missing explicit funding query for a registered symbol")
    return output
