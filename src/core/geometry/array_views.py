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
