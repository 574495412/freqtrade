"""Zstd level6 lossless disk mappings for the unchanged native backtester.

Only our own generated cache pickles are loaded. Downloaded author pickles are
never opened. This changes storage, not indicators, signals, prices or policies.
"""

import hashlib
import io
import pickle
from collections import OrderedDict
from collections.abc import MutableMapping
from pathlib import Path

import pyarrow as pa


class HashingSink:
    def __init__(self, target=None):
        self.target = target
        self.digest = hashlib.sha256()

    def write(self, data):
        self.digest.update(data)
        return self.target.write(data) if self.target is not None else memoryview(data).nbytes


class DiskMapping(MutableMapping):
    def __init__(self, root: Path, maximum_cached: int = 3):
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.maximum_cached = maximum_cached
        self.paths = {}
        self.cached = OrderedDict()
        self.maximum_observed_cached = 0
        self.digests = {}
        self.writeback_skips = 0

    def _write(self, key, value):
        if key not in self.paths:
            name = hashlib.sha256(pickle.dumps(key, protocol=5)).hexdigest()
            self.paths[key] = self.root / (name + ".pkl")
        temporary = self.paths[key].with_suffix(".writing")
        blob = pickle.dumps(value, protocol=5)
        compressed = pa.Codec("zstd", compression_level=6).compress(blob, asbytes=False)
        temporary.write_bytes(memoryview(compressed))
        temporary.replace(self.paths[key])
        self.digests[key] = hashlib.sha256(blob).hexdigest()

    def _trim(self):
        while len(self.cached) > self.maximum_cached:
            key, value = self.cached.popitem(last=False)
            # Mapping values may be mutated in place by the native caller.
            # Skip a rewrite only when the complete serialized object is byte-identical.
            sink = HashingSink()
            pickle.dump(value, sink, protocol=5)
            if self.digests.get(key) == sink.digest.hexdigest():
                self.writeback_skips += 1
            else:
                self._write(key, value)
        self.maximum_observed_cached = max(self.maximum_observed_cached, len(self.cached))

    def __getitem__(self, key):
        if key in self.cached:
            value = self.cached.pop(key)
        else:
            with pa.CompressedInputStream(str(self.paths[key]), "zstd") as raw_stream:
                with io.BufferedReader(raw_stream) as stream:
                    value = pickle.load(stream)  # noqa: S301 - this process wrote it
        self.cached[key] = value
        self._trim()
        return value

    def __setitem__(self, key, value):
        self._write(key, value)
        self.cached.pop(key, None)
        self.cached[key] = value
        self._trim()

    def __delitem__(self, key):
        self.cached.pop(key, None)
        del self.paths[key]

    def __iter__(self):
        return iter(self.paths)

    def __len__(self):
        return len(self.paths)

    def flush(self):
        for key, value in self.cached.items():
            self._write(key, value)


def disk_map_each(original, data, destination, *args):
    """Invoke the original pair-local operation, retaining all output values."""
    result = DiskMapping(destination)
    for pair, frame in data.items():
        result.update(original({pair: frame}, *args))
    return result
