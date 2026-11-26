"""Write the same serialized frames with bounded memory and disk consumption."""

import hashlib
import pickle
from weakref import WeakValueDictionary

import pyarrow as pa

from research.crypto.nfi80_capacity_20261109.zstd_storage import DiskMapping as OriginalDiskMapping


MEMORY_LIMIT = 2 * 1024 ** 3
CODEC = pa.Codec("zstd", compression_level=6)
_MAPPINGS = WeakValueDictionary()


class DiskMapping(OriginalDiskMapping):
    """Use memory for up to 2 GiB of exact compressed blobs, then spill to disk."""

    def __init__(self, root, maximum_cached=3):
        super().__init__(root, maximum_cached)
        self.compressed = {}
        self.raw_lengths = {}
        self.memory_bytes = 0
        _MAPPINGS[id(self)] = self

    def _write(self, key, value):
        if key not in self.paths:
            name = hashlib.sha256(pickle.dumps(key, protocol=5)).hexdigest()
            self.paths[key] = self.root / (name + ".pkl")
        path = self.paths[key]
        raw = pickle.dumps(value, protocol=5)
        digest = hashlib.sha256(raw).hexdigest()
        compressed = CODEC.compress(raw)
        previous = self.compressed.get(key)
        replaced = len(previous) if previous is not None else 0
        resident = sum(mapping.memory_bytes for mapping in _MAPPINGS.values()) - replaced
        if resident + len(compressed) <= MEMORY_LIMIT:
            self.compressed[key] = compressed
            self.memory_bytes += len(compressed) - replaced
            path.unlink(missing_ok=True)
        else:
            temporary = path.with_suffix(".writing")
            with temporary.open("wb") as output:
                output.write(memoryview(compressed))
            temporary.replace(path)
            self.compressed.pop(key, None)
            self.memory_bytes -= replaced
        self.raw_lengths[key] = len(raw)
        self.digests[key] = digest

    def __getitem__(self, key):
        if key in self.cached:
            value = self.cached.pop(key)
        else:
            store = self.compressed.get(key)
            if store is None:
                store = self.paths[key].read_bytes()
            raw = CODEC.decompress(store, self.raw_lengths[key])
            if hashlib.sha256(raw).hexdigest() != self.digests[key]:
                raise ValueError("Generated frame differs from the exact serialized frame")
            value = pickle.loads(raw)  # only this process's checksum-verified cache
        self.cached[key] = value
        self._trim()
        return value

    def __delitem__(self, key):
        path = self.paths[key]
        path.unlink(missing_ok=True)
        resident = self.compressed.pop(key, None)
        if resident is not None:
            self.memory_bytes -= len(resident)
        del self.raw_lengths[key]
        del self.digests[key]
        super().__delitem__(key)

    def discard_generated_cache(self, pair, cache_root):
        path = self.paths[pair]
        if path.is_symlink() or not path.resolve().is_relative_to(cache_root.resolve()):
            raise ValueError("Refusing to remove non-cache data")
        size = path.stat().st_size if path.exists() else 0
        del self[pair]
        return size
