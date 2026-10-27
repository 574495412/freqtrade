"""Lossless disk-backed mappings for an unmodified, wide native NFI backtest.

Only our own generated cache pickles are loaded. Downloaded author pickles are
never opened. This changes storage, not indicators, signals, prices or policies.
"""

import hashlib
import pickle
from collections import OrderedDict
from collections.abc import MutableMapping
from pathlib import Path


class DiskMapping(MutableMapping):
    def __init__(self, root: Path, maximum_cached: int = 3):
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.maximum_cached = maximum_cached
        self.paths = {}
        self.cached = OrderedDict()
        self.maximum_observed_cached = 0

    def _write(self, key, value):
        if key not in self.paths:
            name = hashlib.sha256(pickle.dumps(key, protocol=5)).hexdigest()
            self.paths[key] = self.root / (name + ".pkl")
        temporary = self.paths[key].with_suffix(".writing")
        with temporary.open("wb") as stream:
            pickle.dump(value, stream, protocol=5)
        temporary.replace(self.paths[key])

    def _trim(self):
        while len(self.cached) > self.maximum_cached:
            key, value = self.cached.popitem(last=False)
            # Mapping values may be mutated in place by the native caller.
            self._write(key, value)
        self.maximum_observed_cached = max(self.maximum_observed_cached, len(self.cached))

    def __getitem__(self, key):
        if key in self.cached:
            value = self.cached.pop(key)
        else:
            with self.paths[key].open("rb") as stream:
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
