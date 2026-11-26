"""Source policy executed by native Freqtrade, with exact numerical-result caching.

GPL-3.0 source: nateemma/strategies 9bb7dab5df53203e69f60336749551a40767af0b.
Local research wrapper only. No source trading threshold/model parameter changes.
"""

import hashlib
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from research.crypto.wavelet_policy_native_20261117_v3.SourceWaveletCPU import SourceWaveletCPU


HERE = Path(__file__).resolve().parent
POLICY = Path("/Users/chrise/GitHub/freqtrade/research/crypto/wavelet_policy_native_20261117_v3")
POLICY_SHA = "814fe8478e82518cefa84366e9bc0a738c38cfd7e3d9e7aebb0a71aec54da160"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(obj, indent=2, default=str, allow_nan=False) + "\n")
    temporary.replace(path)


def frame_hash(frame):
    fields = ["date", "open", "high", "low", "close", "volume"]
    subset = frame[fields]
    digest = hashlib.sha256(pd.util.hash_pandas_object(subset, index=True).values.tobytes())
    digest.update(str([(c, str(subset[c].dtype)) for c in fields]).encode())
    return digest.hexdigest()


def array_hash(array):
    value = np.ascontiguousarray(array)
    return hashlib.sha256(
        str((value.shape, str(value.dtype))).encode() + value.tobytes()
    ).hexdigest()


