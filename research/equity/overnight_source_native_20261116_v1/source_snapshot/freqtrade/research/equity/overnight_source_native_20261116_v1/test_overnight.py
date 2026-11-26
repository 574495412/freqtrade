"""Meaningful source arithmetic, strict admission and causal-clock boundaries."""

import ast
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from bootstrap import HERE, boot, save


boot()
from freqtrade.data.equity_overnight import quote_overnight126
from freqtrade.data.history.equity import EquityAdmissionError


def panel(n=150):
    dates = pd.date_range("2024-01-01", periods=n, freq="B", tz="UTC")
    return {
        k: pd.DataFrame({"A/USD": [v] * n}, index=dates)
        for k, v in [("open", 101.0), ("close", 100.0), ("volume", 200_000.0)]
    }


def source_functions(filename, names, values):
    tree = ast.parse((HERE / "sources" / filename).read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names)
    # Only already-read pure functions; no source module imports/top-level IO.
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), values)
    return values


def test_complete_source_arithmetic_and_minimum_history():
    p = panel()
    result = quote_overnight126(p)
    assert result.iloc[:130].isna().all().all()
    assert result.iloc[130, 0] == pytest.approx(1.01**126 - 1, abs=1e-13)
    source = source_functions(
        "final_signal.py", ["compute_momentum_signals"], {"pd": pd, "MOMENTUM_WINDOW": 126}
    )
    raw = pd.DataFrame({k: v.iloc[:, 0] for k, v in p.items()})
    with redirect_stdout(StringIO()):
        value = source["compute_momentum_signals"]({"A": raw.copy()})
    assert value.cum_overnight_126d.iloc[0] == result.iloc[-1, 0]


@pytest.mark.parametrize(
    "field,bad", [("open", np.nan), ("close", 0), ("volume", np.inf), ("volume", -1)]
)
def test_unknown_or_invalid_observation_not_compressed(field, bad):
    p = panel()
    p[field].iloc[50, 0] = bad
    with pytest.raises(EquityAdmissionError, match="inputs"):
        quote_overnight126(p)


def test_axes_and_order_are_not_repaired():
    p = panel()
    p["open"] = p["open"].iloc[::-1]
    with pytest.raises(EquityAdmissionError, match="axes"):
        quote_overnight126(p)
    p = panel()
    p["volume"] = p["volume"].rename(columns={"A/USD": "B/USD"})
    with pytest.raises(EquityAdmissionError, match="axes"):
        quote_overnight126(p)


def test_liquidity_is_past_only_and_binds_exact_threshold():
    p = panel()
    p["volume"].iloc[:] = 100_000.0
    assert quote_overnight126(p).iloc[-1].notna().all()
    p["volume"].iloc[-1] -= 1
    assert quote_overnight126(p).iloc[-1].isna().all()
    original = quote_overnight126(panel())
    p = panel()
    p["open"].iloc[140:] *= 9
    p["volume"].iloc[140:] /= 10
    pd.testing.assert_frame_equal(original.iloc[:140], quote_overnight126(p).iloc[:140])
    pd.testing.assert_frame_equal(
        original.iloc[:140], quote_overnight126({k: v.iloc[:140] for k, v in panel().items()})
    )


def test_source_spy_unknown_fallback_witness():
    empty = pd.DataFrame({"Close": []}, index=pd.DatetimeIndex([]))
    source = source_functions(
        "final_signal.py",
        ["fetch_spy_regime"],
        {
            "pd": pd,
            "MA50_WINDOW": 50,
            "yf": SimpleNamespace(Ticker=lambda _: SimpleNamespace(history=lambda **_: empty)),
        },
    )
    with redirect_stdout(StringIO()):
        direction, close, mean = source["fetch_spy_regime"]()
    assert direction == "LONG" and np.isnan(close) and np.isnan(mean)


def test_source_metric_path_omits_initial_return_witness():
    source = source_functions(
        "final_backtest.py",
        ["compute_metrics", "clip_to_eval"],
        {
            "pd": pd,
            "np": np,
            "CAPITAL_INIT": 1000.0,
            "RISK_FREE_RATE": 0.0,
            "EVAL_START": pd.Timestamp("2024-03-01"),
        },
    )
    dates = [pd.Timestamp("2024-03-01"), pd.Timestamp("2024-03-04")]
    value = source["clip_to_eval"]({"dates": dates, "equity": [1100.0, 1100.0], "rets": [0.1, 0.0]})
    assert value["equity"] == [1000.0, 1000.0]
    assert value["rets"] == [0.1, 0.0]
    metrics = source["compute_metrics"](value["equity"], value["rets"], "synthetic", dates)
    assert metrics["total_ret"] == 0 and metrics["vol"] > 0


def test_registry_metadata_restricts_overnight_to_us_and_finance_closed():
    from src.factors.registry import Registry

    from freqtrade.optimize.equity_backtesting import EquityBacktesting

    registry = Registry(HERE / "zoo")
    assert registry.get("local_quote_overnight126").meta["universe"] == ["equity_us"]
    actual = registry.compute("local_quote_overnight126", panel())
    pd.testing.assert_frame_equal(actual, quote_overnight126(panel()))
    with pytest.raises(EquityAdmissionError, match="History admission"):
        EquityBacktesting({})


def witnesses():
    test_complete_source_arithmetic_and_minimum_history()
    test_source_spy_unknown_fallback_witness()
    test_source_metric_path_omits_initial_return_witness()
    tree = ast.parse((HERE / "sources/final_backtest.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_longshort")
    conditions = [ast.unparse(n.test) for n in ast.walk(fn) if isinstance(n, ast.If)]
    assert any("Forward_Overnight" in text for text in conditions)
    save(
        HERE / "source_witnesses.json",
        {
            "passed": True,
            "synthetic_semantic_witnesses": 3,
            "source_signal_ratio_126_matches": True,
            "source_empty_spy_defaults_LONG": True,
            "source_clipped_first_return_in_metrics_but_not_equity": True,
            "static_future_availability_filter": conditions,
            "source_top_level_executed": False,
            "source_downloaders_executed": False,
            "author_account_reproduced": False,
            "financial_accounts": 0,
            "scope": "three pure function synthetic probes plus static AST; "
            "not historical error rates "
            "or account performance",
        },
    )


def test_label_clocks_and_price_denominators_are_distinct():
    from predictive import labels

    dates = pd.date_range("2025-01-01", periods=12, freq="B", tz="UTC")
    prices = pd.DataFrame({"A/USD": np.arange(12) + 100.0}, index=dates)
    closes = prices + 10
    schedule = pd.DataFrame(
        {"open": dates + pd.Timedelta(hours=14), "close": dates + pd.Timedelta(hours=21)},
        index=dates,
    )
    night, clocks, _ = labels(prices, schedule, 1, closes)
    first = clocks[0]
    assert pd.Timestamp(first["observed_close"]) < pd.Timestamp(first["return_start"])
    assert first["return_start"] == schedule.close.iloc[1].isoformat()
    assert night.iloc[0, 0] == pytest.approx(prices.iloc[2, 0] / closes.iloc[1, 0] - 1)
    primary, clocks, _ = labels(prices, schedule, 5, closes)
    # The first unavailable previous-session anchor remains excluded, not shifted.
    assert clocks[0]["entry"] == dates[5].isoformat()
    assert primary.iloc[0, 0] == pytest.approx(prices.iloc[10, 0] / prices.iloc[5, 0] - 1)


if __name__ == "__main__":
    witnesses()
