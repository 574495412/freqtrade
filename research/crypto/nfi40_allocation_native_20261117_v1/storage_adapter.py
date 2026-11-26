"""Reuse the sealed v003 native storage interfaces; no financial policy here."""

import hashlib
import importlib.util
import json
from pathlib import Path


def load(name, record):
    path = Path(record["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError("Frozen storage source changed: " + str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(account, native, plan, cache, output):
    """Attach the original v003 LazyTrim, RowBlocks and Zstd map operations."""
    storage = load("nfi40_frozen_zstd", plan["storage_source"])
    rows_module = load("nfi40_frozen_rows", plan["row_storage"])
    original_trim = native.trim_dataframes
    original_rows = account._get_ohlcv_as_lists

    def trim(frames, timerange, startup_candles):
        return rows_module.LazyTrimMapping(
            frames, lambda value: original_trim(value, timerange, startup_candles)
        )

    native.trim_dataframes = trim

    def get_rows(processed):
        if account.config.get("export", "none") == "signals":
            raise ValueError("Consumed-cache storage requires non-signals export")
        stored = rows_module.RowBlocks(cache / "native_rows")
        released_bytes = 0
        for pair in list(processed):
            frame = processed[pair]
            original = original_rows({pair: frame})
            stored.add(pair, original[pair])
            released_bytes += rows_module.release_consumed(processed, pair, cache)
        (output / "native_row_storage_audit.json").write_text(
            json.dumps(
                {
                    "exact_serialized_roundtrip": True,
                    "pair_count": len(stored.pairs),
                    "blocks": stored.receipts,
                    "block_size": stored.block_size,
                    "maximum_cached_blocks": stored.maximum_cached,
                    "consumed_cache_bytes_released": released_bytes,
                    "only_current_process_intermediate_frames_released": True,
                },
                indent=2,
            )
            + "\n"
        )
        return stored

    account._get_ohlcv_as_lists = get_rows
    for strategy in account.strategylist:
        operation = strategy.advise_all_indicators

        def advise(frames, operation=operation):
            result = storage.DiskMapping(cache / "indicators", 1)
            for count, (pair, frame) in enumerate(frames.items(), 1):
                result.update(operation({pair: frame}))
                print(
                    json.dumps({"stage": "indicators", "pair": pair, "completed": count}),
                    flush=True,
                )
            return result

        strategy.advise_all_indicators = advise
    provider = account.dataprovider
    original_clear = provider.clear_cache
    serial = 0

    def clear():
        nonlocal serial
        original_clear()
        serial += 1
        provider._DataProvider__cached_pairs = storage.DiskMapping(
            cache / f"analyzed_{serial}", plan["storage_policy"]["analyzed_provider_lru"]
        )

    provider.clear_cache = clear
    clear()
    return {"storage": storage, "rows": rows_module, "original_trim": original_trim}
