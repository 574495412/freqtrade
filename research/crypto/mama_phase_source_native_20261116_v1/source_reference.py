"""Separately compiled pinned source C; no candidate calculator import."""
import ctypes
from pathlib import Path

import numpy as np


def source_mama(close):
    values = np.ascontiguousarray(close, dtype=np.float64)
    n = len(values)
    if not n:
        raise ValueError('nonempty prices')
    library = ctypes.CDLL(str(Path(__file__).absolute().with_name('source_reference.dylib')))
    real = np.ctypeslib.ndpointer(dtype=np.float64, flags='C_CONTIGUOUS')
    library.TA_MAMA.argtypes = [ctypes.c_int, ctypes.c_int, real, ctypes.c_double,
        ctypes.c_double, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), real, real]
    library.TA_MAMA.restype = ctypes.c_int
    begin, count = ctypes.c_int(), ctypes.c_int()
    mama, fama = np.empty(n), np.empty(n)
    code = library.TA_MAMA(0, n - 1, values, .5, .05,
        ctypes.byref(begin), ctypes.byref(count), mama, fama)
    assert code == 0
    result = []
    for column in [mama, fama]:
        full = np.full(n, np.nan)
        full[begin.value:begin.value + count.value] = column[:count.value]
        result.append(full)
    return tuple(result)
