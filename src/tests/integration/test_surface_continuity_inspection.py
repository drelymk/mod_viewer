"""Read-only topology diagnostics distinguish seams from attachment claims."""

from pathlib import Path
import runpy

import numpy as np
import pytest

from tests.support.skinning_data import write_wwmi_remap_mod


INSPECTOR = runpy.run_path(str(
    Path(__file__).resolve().parents[3] / 'tools' / 'inspect_surface_continuity.py'))


def test_surface_seams_point_contacts_and_overlays_keep_distinct_evidence():
    positions = np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
        [1, 0, 0], [0, 1, 0], [1, 1, 0],
        [2, 0, 0], [3, 0, 0], [2, 1, 0],
        [1, 1, 0], [2, 1, 0], [2, 2, 0],
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
    ], dtype=np.float32)
    triangles = np.arange(15).reshape(-1, 3)
    indices = np.array([[7]] * 12 + [[8]] * 3, dtype=np.uint32)
    weights = np.ones((15, 1), dtype=np.float32)
    uv = np.arange(30).reshape(-1, 2)
    baseline = [value.copy() for value in (positions, triangles, indices, weights, uv)]
    result = INSPECTOR['inspect_mesh'](
        positions, triangles, indices, weights, [(0, 1), (1, 3)], uv)
    assert result['indexed_islands'] == 5
    assert result['exact_position_islands'] == 1
    assert result['exact_edge_islands'] == 3
    assert result['authored_compatible_edge_islands'] == 4
    assert result['duplicate_position_triangles'] == 1
    edge, seam = result['probes']
    assert edge['indexed_edge'] and edge['same_indexed_island']
    assert not seam['indexed_edge'] and not seam['same_indexed_island']
    assert seam['same_compatible_edge_island']
    assert edge['authored_overlap'] == 1
    assert edge['influence_island_support'][7]['compatible_edge_islands'] == 3
    for before, after in zip(baseline, (positions, triangles, indices, weights, uv)):
        np.testing.assert_array_equal(before, after)
    weights[[3, 4]] *= 0.5
    scaled = INSPECTOR['inspect_mesh'](positions, triangles, indices, weights, [(1, 3)], uv)
    assert scaled['probes'][0]['authored_overlap'] == 1
    assert scaled['probes'][0]['authored_weight_sums'] == [1, 0.5]
    assert not scaled['probes'][0]['same_compatible_edge_island']
    assert scaled['authored_compatible_edge_islands'] == 5
    with pytest.raises(ValueError, match='selected triangles'):
        INSPECTOR['inspect_mesh'](positions, triangles, indices, weights, [(0, 99)])


def test_inspection_uses_compact_vertices_and_resolved_vertex_vg_ids(tmp_path):
    write_wwmi_remap_mod(tmp_path)
    # Give the decoded fixture positive-area surface evidence.
    (tmp_path / 'component01.pos').write_bytes(np.array([
        [0, 0, 0], [1, 0, 0], [0, 1, 0],
    ], dtype='<f4').tobytes())
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = INSPECTOR['load_inspection'](tmp_path, 'Component01Blend-1', [(0, 1)])
    assert result['authored_vertices'] == [[0, 1]]
    assert result['source']['bone_ids_model_wide'] is True
    assert result['source']['vertex_vg_source'] == 'component01.vertex_vg'
    assert result['probes'][0]['authored_overlap'] == 1
    assert set(result['probes'][0]['authored'][0]) == {3, 259}
    assert result['indexed_islands'] == 1
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
