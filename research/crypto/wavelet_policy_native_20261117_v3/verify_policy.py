"""Independent audit of source ownership, native masks and callback receipts.

Does not import the strategy/projector or execute a predictor or trading account.
"""

# ruff: noqa: S101
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FEATURE = HERE.with_name("wavelet_feature_native_20261117_v2")
ORIGINAL = HERE.with_name("wavelet_source_native_20261117_v1")
OUTPUT = ROOT / "user_data/crypto_quant" / HERE.name
if not __debug__:
    raise RuntimeError("Evidence audit requires assertions")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_class(path, name):
    return next(
        n
        for n in ast.parse(path.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == name
    )


def expression(cls, name):
    candidates = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            candidates.append(node.value)
    return candidates[-1]


def merged_dictionary(cls, name, inherited):
    node = expression(cls, name)
    assert isinstance(node, ast.Dict)
    result = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            assert ast.unparse(value) == "BaseStrategy." + name
            result.update(inherited)
        else:
            result[ast.literal_eval(key)] = ast.literal_eval(value)
    return result


def independent_parameters():
    base = source_class(FEATURE / "sources/Framework/BaseStrategy.py", "BaseStrategy")
    parent = source_class(
        ORIGINAL / "sources/nateemma_strategies/TSPredict/TSPredict.py", "TSPredict"
    )
    defaults = {}
    for cls in [base, parent]:
        for node in cls.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id
                in ["DecimalParameter", "IntParameter", "CategoricalParameter"]
            ):
                default = next(k.value for k in node.value.keywords if k.arg == "default")
                defaults[node.targets[0].id] = ast.literal_eval(default)
    for name in ["buy_params", "sell_params"]:
        base_values = merged_dictionary(base, name, {})
        defaults.update(merged_dictionary(parent, name, base_values))
    # Only declared parameters belong in the native hyper-parameter enumeration.
    actual = read(OUTPUT / "attempt2/effective_parameters.json")
    declared = set(actual["resolved_hyper_parameters"])
    assert {k: defaults[k] for k in declared} == actual["resolved_hyper_parameters"]
    assert actual["source_dictionaries_loaded_exactly"] and not actual["parameter_file_values"]
    for name in ["stoploss", "trailing_stop", "exit_profit_only", "ignore_roi_if_entry_signal"]:
        assert ast.literal_eval(expression(base, name)) == actual[name]
    assert actual["minimal_roi"] == {"0": 0.03} and not actual["position_adjustment_enable"]
    assert (
        actual["forecaster_class"] == "sgd_forecaster"
        and actual["forecaster_display_name"] == "SGD"
    )
    child = source_class(
        ORIGINAL / "sources/nateemma_strategies/TSPredict/TS_Wavelet.py", "TS_Wavelet"
    )
    config = expression(child, "strategy_config")
    aggregate = next(k.value for k in config.keywords if k.arg == "aggregate_pairs")
    assert ast.literal_eval(aggregate) is False and actual["aggregate_pairs"] is False
    return len(declared)


def verify_projection():
    rows = read(OUTPUT / "attempt2/source_projection.json")
    modified = []
    for row in rows:
        path = Path(row["path"])
        assert sha(path) == row["sha256"]
        tree = ast.parse(path.read_text())
        selected = [
            n
            for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef))
            and n.name in row["selected_definitions"]
        ]
        for correction in row["modifications"]:
            assert correction["replacement"] == "use_mlx = False"
            assert path.name == "TS_Wavelet.py"
            cls = next(
                c for c in selected if isinstance(c, ast.ClassDef) and c.name == "TS_Wavelet"
            )
            stmt = next(
                n for n in cls.body if isinstance(n, ast.Assign) and n.lineno == correction["line"]
            )
            assert ast.unparse(stmt) == correction["original"]
            stmt.value = ast.Constant(False)
            modified.append(correction)
        future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
        compiled = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
        assert (
            hashlib.sha256(ast.dump(compiled).encode()).hexdigest()
            == row["compiled_definition_ast_sha256"]
        )
    assert len(modified) == 1
    return len(rows)


