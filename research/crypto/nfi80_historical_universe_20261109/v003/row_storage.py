"""Lossless block storage for the exact rows produced by native Backtesting.

The native function still constructs/signals/shifts every row. This adapter only
changes retention: at most 128 blocks, rather than 221 full Python row lists.
Only caches created by this process are read. No numeric downcast or conversion.
"""

import hashlib
import pickle
from collections import OrderedDict
from collections.abc import Mapping

import pyarrow as pa


class RowBlocks:
    def __init__(self, root, block_size=1024, maximum_cached=128):
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.block_size = block_size
        self.maximum_cached = maximum_cached
        self.cached = OrderedDict()
        self.pairs = {}
        self.receipts = {}

    def add(self, pair, rows):
        key = hashlib.sha256(pair.encode()).hexdigest()
        references = []
        for start in range(0, len(rows), self.block_size):
            block = rows[start : start + self.block_size]
            blob = pickle.dumps(block, protocol=5)
            path = self.root / f"{key}-{start}.pkl.zstd"
            with pa.CompressedOutputStream(str(path), "zstd") as stream:
                stream.write(blob)
            # Prove byte-for-byte recovery before admitting any account row.
            with pa.CompressedInputStream(str(path), "zstd") as stream:
                recovered = stream.read()
            if recovered != blob:
                raise ValueError("Native row block did not recover exactly")
            references.append(path)
            self.receipts[path.name] = {
                "serialized_sha256": hashlib.sha256(blob).hexdigest(),
                "compressed_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": len(block),
            }
        self.pairs[pair] = PairRows(self, references, len(rows))

    def block(self, path):
        if path in self.cached:
            rows = self.cached.pop(path)
        else:
            with pa.CompressedInputStream(str(path), "zstd") as stream:
                blob = stream.read()
            if hashlib.sha256(blob).hexdigest() != self.receipts[path.name]["serialized_sha256"]:
                raise ValueError("Native row block changed during replay")
            rows = pickle.loads(blob)  # noqa: S301 - generated and checksummed in this process
        self.cached[path] = rows
        while len(self.cached) > self.maximum_cached:
            self.cached.popitem(last=False)
        return rows

    def __getitem__(self, pair):
        return self.pairs[pair]

    def keys(self):
        return self.pairs.keys()


class PairRows:
    def __init__(self, store, paths, length):
        self.store, self.paths, self.length = store, paths, length

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(self.length))]
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        block, offset = divmod(index, self.store.block_size)
        return self.store.block(self.paths[block])[offset]


class LazyTrimMapping(Mapping):
    """Apply the exact native trim on demand, without copying the entire cache.

    Native Backtesting uses this temporary map only to determine min/max dates
    when export is not signals. The full preprocessed map remains authoritative.
    """

    def __init__(self, source, operation):
        self.source = source
        self.operation = operation
        self.pairs = []
        for pair, frame in source.items():
            if operation({pair: frame}):
                self.pairs.append(pair)

    def __iter__(self):
        return iter(self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, pair):
        return self.operation({pair: self.source[pair]})[pair]


def release_consumed(mapping, pair, cache_root):
    """Release only this process's generated source frame after native conversion."""
    path = mapping.paths[pair]
    if path.is_symlink() or not path.resolve().is_relative_to(cache_root.resolve()):
        raise ValueError("Refusing to remove a non-cache file")
    size = path.stat().st_size
    del mapping[pair]
    path.unlink()
    return size
