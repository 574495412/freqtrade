"""Build checked-source runtime caches while all trading workers remain held."""

import compileall
import importlib.util
import json
import os
import py_compile
import struct
import sys
from pathlib import Path

from deploy.aliyun_20261111.native_probe import require_cgroup
from deploy.aliyun_20261111.systemd import REMOTE_ROOT, require_stopped
from deploy.launch import verify


def verify_cache(source):
    source = Path(source)
    cache = Path(importlib.util.cache_from_source(str(source)))
    header = cache.read_bytes()[:16]
    if (
        header[:4] != importlib.util.MAGIC_NUMBER
        or struct.unpack("<I", header[4:8])[0] != 3
        or header[8:16] != importlib.util.source_hash(source.read_bytes())
        or not cache.stat().st_mode & 0o004
    ):
        raise ValueError("Expected readable checked-source bytecode")


def main():
    if Path.cwd().resolve() != REMOTE_ROOT or os.geteuid() != 0 or sys.version_info[:2] != (3, 12):
        raise ValueError("Use the held ECS checkout and pinned Python 3.12 as root")
    require_stopped()
    require_cgroup()
    verify()
    os.umask(0o022)
    for folder in ("freqtrade", "deploy", ".venv-paper/lib/python3.12/site-packages/ccxt"):
        if not compileall.compile_dir(
            folder, force=True, quiet=1, workers=1,
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        ):
            raise ValueError("Runtime byte compilation failed")
    for source in (
        "deploy/strategy/nfi-x8/NostalgiaForInfinityX8.py", "freqtrade/freqtradebot.py",
        ".venv-paper/lib/python3.12/site-packages/ccxt/binance.py",
    ):
        verify_cache(source)
    print(json.dumps({"checked_source_caches": True, "workers_started": 0}))


if __name__ == "__main__":
    main()
