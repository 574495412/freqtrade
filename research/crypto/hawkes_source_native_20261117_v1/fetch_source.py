"""Retrieve immutable public author source; do not run its account illustration."""

import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://api.github.com/repos/neurotrader888/VolatilityHawkes"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Codex-quant-research"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main():
    if (HERE / "source_receipts.json").exists():
        raise ValueError("Already pinned; preserve existing source")
    repo = fetch(BASE)
    (HERE / "repository.json").write_bytes(repo)
    commit = fetch(BASE + "/commits/main")
    (HERE / "commit.json").write_bytes(commit)
    sha = json.loads(commit)["sha"]
    tree = fetch(BASE + "/git/trees/" + sha + "?recursive=1")
    (HERE / "tree.json").write_bytes(tree)
    files = []
    for name in ["hawkes.py", "LICENSE", "README.md"]:
        url = f"https://raw.githubusercontent.com/neurotrader888/VolatilityHawkes/{sha}/{name}"
        raw = fetch(url)
        path = HERE / "source" / name
        path.write_bytes(raw)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        item = next(x for x in json.loads(tree)["tree"] if x["path"] == name)
        assert blob == item["sha"]
        files.append({"path": str(path), "url": url, "bytes": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest(), "git_blob_sha1": blob})
    receipt = {"retrieved_at": datetime.now(UTC).isoformat(), "repository": BASE,
               "commit": sha, "files": files, "license": "MIT",
               "author_video": "https://www.youtube.com/watch?v=wdsiZBIhAFw",
               "youtube_evidence": "Author description retrieved via web search; no full video or transcript review",
               "source_account_illustration_run": False, "financial_returns_seen": False,
               "first_network_attempt": "Sandbox DNS unavailable before any source bytes; public read retry with network permission"}
    (HERE / "source_receipts.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"commit": sha, "files": len(files), "license": "MIT"}))


if __name__ == "__main__":
    main()
