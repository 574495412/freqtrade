"""Two-asset native dictionaries retained in RAM under the registered RSS guard.

The official engine already operates on mappings. This auxiliary adapter keeps
its original dict/object semantics and avoids duplicating wide temporary frames
on a nearly full disk. Account results, fills, NAV and source feature receipts
are still persisted separately. It provides no trading or valuation calculation.
"""


class DiskMapping(dict):
    """Match the existing runner's mapping constructor with native dict storage."""

    def __init__(self, root, maximum_cached=3):
        super().__init__()
        root.mkdir(parents=True, exist_ok=False)
        self.root = root
        self.maximum_cached = maximum_cached

    def flush(self):
        return None
