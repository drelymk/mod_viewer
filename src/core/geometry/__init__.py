"""Geometry identities, buffer formats, draw calls, and mesh building."""

import os


# Geometry batches use array operations rather than BLAS. Set its worker limit
# before the lazy NumPy imports to avoid allocating unused worker stacks.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
