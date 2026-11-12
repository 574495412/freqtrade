"""-B reads a checked cache; changed source must invalidate it before execution."""
# ruff: noqa: S101

import py_compile
import subprocess
import sys

from deploy.aliyun_20261111.bytecode import verify_cache


def test_checked_source_cache_avoids_compilation_and_rejects_changed_source(tmp_path):
    source = tmp_path / "cached.py"
    source.write_text("VALUE = 7\n")
    py_compile.compile(str(source), invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    verify_cache(source)
    script = """
import builtins, importlib.util, sys
def reject(*args, **kwargs):
    raise RuntimeError('source recompilation required')
builtins.compile = reject
spec = importlib.util.spec_from_file_location('cached', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.VALUE)
"""
    valid = subprocess.run([sys.executable, "-B", "-c", script, str(source)],
                           check=False, text=True, capture_output=True)
    assert valid.returncode == 0 and valid.stdout.strip() == "7"
    source.write_text("VALUE = 8\n")
    invalid = subprocess.run([sys.executable, "-B", "-c", script, str(source)],
                             check=False, text=True, capture_output=True)
    assert invalid.returncode != 0 and "source recompilation required" in invalid.stderr


def test_live_engine_import_does_not_load_report_only_scipy_statistics():
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import sys; import freqtrade.freqtradebot; assert 'scipy.stats' not in sys.modules",
    ], check=False, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
