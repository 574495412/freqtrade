"""Transparent APFS compression of inactive completed native cache bytes.

No pickle is deserialized, no data path is removed, and no account is replayed.
Per-file intent and completion receipts preserve original byte/metadata evidence.
"""

import ctypes
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = ROOT / "user_data/crypto_quant/nfi_original_20261027_v001"
OWNERS = ["A_original_observed_v003", "B_cost8_v001", "C_cost15_v001",
          "D_leverage1_v001", "E_grosscap_v001", "F_stop8_v001", "G_halt15_v001"]
TARGET_FREE = 12 * 1024**3
MINIMUM_FREE = 1024**3


class Attrlist(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_uint16), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


LIB = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
LIB.getattrlist.argtypes = [ctypes.c_char_p, ctypes.POINTER(Attrlist), ctypes.c_void_p,
                           ctypes.c_size_t, ctypes.c_ulong]
LIB.setattrlist.argtypes = LIB.getattrlist.argtypes
ATTRS = Attrlist(5, 0, 0x200, 0, 0, 0, 0)  # SDK ATTR_CMN_CRTIME


def birth(path):
    buf = ctypes.create_string_buffer(32)
    if LIB.getattrlist(os.fsencode(path), ctypes.byref(ATTRS), buf, len(buf), 0):
        raise OSError(ctypes.get_errno(), str(path))
    length, seconds, nanos = struct.unpack_from("=Iqq", buf.raw)
    assert length == 20 and 0 <= nanos < 1000000000
    assert abs(seconds + nanos / 1e9 - path.stat().st_birthtime) < 2e-6
    return seconds, nanos


def restore_birth(path, value):
    buf = ctypes.create_string_buffer(struct.pack("=qq", *value))
    if LIB.setattrlist(os.fsencode(path), ctypes.byref(ATTRS), buf, 16, 0):
        raise OSError(ctypes.get_errno(), str(path))
    assert birth(path) == value


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def acl(path):
    result = subprocess.run(["/bin/ls", "-le", str(path)], capture_output=True,
                            text=True, check=True)
    return result.stdout.splitlines()[1:]


def xattrs(path):
    names = subprocess.run(["/usr/bin/xattr", str(path)], capture_output=True,
                           text=True, check=True).stdout.splitlines()
    return {name: hashlib.sha256(subprocess.run(["/usr/bin/xattr", "-px", name, str(path)],
                capture_output=True, check=True).stdout).hexdigest() for name in names}


def metadata(path):
    value = path.stat()
    return {"mode": value.st_mode, "uid": value.st_uid, "gid": value.st_gid,
            "size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "birthtime": birth(path), "acl": acl(path), "xattrs": xattrs(path)}


def journal(stream, value):
    stream.write(json.dumps(value) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def main():
    assert not (HERE / "compression_journal.jsonl").exists(), "Preserve original attempt"
    before_free = shutil.disk_usage(HERE).free
    completed = []
    with (HERE / "compression_journal.jsonl").open("x") as stream:
        for name in OWNERS:
            owner = OLD / name
            completion = owner / "completion.json"
            if not completion.exists() or not json.loads(completion.read_text()).get("native_run_completed"):
                continue
            for path in sorted((owner / "lossless_cache").rglob("*.pkl")):
                if shutil.disk_usage(HERE).free >= TARGET_FREE:
                    break
                value = path.lstat()
                if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                        or value.st_flags != 0 or value.st_size < 1024 * 1024):
                    continue
                assert path.resolve().is_relative_to(owner / "lossless_cache")
                assert shutil.disk_usage(HERE).free > MINIMUM_FREE + value.st_size
                info, digest = metadata(path), sha(path)
                temporary = path.with_name(path.name + ".codex-hfs.tmp")
                assert not temporary.exists()
                journal(stream, {"stage": "intent", "path": str(path), "sha256": digest,
                                 "metadata": info, "completion_sha256": sha(completion),
                                 "original_inode": value.st_ino, "original_blocks": value.st_blocks})
                result = subprocess.run(["/usr/bin/ditto", "--hfsCompression", "--noclone",
                    str(path), str(temporary)], capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                restore_birth(temporary, tuple(info["birthtime"]))
                assert metadata(temporary) == info and sha(temporary) == digest
                # Refuse races or a newly shared cache before the atomic replacement.
                current = path.stat()
                assert (current.st_ino, current.st_nlink, current.st_mtime_ns) == (
                    value.st_ino, 1, value.st_mtime_ns)
                assert sha(path) == digest and metadata(path) == info
                os.replace(temporary, path)
                assert sha(path) == digest and metadata(path) == info
                after = path.stat()
                receipt = {"stage": "completed", "path": str(path), "sha256": digest,
                           "metadata_preserved": True, "before_allocated": value.st_blocks * 512,
                           "after_allocated": after.st_blocks * 512,
                           "compressed_flag": bool(after.st_flags & 32),
                           "free_disk_bytes": shutil.disk_usage(HERE).free}
                journal(stream, receipt)
                completed.append(receipt)
                if len(completed) % 50 == 0:
                    print(json.dumps({"files_verified": len(completed),
                                      "free_disk_bytes": receipt["free_disk_bytes"]}), flush=True)
            if shutil.disk_usage(HERE).free >= TARGET_FREE:
                break
    # Recheck every final path. Journal is the audit trail if this run is interrupted.
    assert all(sha(Path(row["path"])) == row["sha256"] for row in completed)
    result = {"completed_at": datetime.now(UTC).isoformat(), "passed": True,
              "files_compressed_and_rechecked": len(completed), "free_before": before_free,
              "free_after": shutil.disk_usage(HERE).free,
              "reported_block_reduction": sum(r["before_allocated"] - r["after_allocated"] for r in completed),
              "all_paths_and_logical_bytes_preserved": True, "hardlinks_skipped": True,
              "ctime_and_inode_changed_as_storage_operation": True,
              "birthtime_mtime_ACL_xattrs_owner_mode_preserved": True,
              "pickle_loaded": False, "financial_accounts_started": 0,
              "historical_strategy_returns_changed": False, "orders_enabled": False}
    (HERE / "compression_complete.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
