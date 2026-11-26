"""Process-owned analyzed-frame blocks behind the unchanged native DP slice API."""

import hashlib
import pickle
from collections import OrderedDict
from collections.abc import MutableMapping
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa


class UnsupportedFrameOperation(TypeError):
    pass


def digest(value):
    return hashlib.sha256(value).hexdigest()


def owned_copy(value):
    # Bytes are generated here, never read from an external cache or manifest.
    return pickle.loads(pickle.dumps(value, protocol=5))  # noqa: S301


def semantic_digest(frame):
    """Fingerprint exact public values/schema, excluding Pandas read caches."""
    result = hashlib.sha256()

    def add(value):
        raw = pickle.dumps(value, protocol=5)
        result.update(len(raw).to_bytes(8, "big"))
        result.update(raw)

    def index_values(index):
        add((type(index).__module__, type(index).__qualname__, list(index.names), str(index.dtype)))
        if isinstance(index, pd.MultiIndex):
            add(index.sortorder)
            for level in index.levels:
                index_values(level)
            for codes in index.codes:
                add((codes.dtype.str, codes.tobytes()))
        elif isinstance(index, pd.RangeIndex):
            add((index.start, index.stop, index.step))
        elif isinstance(index, (pd.DatetimeIndex, pd.TimedeltaIndex, pd.PeriodIndex)):
            add(
                (
                    index.asi8.tobytes(),
                    str(getattr(index, "tz", None)),
                    str(getattr(index, "freq", None)),
                )
            )
        elif isinstance(index, pd.CategoricalIndex):
            add((index.codes.dtype.str, index.codes.tobytes(), index.ordered))
            index_values(index.categories)
        else:
            values = index.to_numpy()
            add(
                (values.dtype.str, values.tobytes()) if values.dtype.kind != "O" else index.tolist()
            )

    add(frame.shape)
    index_values(frame.index)
    index_values(frame.columns)
    add(frame.attrs)
    add(frame.flags.allows_duplicate_labels)
    for position in range(frame.shape[1]):
        column = frame.iloc[:, position]
        dtype = column.dtype
        add((type(dtype).__module__, type(dtype).__qualname__, str(dtype)))
        if isinstance(dtype, pd.CategoricalDtype):
            add((column.array.codes.dtype.str, column.array.codes.tobytes(), dtype.ordered))
            index_values(dtype.categories)
        elif isinstance(dtype, pd.DatetimeTZDtype):
            add((column.array.asi8.tobytes(), str(dtype.tz), dtype.unit))
        elif isinstance(dtype, np.dtype) and dtype.kind != "O":
            add((dtype.str, column.to_numpy().tobytes()))
        else:
            add(column.to_numpy(dtype=object).tolist())
    return result.hexdigest()


class _Slices:
    def __init__(self, frame):
        self._frame = frame

    def __getitem__(self, selection):
        if not isinstance(selection, slice) or selection.step not in (None, 1):
            raise UnsupportedFrameOperation("Only contiguous row .iloc slices are supported")
        return self._frame._slice(selection)

    def __setitem__(self, key, value):
        raise UnsupportedFrameOperation("Lazy analyzed frames are immutable")


