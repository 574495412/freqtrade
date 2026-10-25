"""A paper launcher must fail closed before importing the trading runtime."""

import hashlib
import json
import os
import subprocess
import sys

import pytest

from research.crypto.prepare_candidate_paper import LAUNCHER, prepare


@pytest.mark.parametrize("change", ["real_mode", "environment", "credentials", "tampering"])
def test_paper_launcher_rejects_unsafe_configuration(tmp_path, change):
    config = {"dry_run": True, "exchange": {"key": "", "secret": ""}}
    if change == "real_mode":
        config["dry_run"] = False
    if change == "credentials":
        config["exchange"]["secret"] = "test-secret"
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(config))
    manifest = {"settings_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}
    if change == "tampering":
        config_path.write_text(json.dumps(dict(config, max_open_trades=99)))
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    launcher = tmp_path / "run_paper.py"
    launcher.write_text(LAUNCHER)
    env = {k: v for k, v in os.environ.items() if not k.startswith("FREQTRADE__")}
    if change == "environment":
        env["FREQTRADE__DRY_RUN"] = "false"
    result = subprocess.run(
        [sys.executable, str(launcher)], env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    messages = {
        "real_mode": "requires dry_run=true",
        "environment": "refuses external configuration overrides",
        "credentials": "no trading credentials",
        "tampering": "changed after packaging",
    }
    assert messages[change] in result.stderr
    assert "Traceback" not in result.stderr


def test_failed_economic_screen_cannot_create_paper_package(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "evaluation.json").write_text('{"selected_historical_screen_passed":false}')
    (run / "bias_result.json").write_text('{"native_lookahead_passed":true}')
    output = tmp_path / "paper"
    with pytest.raises(ValueError, match="has not passed"):
        prepare(run, output)
    assert not output.exists()
