"""Execute only the reviewed source OnData method against nonfinancial stubs.

No QuantConnect runtime, initializer, data downloader, orders or account engine.
"""

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


HERE = Path(__file__).absolute().parent


def main():
    path = HERE / "sources/EODHDUpcomingSplitsAlgorithm.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "OnData")
    namespace = {"timedelta": timedelta, "EODHDUpcomingSplits": object()}
    code = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(code, str(path) + "::OnData-only", "exec"), namespace)  # noqa: S102
    now = datetime(2025, 6, 8, 13, 30, tzinfo=UTC)

    def probe(data, invested):
        calls = []
        stub = SimpleNamespace(
            equity_symbol="SYNTHETIC",
            portfolio={"SYNTHETIC": SimpleNamespace(invested=invested)},
            set_holdings=lambda *args: calls.append(["set_holdings", *args]),
            liquidate=lambda *args: calls.append(["liquidate", *args]),
        )
        slice_stub = SimpleNamespace(time=now, get=lambda _: data)
        namespace["OnData"](stub, slice_stub)
        return calls

    forward = SimpleNamespace(split_date=now + timedelta(days=2), split_factor=1 / 15)
    reverse = SimpleNamespace(split_date=now + timedelta(days=2), split_factor=10)
    stale = SimpleNamespace(split_date=now - timedelta(days=10), split_factor=1 / 15)
    rows = {
        "forward_factor_old_over_new": probe({"SYNTHETIC": forward}, False),
        "reverse_source_requests_short": probe({"SYNTHETIC": reverse}, False),
        "missing_observation_before_planned_date": probe({}, True),
        "past_split_date_if_still_in_data": probe({"SYNTHETIC": stale}, False),
    }
    assert rows["forward_factor_old_over_new"] == [["set_holdings", "SYNTHETIC", 1]]
    assert rows["reverse_source_requests_short"] == [["set_holdings", "SYNTHETIC", -1]]
    assert rows["missing_observation_before_planned_date"] == [["liquidate", "SYNTHETIC"]]
    assert rows["past_split_date_if_still_in_data"] == [["set_holdings", "SYNTHETIC", 1]]
    result = {
        "scope": "Reviewed source method control-flow only;not actual QC runtime or profit",
        "source_path": str(path),
        "cases": rows,
        "source_account_executed": False,
        "stub_call_is_not_fill": True,
        "observed_semantics": [
            (
                "Missing target data exits invested position immediately,"
                "not an explicit split-plus-one-day timer"
            ),
            "Only upper split-date bound exists;feed must prevent stale observations",
            "Reverse branch needs borrow/short-account support",
            "Source initialization/API spelling and actual dataset behavior not runtime-verified",
        ],
    }
    (HERE / "source_witnesses.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