class LazyAnalyzedFrame:
    """No fallback materialization: native DP must slice before strategy use."""

    def __init__(self, owner, token, frame, blocks):
        self._owner = owner
        self._token = token
        self._index = owned_copy(frame.index)
        self._template = owned_copy(frame.iloc[:0])
        self._attrs = owned_copy(frame.attrs)
        self._allows_duplicates = frame.flags.allows_duplicate_labels
        self._blocks = blocks
        self._shape = frame.shape
        self._metadata_snapshot = pickle.dumps(
            (self._index, self._template, self._attrs, self._allows_duplicates, self._shape),
            protocol=5,
        )

    @property
    def iloc(self):
        return _Slices(self)

    @property
    def shape(self):
        return self._shape

    def __len__(self):
        return self._shape[0]

    @property
    def empty(self):
        return not self._shape[0] or not self._shape[1]

    @property
    def columns(self):
        return owned_copy(self._template.columns)

    @property
    def dtypes(self):
        return self._template.dtypes.copy(deep=True)

    @property
    def index(self):
        return owned_copy(self._index)

    def __getattr__(self, name):
        raise UnsupportedFrameOperation(f"Unsupported lazy analyzed-frame API: {name}")

    def __getitem__(self, key):
        raise UnsupportedFrameOperation("Slice rows through native DataProvider first")

    def __setitem__(self, key, value):
        raise UnsupportedFrameOperation("Lazy analyzed frames are immutable")

    def _check_metadata(self):
        # Pandas may populate lazy index caches during read-only slicing. Those
        # physical pickle-layout changes are not semantic frame mutations.
        index, template, attrs, flag, shape = pickle.loads(self._metadata_snapshot)  # noqa: S301
        try:
            pd.testing.assert_index_equal(self._index, index, exact=True, check_categorical=True)
            pd.testing.assert_frame_equal(
                self._template,
                template,
                check_exact=True,
                check_dtype=True,
                check_index_type=True,
                check_column_type=True,
                check_flags=True,
            )
            assert getattr(self._index, "freq", None) == getattr(index, "freq", None)
            assert pickle.dumps(self._attrs, protocol=5) == pickle.dumps(attrs, protocol=5)
            assert pickle.dumps(self._template.attrs, protocol=5) == pickle.dumps(
                template.attrs, protocol=5
            )
            assert self._allows_duplicates == flag and self._shape == shape
        except AssertionError as error:
            raise ValueError("Immutable analyzed-frame metadata was mutated") from error

    def _slice(self, selection):
        start, stop, _ = selection.indices(len(self))
        pieces = []
        if stop > start:
            first = start // self._owner.block_rows
            last = (stop - 1) // self._owner.block_rows
            for block_index in range(first, last + 1):
                offset = block_index * self._owner.block_rows
                block = self._owner._load(self._token, block_index, self._blocks[block_index])
                pieces.append(block.iloc[max(0, start - offset) : min(len(block), stop - offset)])
        result = pd.concat(pieces, axis=0) if pieces else self._template.copy(deep=True)
        # concat may change index classes/frequency; original positional slice is authoritative.
        result.index = owned_copy(self._index[selection])
        result.columns = owned_copy(self._template.columns)
        result.attrs = owned_copy(self._attrs)
        result.flags.allows_duplicate_labels = self._allows_duplicates
        # Detach nested object values too; pandas deep=True only copies object pointers.
        return owned_copy(result)


