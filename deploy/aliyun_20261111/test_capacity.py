"""An exited core process is never mistaken for successful warm admission."""
# ruff: noqa: S101

import pytest

from deploy.aliyun_20261111.capacity import failed_jobs


@pytest.mark.parametrize(
    "states,expected",
    [
        ({"bytecode": 0, "spot": None, "futures": None, "selector1": 0}, {}),
        ({"spot": None, "selector1": 75}, {}),
        ({"spot": 0, "selector1": 0}, {"spot": 0}),
        ({"futures": 1}, {"futures": 1}),
        ({"bytecode": 1}, {"bytecode": 1}),
        ({"notification-reserve": 0}, {"notification-reserve": 0}),
        ({"selector1": 1}, {"selector1": 1}),
    ],
)
def test_terminated_core_or_failed_selector_prevents_admission(states, expected):
    assert failed_jobs(states) == expected
