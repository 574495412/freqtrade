"""Audit diagnostics must remain actionable without enabling trades or leaking URLs."""

# ruff: noqa: S101 -- Pytest assertions.

import pytest

from deploy.mainnet_100_20261109 import checks, manual


@pytest.mark.parametrize("status", ["CLEARED", "RELEASED_BY_USER"])
def test_research_gate_accepts_audit_clearance_or_explicit_user_release(status):
    manual.enforce_research_hold({"status": status, "reasons": ["Unresolved research finding"]})


@pytest.mark.parametrize("status", ["BLOCKED_RESEARCH_AUDIT", "UNKNOWN", ""])
def test_research_gate_still_rejects_other_states(status):
    with pytest.raises(manual.ResearchAuditHold):
        manual.enforce_research_hold({"status": status, "reasons": []})


def test_audit_hold_stops_before_account_checks_worker_or_subprocess(monkeypatch):
    def held(**kwargs):
        raise manual.ResearchAuditHold([
            "Retrospective author blacklist",
            "Official spot and futures exit-time lookahead warnings unresolved",
            "No same-contract100USDT spot/futures economic reproduction",
        ])

    monkeypatch.setattr(manual, "verify", held)
    monkeypatch.setattr(manual.sys, "argv", ["manual"])
    monkeypatch.setattr(checks, "account_checks", lambda: pytest.fail("Account read reached"))
    monkeypatch.setattr(manual, "worker", lambda _: pytest.fail("Worker reached"))
    monkeypatch.setattr(manual.subprocess, "Popen", lambda *a, **k: pytest.fail("Process spawned"))
    with pytest.raises(SystemExit) as stopped:
        manual.cli()
    message = str(stopped.value)
    assert "ResearchAuditHold" in message
    assert "blacklist" in message
    assert "lookahead" in message
    assert "100-USDT" in message
    assert "--check-only" in message


def test_unrecognized_reasons_and_other_exceptions_do_not_expose_secrets(monkeypatch):
    private = "https://example.invalid?signature=private"
    assert private not in str(manual.ResearchAuditHold([private]))

    def failed():
        raise ValueError(private)

    monkeypatch.setattr(manual, "main", failed)
    with pytest.raises(SystemExit) as stopped:
        manual.cli()
    assert str(stopped.value) == "Manual command stopped: ValueError"