class AnalyzedFrameMapping(MutableMapping):
    def __init__(self, root, *, runmode, block_rows=4096, maximum_cached=32):
        if getattr(runmode, "value", runmode) != "backtest":
            raise ValueError("Analyzed block storage is backtest-only")
        if block_rows != 4096 or maximum_cached != 32:
            raise ValueError("This prototype has frozen 4096-row blocks and a 32-block LRU")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.block_rows = block_rows
        self.maximum_cached = maximum_cached
        self._entries = {}
        self._cache = OrderedDict()
        self._generation = 0
        self.stats = {
            "block_loads": 0,
            "cache_hits": 0,
            "compressed_read_bytes": 0,
            "decompressed_read_bytes": 0,
            "written_bytes": 0,
            "maximum_cached_blocks": 0,
            "evictions": 0,
        }
        self.receipts = []

    @staticmethod
    def _key(key):
        if not isinstance(key, tuple) or len(key) != 3 or not all(isinstance(x, str) for x in key):
            raise TypeError("Native analyzed-cache keys must be (pair,timeframe,candle_type)")

    def __setitem__(self, key, value):
        self._key(key)
        if not isinstance(value, tuple) or len(value) != 2:
            raise TypeError("Native cached value must be (DataFrame, lastrefresh)")
        frame, refreshed = value
        if not isinstance(frame, pd.DataFrame) or not isinstance(refreshed, datetime):
            raise TypeError("Native DataFrame and datetime refresh object required")
        self.flush()  # A new native assignment cannot silently erase a dirty old block.
        self._generation += 1
        token = f"{self._generation:06d}-{digest(pickle.dumps(key, protocol=5))}"
        blocks = []
        for start in range(0, len(frame), self.block_rows):
            raw = pickle.dumps(frame.iloc[start : start + self.block_rows], protocol=5)
            compressed = pa.Codec("zstd").compress(raw, asbytes=True)
            path = self.root / f"{token}-{start:09d}.pkl.zstd"
            with path.open("xb") as stream:
                stream.write(compressed)
            record = {
                "path": str(path),
                "start": start,
                "rows": min(self.block_rows, len(frame) - start),
                "compressed_bytes": len(compressed),
                "raw_bytes": len(raw),
                "compressed_sha256": digest(compressed),
                "raw_sha256": digest(raw),
            }
            blocks.append(record)
            self.receipts.append(record)
            self.stats["written_bytes"] += len(compressed)
        if key in self._entries:
            self.__delitem__(key)
        self._entries[key] = (LazyAnalyzedFrame(self, token, frame, blocks), refreshed)

    def __getitem__(self, key):
        return self._entries[key]

    def __delitem__(self, key):
        self.flush()
        frame, _ = self._entries.pop(key)
        for cache_key in list(self._cache):
            if cache_key[0] == frame._token:
                del self._cache[cache_key]
        # Generated bytes remain as provenance; never reuse a generation/path.

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)

    def _check_block(self, cached):
        frame, snapshot = cached
        if semantic_digest(frame) != snapshot:
            raise ValueError("Immutable cached analyzed block was mutated")

    def _load(self, token, block_index, receipt):
        key = (token, block_index)
        if key in self._cache:
            cached = self._cache.pop(key)
            self._cache[key] = cached
            self.stats["cache_hits"] += 1
            return cached[0]
        # Only paths registered by this object's own writer can arrive here.
        path = Path(receipt["path"])
        if path.parent != self.root or path.is_symlink():
            raise ValueError("Generated block escaped its process-owned directory")
        compressed = path.read_bytes()
        if digest(compressed) != receipt["compressed_sha256"]:
            raise ValueError("Generated block compressed hash differs before unpickle")
        raw = pa.Codec("zstd").decompress(compressed, receipt["raw_bytes"], asbytes=True)
        if digest(raw) != receipt["raw_sha256"]:
            raise ValueError("Generated block raw hash differs before unpickle")
        frame = pickle.loads(raw)  # noqa: S301 - process-owned, both hashes checked above
        if not isinstance(frame, pd.DataFrame) or len(frame) != receipt["rows"]:
            raise ValueError("Generated block schema differs")
        self.stats["block_loads"] += 1
        self.stats["compressed_read_bytes"] += len(compressed)
        self.stats["decompressed_read_bytes"] += len(raw)
        self._cache[key] = (frame, semantic_digest(frame))
        while len(self._cache) > self.maximum_cached:
            _, old = self._cache.popitem(last=False)
            self._check_block(old)
            self.stats["evictions"] += 1
        self.stats["maximum_cached_blocks"] = max(
            self.stats["maximum_cached_blocks"], len(self._cache)
        )
        return frame

    def flush(self):
        for frame, _ in self._entries.values():
            frame._check_metadata()
        for cached in self._cache.values():
            self._check_block(cached)

    def drop_cached_blocks(self):
        self.flush()
        self._cache.clear()


def install(provider, root):
    """Replace storage only, preserving native setters/getter and clear semantics."""
    from freqtrade.enums import RunMode

    if provider.runmode != RunMode.BACKTEST:
        raise ValueError("Native provider must already be in backtest mode")
    root = Path(root)
    original_clear = provider.clear_cache
    stores = []

    def clear():
        for store in stores:
            store.flush()
        original_clear()
        store = AnalyzedFrameMapping(root / str(len(stores)), runmode=provider.runmode)
        provider._DataProvider__cached_pairs = store
        stores.append(store)

    provider.clear_cache = clear
    clear()
    return stores
