"""Use pinned native history functions; verify local extension against real rows."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    plan = json.loads((HERE / "plan.json").read_text())
    engine = Path(plan["native_source"])
    assert sha(plan["native_manifest"]) == plan["native_manifest_sha256"]
    manifest = json.loads(Path(plan["native_manifest"]).read_text())
    assert manifest["commit"] == plan["native_commit"]
    for name, digest in manifest["files"].items():
        assert sha(engine / name) == digest, name
    assert sha(plan["local_extension"]) == plan["local_extension_sha256"]
    sys.path.insert(0, str(engine))
    import pandas as pd
    from freqtrade.data.history.history_utils import load_pair_history
    from freqtrade.data.history.datahandlers.featherdatahandler import FeatherDataHandler
    from freqtrade.enums import CandleType

    spec = importlib.util.spec_from_file_location("local_equity_history", plan["local_extension"])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    grids = {}
    for name, rec in plan["calendars"].items():
        assert sha(rec["path"]) == rec["sha256"]
        assert sha(rec["schedule_path"]) == rec["schedule_sha256"]
        grids[name] = tuple(json.loads(Path(rec["path"]).read_text()))
    native_dir = HERE / "native_history"
    native_dir.mkdir(exist_ok=False)
    rows = []
    for rec in plan["records"]:
        assert sha(rec["source"]) == rec["source_sha256"]
        assert sha(rec["staged"]) == rec["staged_sha256"]
        frame = pd.read_parquet(rec["staged"])
        FeatherDataHandler(native_dir).ohlcv_store(rec["pair"], "1d", frame, CandleType.SPOT)
        contract = module.EquitySessionContract(
            rec["pair"], rec["calendar"], grids[rec["calendar"]]
        )
        result = dict(symbol=rec["symbol"], calendar=rec["calendar"], source_rows=len(frame))
        default = load_pair_history(rec["pair"], "1d", native_dir)
        source_dates = pd.DatetimeIndex(frame.date)
        inserted = pd.DatetimeIndex(default.date).difference(source_dates)
        result.update(
            default_native_rows=len(default),
            default_inserted_dates=len(inserted),
            default_inserted_dates_sample=inserted[:5].strftime("%Y-%m-%d").tolist(),
        )
        try:
            admitted = module.load_equity_pair_history(native_dir, contract)
            pd.testing.assert_frame_equal(
                frame.reset_index(drop=True), admitted, check_exact=True, check_dtype=False
            )
            result.update(status="admitted", exact_ohlcv_cells=len(frame) * 5)
        except module.EquityAdmissionError as exc:
            result.update(status="rejected", reason=str(exc), exact_ohlcv_cells=0)
        rows.append(result)
    try:
        module.require_complete_equity_account_support()
    except module.EquityAdmissionError as exc:
        account = dict(status="rejected", reason=str(exc), complete_accounts=0, return_metrics=None)
    else:
        raise AssertionError("Unexpected complete equity account certification")
    summary = dict(
        plan_sha256=sha(HERE / "plan.json"),
        native_commit=plan["native_commit"],
        native_core_verified_files=len(manifest["files"]),
        actual_native_history_api=load_pair_history.__module__,
        actual_native_module_path=str(sys.modules[load_pair_history.__module__].__file__),
        local_extension=True,
        calendar_certification=False,
        records=rows,
        account=account,
        admitted=sum(r["status"] == "admitted" for r in rows),
        rejected=sum(r["status"] == "rejected" for r in rows),
        financial_backtests=0,
        alpha_definitions=0,
        independent_oos_certified=False,
    )
    (HERE / "audit.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        "AUDITED",
        summary["admitted"],
        "admitted",
        summary["rejected"],
        "rejected; 0 accounts",
        flush=True,
    )


if __name__ == "__main__":
    main()
