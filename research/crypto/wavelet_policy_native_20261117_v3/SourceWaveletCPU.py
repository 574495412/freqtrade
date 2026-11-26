"""Local import bridge for pinned author policy through official IStrategy.

Source: nateemma/strategies 9bb7dab5df53203e69f60336749551a40767af0b, GPL-3.0.
This selects cold CPU definitions; it is not an upstream Freqtrade strategy.
"""

from research.crypto.wavelet_policy_native_20261117_v3.source_policy import build_policy_class


AuthorWaveletDWT = build_policy_class()


class SourceWaveletCPU(AuthorWaveletDWT):
    """All trading callbacks inherit actual fixed author definitions."""
