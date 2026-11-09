"""Publish a shared daily snapshot selected by the unmodified native pairlist CLI."""

import fcntl
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATE = ROOT / "user_data/deployment/nfi80-daily-20261109"
SNAPSHOT = STATE / "current-pairs.json"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".writing")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def main():
    from deploy.nfi80_flatboost.launch import verify

    verify()
    policy = read(HERE / "policy.json")
    if (
        sha(__file__) != policy["selector_sha256"]
        or sha(HERE / "selector-config.json") != policy["selector_config_sha256"]
    ):
        raise ValueError("Frozen selector source or native configuration changed")
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(STATE / "selector.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"skipped": "another native selector is running"}))
        return
    now = datetime.now(UTC)
    if SNAPSHOT.exists():
        current = read(SNAPSHOT)
        due = datetime.fromisoformat(current["valid_until_utc"]) - timedelta(seconds=900)
        if now < due:
            print(
                json.dumps(
                    {
                        "skipped": "daily snapshot remains fresh",
                        "snapshot_id": current["snapshot_id"],
                    }
                )
            )
            return
    attempt = STATE / "selector" / now.strftime("%Y%m%dT%H%M%SZ")
    attempt.mkdir(parents=True)
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    command = [
        str(ROOT / ".venv-paper/bin/python"),
        "-B",
        "-m",
        "freqtrade",
        "test-pairlist",
        "--config",
        str(HERE / "selector-config.json"),
        "--userdir",
        str(attempt),
        "--print-json",
    ]
    with (attempt / "stdout.json").open("x") as out, (attempt / "stderr.log").open("x") as err:
        child = subprocess.run(
            command, cwd=ROOT, env=env, stdout=out, stderr=err, timeout=900, check=False
        )
    save(attempt / "completion.json", {"returncode": child.returncode, "command": command})
    if child.returncode:
        raise RuntimeError("Native test-pairlist failed; prior snapshot retained until expiry")
    pairs = read(attempt / "stdout.json")
    if not isinstance(pairs, list) or len(pairs) != 80 or len(set(pairs)) != 80:
        raise ValueError("Native selection did not return exactly80 distinct eligible pairs")
    if not set(pairs) <= set(policy["author_allowed_pairs"]):
        raise ValueError("Native selector escaped the frozen author universe")
    generated = datetime.now(UTC)
    snapshot = {
        "pairs": pairs,
        "refresh_period": 60,
        "snapshot_id": generated.strftime("%Y%m%dT%H%M%SZ"),
        "generated_at_utc": generated.isoformat(),
        "valid_until_utc": (generated + timedelta(days=1)).isoformat(),
        "native_cli_returncode": 0,
        "native_config_sha256": sha(HERE / "selector-config.json"),
        "policy_sha256": sha(HERE / "policy.json"),
        "native_attempt": str(attempt),
        "volume_basis": "Latest rolling24h quoteVolume via native VolumePairList",
    }
    save(attempt / "selected-snapshot.json", snapshot)
    save(SNAPSHOT, snapshot)
    save(
        HERE / "latest-selector-receipt.json",
        {
            "snapshot_id": snapshot["snapshot_id"],
            "generated_at_utc": snapshot["generated_at_utc"],
            "valid_until_utc": snapshot["valid_until_utc"],
            "pairs": pairs,
            "snapshot_sha256": sha(SNAPSHOT),
            "native_attempt": str(attempt),
            "native_cli_returncode": 0,
            "orders_enabled": False,
        },
    )
    print(
        json.dumps({"published": True, "pairs": len(pairs), "snapshot_id": snapshot["snapshot_id"]})
    )


if __name__ == "__main__":
    main()
