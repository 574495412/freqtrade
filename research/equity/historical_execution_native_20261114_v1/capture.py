"""Capture primary rules/source bytes; retain explicit failed requests."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def main():
    sources = HERE / "sources"
    sources.mkdir(exist_ok=True)
    urls = {
        "hkex_overview.html": "https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads?sc_lang=en",
        "hkex_phase1_table.pdf": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/CT02725E.pdf",
        "hkex_phase2_faq.pdf": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQP2_E.pdf",
        "hkex_phase1_launch.pdf": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/CT10925E.pdf",
        "hkex_phase2_launch.pdf": "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/CT10426E.pdf",
        "PayoutYield.py": "https://raw.githubusercontent.com/OpenSourceAP/CrossSection/9f053398c4995651d822e90d81526d0644c6f3e5/Signals/pyCode/Predictors/PayoutYield.py",
        "payout_issue.json": "https://api.github.com/repos/OpenSourceAP/CrossSection/issues/207",
        "payout_comments.json": "https://api.github.com/repos/OpenSourceAP/CrossSection/issues/207/comments",
        "quantconnect_value_youtube.html": "https://www.youtube.com/watch?v=UlFQNUtOACw",
    }
    receipts = []
    for name, url in urls.items():
        assert not (sources / name).exists(), "Never overwrite original capture"
        row = dict(url=url, captured_utc=datetime.now(UTC).isoformat())
        try:
            result = requests.get(url, timeout=45)
            path = sources / name
            path.write_bytes(result.content)
            row.update(
                status=result.status_code,
                path=str(path),
                bytes=len(result.content),
                sha256=hashlib.sha256(result.content).hexdigest(),
                content_type=result.headers.get("Content-Type"),
            )
        except requests.RequestException as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)
        receipts.append(row)
        (HERE / "source_receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    print(
        json.dumps(
            [{k: r[k] for k in ("url", "status", "bytes", "failure") if k in r} for r in receipts],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
