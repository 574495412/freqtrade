"""Retrieve only the pinned author source and preserve public API failures."""

import hashlib
import json
import shutil
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


STUDY = Path(__file__).parent
COMMIT = "da99c20bf3d977b639451258cd6cfca9baa1dcc3"
EXPECTED = {
    "head_shoulders.py": "8dbcde6cc51b7dc7e8cd56d0491fc28cada901e98894cb74ad27d4b835f9a0bd",
    "rolling_window.py": "58ea065d44bf1136fb6d31791e8398f4c4a4417b8d03120aeeea5cfdfcf8d480",
    "LICENSE": "d0eea335221a9ce020333f13fc56056a940a309d0f54f058ca5bdf83aad955c0",
    "README.md": "1be286b44aa03b7b770bac44afaa022c70aeacb6bd4090e934d48e74535d7f1f",
}


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "pinned-quant-source-audit"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read()


def main():
    if shutil.disk_usage(STUDY).free < 1024**3:
        raise RuntimeError("Free disk is below the frozen 1 GiB guard")
    source = STUDY / "source"
    source.mkdir(exist_ok=True)
    files = []
    for name, expected in EXPECTED.items():
        url = (
            "https://raw.githubusercontent.com/neurotrader888/"
            f"TechnicalAnalysisAutomation/{COMMIT}/{name}"
        )
        raw = get(url)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Pinned author source differs: {name}")
        with (source / name).open("xb") as output:
            output.write(raw)
        files.append({"path": str(source / name), "url": url, "bytes": len(raw), "sha256": actual})
    api = "https://api.github.com/repos/neurotrader888/TechnicalAnalysisAutomation"
    tree = {"verified": False}
    try:
        commit = json.loads(get(f"{api}/git/commits/{COMMIT}"))
        root_tree = commit["tree"]["sha"]
        response = json.loads(get(f"{api}/git/trees/{root_tree}?recursive=1"))
        tree = {"verified": not response["truncated"], "tree_sha": root_tree, "response": response}
    except urllib.error.HTTPError as error:
        tree = {
            "verified": False,
            "status": error.code,
            "url": error.url,
            "body": error.read().decode("utf-8", errors="replace"),
        }
    except urllib.error.URLError as error:
        tree = {"verified": False, "transport_failure": str(error)}
    with (STUDY / "tree_api_receipt.json").open("x") as output:
        json.dump(tree, output, indent=2)
        output.write("\n")
    receipt = {
        "retrieved_at": datetime.now(UTC).isoformat(), "commit": COMMIT,
        "repository": "https://github.com/neurotrader888/TechnicalAnalysisAutomation",
        "license": "MIT", "files": files, "tree_api_verified": tree["verified"],
        "source_original_bytes_preserved": True,
        "source_strategy_or_pattern_diagnostic_run": False,
        "author_account_performance_reproduced": False,
    }
    with (STUDY / "source_receipts.json").open("x") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
