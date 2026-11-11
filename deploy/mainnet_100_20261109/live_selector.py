"""Maintain only the live futures input snapshot; never launch a trading account."""

# ruff: noqa: RUF001 -- Chinese notification copy.

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from deploy.nfi80_selected.launch import ROOT, read


HERE = Path(__file__).resolve().parent
STATE = ROOT / "user_data/deployment/mainnet-100-20261109"
SNAPSHOT = ROOT / "user_data/deployment/nfi80-selected-20261109/current-pairs.json"
POLICY = ROOT / "deploy/nfi80_selected/policy.json"


def verify_cached_source():
    """Verify pinned inputs and approved private config without loading a trader."""
    from deploy.nfi80_selected.launch import verify

    verify()
    config = STATE / "futures/config.json"
    release = read(HERE / "release.json")
    if (
        config.is_symlink()
        or config.stat().st_mode & 0o077
        or hashlib.sha256(config.read_bytes()).hexdigest() != release["config_sha256"]["futures"]
        or read(HERE / "audit-hold.json")["status"] not in {"CLEARED", "RELEASED_BY_USER"}
    ):
        raise ValueError("Existing live selector configuration or source admission changed")


def completed_today(snapshot):
    now = datetime.now(UTC)
    try:
        return (
            snapshot["policy_sha256"] == hashlib.sha256(POLICY.read_bytes()).hexdigest()
            and snapshot["decision_day_utc"] == now.date().isoformat()
            and now < datetime.fromisoformat(snapshot["valid_until_utc"])
            and len(snapshot["pairs"]) == 80
        )
    except (KeyError, TypeError, ValueError):
        return False


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
    if refresh is None:
        verify_cached_source()
    url = context()
    previous = read(SNAPSHOT) if SNAPSHOT.exists() else {}
    if refresh is None:
        # Identical same-day admission to the native input publisher. A valid
        # snapshot needs no exchange/numerical imports or notification replay.
        if completed_today(previous):
            return {
                "updated": False, "pairs": 80,
                "valid_until_utc": previous["valid_until_utc"], "cached_inputs": True,
            }
        from deploy.mainnet_100_20261109.manual import verify
        from deploy.nfi80_selected.selector import main as refresh

        verify()
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
        print(json.dumps(run(), ensure_ascii=False))
    except Exception as exc:
        raise SystemExit("Live selector failed: " + type(exc).__name__) from None