class ResearchWaveletCPU(SourceWaveletCPU):
    """All source policy callbacks remain inherited; only observation/cache is local."""

    def ft_bot_start(self, **kwargs):
        if (
            self.config.get("runmode") != RunMode.BACKTEST
            or not self.config.get("dry_run")
            or self.config.get("research_wavelet_cpu") is not True
            or self.config.get("trading_mode") != "spot"
            or Trade.use_db
            or self.config["exchange"]["pair_whitelist"] != ["BTC/USDT", "ETH/USDT"]
            or self.config.get("max_open_trades") != 2
        ):
            raise ValueError("Fixed isolated native BTC/ETH spot research required")
        if sha(POLICY / "artifact_manifest.json") != POLICY_SHA:
            raise ValueError("Source policy seal changed")
        self.research_trace = {"model_events": [], "feature_caches": []}
        self._fatal_error = None
        self._artifact_root = Path(self.config["research_artifact_root"])
        self._cache_root = Path(self.config["research_feature_cache"])
        self._cache_root.mkdir(parents=True, exist_ok=True)
        super().ft_bot_start(**kwargs)
        if Path(self.get_storage_location()).exists():
            raise ValueError("Cold source branch refuses any saved_data directory")
        if self.use_mlx or self.scale_results or self.strategy_config.aggregate_pairs:
            raise ValueError("Source CPU branch changed")

    def feature_identity(self, frame):
        parameters = {name: value.value for name, value in self.enumerate_parameters()}
        return {
            "input_sha256": frame_hash(frame),
            "source_policy_manifest_sha256": POLICY_SHA,
            "resolved_parameters": parameters,
            "source_startup_candles": self.startup_candle_count,
            "numerical_wrapper_sha256": sha(__file__),
            "source_rows": len(frame),
            "first": str(frame.date.iloc[0]),
            "last": str(frame.date.iloc[-1]),
        }

    def record_progress(self, row):
        self.research_trace["model_events"].append(row)
        save(self._artifact_root / "model_progress.json", row)

    def guarded_source_indicators(self, dataframe, metadata):
        function_globals = self.add_rolling_predictions.__func__.__globals__
        original = function_globals["MultiOutputRegressor"]
        owner, started = self, time.monotonic()
        dates = dataframe.date.reset_index(drop=True)
        events = []

        class ObservedRegressor(original):
            def fit(self, x, y, **kwargs):
                n = len(x)
                event = {
                    "stage": "source_model_fit",
                    "pair": metadata["pair"],
                    "rows": n,
                    "columns": x.shape[1],
                    "label_last": str(dates.iloc[n + 5]),
                    "first_prediction": str(dates.iloc[n + 6]),
                    "x_sha256": array_hash(x),
                    "y_sha256": array_hash(y),
                    "elapsed_seconds": time.monotonic() - started,
                }
                if not (dates.iloc[n + 5] < dates.iloc[n + 6]) or x.shape[1] != 101:
                    raise ValueError("Unexpected source training contract")
                owner.record_progress(event)
                result = super().fit(x, y, **kwargs)
                params = self.estimators_[0].booster_.params
                if (
                    len(self.estimators_) != 101
                    or params["num_iterations"] != 100
                    or params["random_state"] != 42
                ):
                    raise ValueError("Source estimator parameters changed")
                event.update(
                    stage="source_model_fit_complete",
                    estimator_count=len(self.estimators_),
                    booster_parameters=params,
                )
                events.append(dict(event))
                owner.record_progress(dict(event))
                return result

            def predict(self, x, **kwargs):
                predicted = super().predict(x, **kwargs)
                if not np.isfinite(predicted).all():
                    raise ValueError("Nonfinite source model prediction")
                event = {
                    "stage": "source_model_predict_complete",
                    "pair": metadata["pair"],
                    "rows": len(x),
                    "prediction_sha256": array_hash(predicted),
                    "elapsed_seconds": time.monotonic() - started,
                }
                events.append(event)
                owner.record_progress(event)
                return predicted

        function_globals["MultiOutputRegressor"] = ObservedRegressor
        try:
            result = super().populate_indicators(dataframe, metadata)
        finally:
            function_globals["MultiOutputRegressor"] = original
        expected = len(range(1000, len(dataframe), 2500))
        if (
            len([e for e in events if e["stage"] == "source_model_fit_complete"]) != expected
            or len([e for e in events if e["stage"] == "source_model_predict_complete"]) != expected
        ):
            raise ValueError("Source caught a numerical failure or skipped a training window")
        if not np.isfinite(result.predicted_gain).all():
            raise ValueError("Invalid source output")
        return result, events

    def populate_indicators(self, dataframe, metadata):
        if shutil.disk_usage(self._cache_root).free < 1024**3:
            raise RuntimeError("Registered 1GiB minimum free disk guard")
        identity = self.feature_identity(dataframe)
        asset = metadata["pair"].replace("/", "_")
        target, receipt_path = (
            self._cache_root / (asset + ".parquet"),
            self._cache_root / (asset + ".json"),
        )
        if target.exists() or receipt_path.exists():
            if not (target.exists() and receipt_path.exists()):
                raise ValueError("Incomplete numerical cache; preserve failed attempt")
            receipt = json.loads(receipt_path.read_text())
            if receipt["identity"] != identity or sha(target) != receipt["output_sha256"]:
                raise ValueError("Numerical cache provenance/input mismatch")
            result = pd.read_parquet(target)
            self.curr_pair, self.curr_dataframe = metadata["pair"], result
            self.model_trained, self.new_model, self.training_mode = True, False, False
            self.custom_trade_info[metadata["pair"]] = receipt["source_custom_trade_info"]
            self.research_trace["feature_caches"].append(
                {
                    "pair": metadata["pair"],
                    "cache_hit": True,
                    "path": str(target),
                    "sha256": sha(target),
                }
            )
            return result
        result, events = self.guarded_source_indicators(dataframe, metadata)
        result.to_parquet(target, index=True, compression="zstd")
        source_state = self.custom_trade_info[metadata["pair"]]
        if any(
            v is not None and not isinstance(v, (str, int, float, bool))
            for v in source_state.values()
        ):
            raise ValueError(
                "Unexpected non-scalar source runtime state; do not synthesize cache state"
            )
        receipt = {
            "identity": identity,
            "source_custom_trade_info": source_state,
            "output_sha256": sha(target),
            "model_events": events,
            "computed_through_actual_source_native_indicator_callback": True,
            "financial_matching_has_not_started_for_this_feature_generation": True,
        }
        save(receipt_path, receipt)
        self.research_trace["feature_caches"].append(
            {
                "pair": metadata["pair"],
                "cache_hit": False,
                "path": str(target),
                "sha256": sha(target),
            }
        )
        return result
