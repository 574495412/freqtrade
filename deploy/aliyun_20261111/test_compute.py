"""Scheduling admission and fresh process identity prevent concurrent cold starts."""
# ruff: noqa: S101

import json
from pathlib import Path

import pytest

from deploy.aliyun_20261111 import compute, systemd


@pytest.mark.parametrize("phase,capacity,reason", [
    (74, True, "outside_idle_window"), (75, True, None), (104, True, None),
    (105, True, "outside_idle_window"), (80, False, "memory_or_pressure_admission"),
])
def test_selector_admits_only_an_idle_window_with_resource_headroom(
    tmp_path, monkeypatch, phase, capacity, reason,
):
    monkeypatch.setattr(compute, "ROOT", Path("/opt/freqtrade"))
    monkeypatch.setattr(compute, "LOCK", tmp_path / "compute.lock")
    monkeypatch.setattr(compute.time, "time", lambda: phase)
    monkeypatch.setattr(compute, "selector_capacity", lambda: capacity)
    with compute.selector_slot() as actual:
        assert actual == reason


def test_selector_never_queues_behind_core_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(compute, "ROOT", Path("/opt/freqtrade"))
    monkeypatch.setattr(compute, "LOCK", tmp_path / "compute.lock")
    monkeypatch.setattr(compute.time, "time", lambda: 80)
    monkeypatch.setattr(compute, "selector_capacity", lambda: True)
    with compute.core_slot(), compute.selector_slot() as reason:
        assert reason == "core_analysis_busy"
    with compute.selector_slot() as reason:
        assert reason is None


@pytest.mark.parametrize("changed,expected", [
    ({}, True), ({"memory.max": str(950 * compute.MIB)}, False),
    ({"memory.current": str(950 * compute.MIB)}, False),
    ({"memory.pressure": "full avg10=5.00"}, False),
])
def test_kernel_headroom_excludes_only_inactive_file_cache(tmp_path, changed, expected):
    values = {
        "memory.max": str(900 * compute.MIB), "memory.high": str(832 * compute.MIB),
        "memory.current": str(870 * compute.MIB),
        "memory.stat": f"inactive_file {320 * compute.MIB}",
        "memory.pressure": "full avg10=0.00",
    }
    for key, value in {**values, **changed}.items():
        (tmp_path / key).write_text(value)
    info = tmp_path / "meminfo"
    info.write_text(f"MemAvailable: {800 * 1024} kB\n")
    assert compute.selector_capacity(tmp_path, info) is expected
    info.write_text(f"MemAvailable: {640 * 1024} kB\n")
    assert not compute.selector_capacity(tmp_path, info)


@pytest.mark.parametrize("pid,ticks,age,passes", [
    (10, 42, 1, True), (0, 42, 1, False), (11, 42, 1, False),
    (10, 43, 1, False), (10, 42, 31, False),
])
def test_futures_start_requires_matching_fresh_spot_process(
    tmp_path, monkeypatch, pid, ticks, age, passes,
):
    folder = tmp_path / "user_data/deployment/mainnet-100-20261109/spot"
    folder.mkdir(parents=True)
    (folder / "analysis-ready.private.json").write_text(json.dumps({
        "pid": 10, "process_start_ticks": 42, "time": 100 - age, "pairs": 36,
    }))
    monkeypatch.setattr(compute, "ROOT", tmp_path)
    monkeypatch.setattr(compute.subprocess, "check_output", lambda *a, **k: str(pid))
    monkeypatch.setattr(compute, "process_start_ticks", lambda p: ticks)
    monkeypatch.setattr(compute.time, "time", lambda: 100)
    clock = iter((0, 0, 2))
    monkeypatch.setattr(compute.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(compute.time, "sleep", lambda _: None)
    if passes:
        compute.wait_spot(seconds=1)
    else:
        with pytest.raises(RuntimeError, match="Fresh spot"):
            compute.wait_spot(seconds=1)


def test_persistent_units_stagger_startup_and_bound_daily_selector_runtime():
    assert "compute --wait-spot" in systemd.unit("futures", "module")
    assert "freqtrade-live-spot.service" in systemd.unit("futures", "module")
    assert "RuntimeMaxSec=180" in systemd.unit("selector", "module")
    assert "OnCalendar=*-*-* *:01/5:15 UTC" in systemd.timer_unit()
    assert "OnBootSec=" not in systemd.timer_unit()
