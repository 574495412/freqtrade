"""Maintain only the live futures input snapshot; never launch a trading account."""

# ruff: noqa: RUF001 -- Chinese notification copy.

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from deploy.mainnet_100_20261109.prepare import STATE
from deploy.nfi80_selected.selector import SNAPSHOT, read


def context():
    config = read(STATE / "futures/config.json")
    if config.get("dry_run") is not False or config.get("trading_mode") != "futures":
        raise ValueError("Requires the existing live futures configuration")
    rules = config["pairlists"]
    if (
        len(rules) != 1
        or rules[0]["method"] != "RemotePairList"
        or rules[0]["number_assets"] != 80
        or Path(unquote(urlparse(rules[0]["pairlist_url"]).path)).resolve() != SNAPSHOT.resolve()
    ):
        raise ValueError("Live selector snapshot contract differs")
    url = config["webhook"]["url"]
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 18789
        or not parsed.path.startswith("/events/")
    ):
        raise ValueError("Use the existing local notification relay")
    return url


def notify(url, message):
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            url, json={"msgtype": "text", "text": {"content": message}}, timeout=5
        )
        response.raise_for_status()


def run(refresh=None, send=notify):
    url = context()
    if refresh is None:
        from deploy.nfi80_selected.selector import main as refresh

    previous = read(SNAPSHOT) if SNAPSHOT.exists() else {}
    try:
        # Existing pinned native input workflow, shared rate guard and publisher lock.
        # It creates no paper ledger/worker and loads no live exchange credentials.
        refresh()
        current = read(SNAPSHOT)
        if len(current["pairs"]) != 80 or datetime.fromisoformat(
            current["valid_until_utc"]
        ) <= datetime.now(UTC):
            raise ValueError("Live input snapshot invalid or expired")
    except Exception as exc:
        send(url, "[NFI X8｜永续实盘] 日更选币数据更新失败：" + type(exc).__name__)
        raise
    updated = current["snapshot_id"] != previous.get("snapshot_id")
    if updated:
        entered = len(set(current["pairs"]) - set(previous.get("pairs", [])))
        send(url, f"[NFI X8｜永续实盘] 日更选币更新成功：80币，新增{entered}币。仅影响新开仓。")
    return {"updated": updated, "pairs": 80, "valid_until_utc": current["valid_until_utc"]}


if __name__ == "__main__":
    os.umask(0o077)
    try:
        from deploy.mainnet_100_20261109.manual import verify

        verify()
        print(json.dumps(run(), ensure_ascii=False))
    except Exception as exc:
        raise SystemExit("Live selector failed: " + type(exc).__name__) from None
