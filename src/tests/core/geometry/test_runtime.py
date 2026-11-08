"""Geometry runtime initialization before lazy array decoding."""

import os
from pathlib import Path
import subprocess
import sys


def test_geometry_limits_blas_before_numpy_initializes():
    result = subprocess.run(
        [sys.executable, "-c", """
import os
import sys

from core.geometry import batch_packing, vertex_attributes

assert 'numpy' not in sys.modules
assert os.environ['OPENBLAS_NUM_THREADS'] == '1'

import numpy as np

source = vertex_attributes.VertexAttributeSource('normal.buf', 12, 0, 'f32x3')
data = np.tile(np.array([0., 0., 1.], dtype='<f4'), (4096, 1)).tobytes()
decoded = vertex_attributes.decode_normals(source, data, range(4096))
assert decoded == data
"""],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "OPENBLAS_NUM_THREADS": "64"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
