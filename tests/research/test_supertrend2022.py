"""Compare the array adapter with the pinned legacy formula under pandas copy-on-write."""

import ast
import hashlib
import json
import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import talib.abstract as ta


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_legacy_supertrend_numerical_and_direction_parity(seed, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "research/crypto/strategies"))
    from supertrend2022 import Supertrend2022

    source = (
        root / "user_data/crypto_quant/external_sources_20261022/supertrend_pre2024/Supertrend.py"
    )
    provenance = json.loads((root / "research/crypto/supertrend2022_source.json").read_text())
    assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance["sha256"]
    raw = source.read_text()
    cls = next(n for n in ast.parse(raw).body if isinstance(n, ast.ClassDef))
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "supertrend")
    # Restore the legacy mutation semantics with DataFrame-level iat. The original
    # chained Series assignment cannot update its parent under pandas copy-on-write.
    reference = textwrap.dedent(ast.get_source_segment(raw, method))
    reference = re.sub(
        r"df\[(.*?)\]\.iat\[(.*?)\]",
        lambda match: f"df.iat[{match[2]}, df.columns.get_loc({match[1]})]",
        reference,
    ).replace("np.NaN", '"nan"')
    scope = {"np": np, "ta": ta, "DataFrame": pd.DataFrame}
    exec(compile(reference, str(source), "exec"), scope)  # noqa: S102 - SHA-pinned reference formula
    rng = np.random.default_rng(seed)
    close = np.exp(4 + np.cumsum(rng.normal((seed - 1.5) / 1000, 0.02, 400)))
    frame = pd.DataFrame(
        {
            "open": close * 0.998,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": rng.uniform(1, 1000, len(close)),
        }
    )
    for multiplier, period in [(4, 8), (7, 9), (1, 8), (1, 16), (3, 18), (6, 18)]:
        expected = scope["supertrend"](None, frame, multiplier, period)
        actual = Supertrend2022.supertrend(None, frame, multiplier, period)
        pd.testing.assert_frame_equal(expected, actual, check_exact=True)
