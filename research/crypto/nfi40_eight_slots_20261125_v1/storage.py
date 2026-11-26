"""Retain up to twelve analyzed frames for the eight-slot replay."""

from research.crypto.nfi80_capacity_20261109.zstd_storage import DiskMapping as ZstdDiskMapping


class DiskMapping(ZstdDiskMapping):
    def __init__(self, root, maximum_cached):
        # Keep at most eight active pairs plus room for entry checks and market context.
        # Serialization and in-place mutation detection use the existing lossless owner.
        if root.name.startswith("analyzed_"):
            maximum_cached = 12
        super().__init__(root, maximum_cached)
