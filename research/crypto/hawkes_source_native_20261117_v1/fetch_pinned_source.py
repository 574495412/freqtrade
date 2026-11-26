"""Fallback public raw-source read after the unauthenticated API rate limit."""
import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMIT = "51c8557eb078f1be368e1ff82c53c9118685e7de"
EXPECTED = {
    "hawkes.py": "004d1a46777fa7d70a3fa97484fdc9329fd369fc61660a913bb1980991e47cdf",
    "LICENSE": "d0eea335221a9ce020333f13fc56056a940a309d0f54f058ca5bdf83aad955c0",
    "README.md": "1b8eefe1d2fb800ecd4170b37aa52a8573c955a2b0260a1c7f73aed767d0cfd4",
}

if __name__ == "__main__":
    assert not (HERE / "source_receipts.json").exists()
    files = []
    for name, expected in EXPECTED.items():
        url = f"https://raw.githubusercontent.com/neurotrader888/VolatilityHawkes/{COMMIT}/{name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            raw = response.read()
        assert hashlib.sha256(raw).hexdigest() == expected
        target = HERE / "source" / name
        target.write_bytes(raw)
        files.append({"path": str(target), "url": url, "bytes": len(raw), "sha256": expected})
    receipt = {"retrieved_at": datetime.now(UTC).isoformat(), "commit": COMMIT,
               "files": files, "license": "MIT", "financial_returns_seen": False,
               "source_account_illustration_run": False,
               "tree_api_verified": False, "failures_preserved": [
                   "Initial sandbox DNS unavailable; no source bytes returned",
                   "fetch_source.py public API read returned403rate limit; original script retained"],
               "author_video": "https://www.youtube.com/watch?v=wdsiZBIhAFw",
               "youtube_evidence": "Author description obtained; no full video or transcript review"}
    (HERE / "source_receipts.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"commit": COMMIT, "verified_raw_files": len(files)}))
