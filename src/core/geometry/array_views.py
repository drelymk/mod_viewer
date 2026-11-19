"""Shared bounded-layout views over load-owned binary vertex records."""


class UnsupportedLayout(Exception):
    """A layout requires the caller's conservative scalar decoder."""


def strided_view(np, data, *, stride, width, dtype, offset=0):
    """View complete attributes without copying or owning the source bytes.

    Callers supply their lazily imported NumPy module and retain ownership of
    the buffer. Value validation, vertex selection and output conversion belong
    to each attribute decoder; this helper only validates the record layout.
    """
    dtype = np.dtype(dtype)
    record_size = offset + width * dtype.itemsize
    if width <= 0 or stride < record_size or offset < 0:
        raise UnsupportedLayout
    count = max(0, 1 + (len(data) - record_size) // stride)
    if not count:
        return np.empty((0, width), dtype=dtype)
    return np.ndarray(
        (count, width), dtype=dtype, buffer=data, offset=offset,
        strides=(stride, dtype.itemsize))


def python_float_sum(np, columns):
    """Row-wise ``sum()`` of Float64 columns with Python 3.12+ rounding.

    ``sum()`` over floats uses Neumaier compensation and adds the compensation
    once at the end when it is finite and nonzero. Matching it lets array paths
    equal scalar decoders exactly.
    """
    total = np.asarray(columns[0], dtype=np.float64).copy()
    compensation = np.zeros_like(total)
    for column in columns[1:]:
        following = total + column
        compensation += np.where(
            np.abs(total) >= np.abs(column),
            (total - following) + column,
            (column - following) + total)
        total = following
    return np.where((compensation != 0) & np.isfinite(compensation),
                    total + compensation, total)