def verify_tables():
    admitted = read(OUTPUT / "attempt2/admission.json")
    assert admitted["passed"] and not admitted["financial_start_called"]
    tables = {}
    rows_checked = signals = 0
    for row in admitted["cases"]:
        assert sha(row["path"]) == row["sha256"]
        frame = pd.read_parquet(row["path"])
        gain = (
            (100 * (frame.close - frame.close.shift(6)) / frame.close.shift(6)).fillna(0).round(4)
        )
        np.testing.assert_array_equal(frame.gain, gain)
        profits, losses = gain.clip(lower=0), gain.clip(upper=0)
        up = (
            (profits.rolling(40).mean() + 1.9 * profits.rolling(40).std()).clip(lower=0.5).fillna(0)
        )
        down = (
            (losses.rolling(40).mean() - 1.8 * abs(losses.rolling(40).std()))
            .clip(upper=-0.5)
            .fillna(0)
        )
        np.testing.assert_array_equal(up, frame.target_profit)
        np.testing.assert_array_equal(down, frame.target_loss)
        buy = (
            (frame.predicted_gain > up)
            & (frame.volume > 0)
            & (frame.rvol > -1)
            & (frame.atr_pct_roll > -1)
            & (frame.guard_metric < 0)
            & (frame.close_norm < 1.1)
            & (frame.adx > -1)
            & (frame.bb_width > -1)
        )
        sell = (frame.predicted_gain < down) & (frame.rvol > 2) & (frame.guard_metric > 0)
        np.testing.assert_array_equal(buy, frame.enter_long.fillna(0).astype(bool))
        np.testing.assert_array_equal(sell, frame.exit_long.fillna(0).astype(bool))
        assert row["entry_signals"] == int(buy.sum()) and row["exit_signals"] == int(sell.sum())
        assert not frame.predicted_gain.iloc[:1000].any()
        assert frame.predicted_gain.between(-3, 3).all()
        assert "rsi" not in frame
        tables[row["pair"], row["case"]] = frame
        rows_checked += len(frame)
        signals += int(buy.sum() + sell.sum())
    for asset in ["BTC", "ETH"]:
        full = tables[asset + "/USDT", "full2000"]
        reference = pd.read_parquet(FEATURE / "numerical_outputs" / (asset + "_full6000.parquet"))
        np.testing.assert_array_equal(
            full.predicted_gain, reference.source_predicted_gain.iloc[:2000]
        )
        for case in ["prefix1800", "future_shock"]:
            pd.testing.assert_frame_equal(
                full.iloc[:1800], tables[asset + "/USDT", case].iloc[:1800]
            )
    return rows_checked, signals


def verify_callbacks():
    stop = read(OUTPUT / "attempt2/stop_contract.json")
    assert stop["native_after_fill_stop"] == 100 * (1 - 0.17)
    assert stop["stop_during_grace_at_price110"] == 83
    assert stop["native_stop_after2_6hours"] == 100 * (1 - 0.05)
    assert stop["stop_at_price120_does_not_trail"] == 95
    actual = read(OUTPUT / "callback_probe/admission.json")
    assert actual["passed"] and actual["new_financial_wallets"] == 0
    checked = 0
    for row in actual["cases"]:
        assert sha(row["real_indicator_input"]) == row["input_sha256"]
        frame = pd.read_parquet(row["real_indicator_input"])
        quote = float(frame.volume.iloc[-1] * frame.close.iloc[-1])
        assert quote == row["quote_volume"] and abs(quote / 10 - row["stake_cap"]) < 1e-7
        guard = frame.guard_metric.iloc[-1]
        for case in row["custom_exit_cases"]:
            profit, age = case["net_profit_argument"], case["age_hours"]
            reason = None
            if profit > 0 and guard > 0.98:
                reason = "metric_overbought"
            elif profit > 0.022:
                reason = "take_profit"
            elif age >= 12 and profit > 0.005:
                reason = "unclog_12h"
            elif age >= 24 and profit >= 0:
                reason = "unclog_1d"
            elif age >= 21 * 24:
                reason = "max_hold"
            assert reason == case["reason"] == case["independent_reason"]
            checked += 1
    return checked


def main():
    for name in ["interface_freeze.json", "callback_freeze.json"]:
        for path, digest in read(HERE / name)["files"].items():
            assert sha(path) == digest, path
    parents = []
    for path, digest in read(HERE / "protocol.json")["inputs"].items():
        assert sha(path) == digest
        rows = read(path)["files"]
        for row in rows:
            assert sha(row["path"]) == row["sha256"]
        parents.append({"path": path, "sha256": digest, "files_verified": len(rows)})
    for row in read(HERE / "source_receipts.json"):
        raw = Path(row["local_path"]).read_bytes()
        assert sha(row["local_path"]) == row["sha256"]
        assert (
            hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
            ).hexdigest()
            == row["git_blob"]
        )
    parameter_count = independent_parameters()
    projections = verify_projection()
    rows, signals = verify_tables()
    callback_count = verify_callbacks()
    first = read(OUTPUT / "attempt1/failure.json")
    assert first["stage"] == "native_parameters" and not first["financial_start_called"]
    result = {
        "passed": True,
        "native_parameter_values_checked": parameter_count,
        "source_definition_projections_checked": projections,
        "native_indicator_and_signal_rows_checked": rows,
        "diagnostic_signal_occurrences_not_trades": signals,
        "all_columns_exact_under_prefix_and_future_shock": True,
        "native_stop_and_discretionary_exit_confirmation_contract_passed": True,
        "custom_exit_grid_cases_checked": callback_count,
        "native_sizing_and_entry_volume_headroom_calls_passed": True,
        "prior_studies_verified": parents,
        "first_probe_label_error_preserved": True,
        "financial_wallets_completed": 0,
        "predictive_power_validated": False,
        "profitability_or_higher_return_proven": False,
        "goal_complete": False,
    }
    (HERE / "independent_verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
