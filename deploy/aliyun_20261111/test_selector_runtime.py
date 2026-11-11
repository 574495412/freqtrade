"""Relocation must retain original input hashes and selection rules."""
# ruff: noqa: S101

import copy
import hashlib
import json

import pytest

from deploy.nfi80_selected import selector


def test_explicit_source_relocation_checks_original_bytes(tmp_path, monkeypatch):
    path = tmp_path / "user_data/seed.json"
    path.parent.mkdir()
    path.write_bytes(b"frozen-input")
    monkeypatch.setattr(selector, "ROOT", tmp_path)
    monkeypatch.setattr(selector, "runtime_context", lambda: {"source_root": "/original"})
    record = {
        "path": "/original/user_data/seed.json",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    assert selector.checked(record) == path
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Pinned"):
        selector.checked(record)
    with pytest.raises(ValueError):
        selector.checked({**record, "path": "/other/seed.json"})


@pytest.mark.parametrize(
    "value", [{"weights": [1]}, {"source_root": "relative"}, {"https_proxy": "file:///unsafe"}]
)
def test_runtime_rejects_financial_overrides_and_invalid_routes(tmp_path, monkeypatch, value):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(selector, "RUNTIME", path)
    with pytest.raises(ValueError):
        selector.runtime_context()


def test_no_runtime_keeps_existing_transport_and_direct_override_is_narrow(tmp_path, monkeypatch):
    monkeypatch.setattr(selector, "RUNTIME", tmp_path / "absent.json")
    assert selector.runtime_context() == {}
    config = {
        "max_open_trades": 12,
        "exchange": {
            "ccxt_config": {"httpsProxy": "http://127.0.0.1:7897", "options": {"x": 1}},
            "ccxt_async_config": {"httpsProxy": "http://127.0.0.1:7897"},
        },
    }
    assert selector.runtime_transport(copy.deepcopy(config), {}) == config
    result = selector.runtime_transport(copy.deepcopy(config), {"https_proxy": None})
    assert result["max_open_trades"] == 12
    assert result["exchange"]["ccxt_config"] == {"options": {"x": 1}}
    assert result["exchange"]["ccxt_async_config"] == {}
