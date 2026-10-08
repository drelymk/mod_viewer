import pytest

from tools import benchmark_texture_pipeline as benchmark


@pytest.mark.parametrize(
    ("times", "expected_median", "expected_min", "expected_max"),
    [([1.0, 2.0], 1.5, 1.0, 2.0), ([2.5, 3.5], 3.0, 2.5, 3.5)],
)
def test_summarize_runs_reports_timing_statistics(
        times, expected_median, expected_min, expected_max):
    runs = [{"backend": {"api_load_seconds": seconds}} for seconds in times]
    summary = benchmark._summarize_runs(runs)
    assert summary["repeats"] == 2
    assert summary["timings"]["backend.api_load_seconds"] == {
        "median": expected_median, "min": expected_min, "max": expected_max,
    }


def test_summarize_runs_reports_geometry_packing_substages():
    run = {
        "backend": {
            "pack_draw_geometry_seconds": 10.0,
            "prepare_draw_vertices_seconds": 4.0,
            "index_decode_seconds": 1.0,
            "decode_normals_seconds": 2.0,
            "build_shape_buffers_seconds": 1.5,
            "pack_other_seconds": 2.5,
        },
    }

    summary = benchmark._summarize_runs([run])

    for field, value in {
        "backend.pack_draw_geometry_seconds": 10.0,
        "backend.prepare_draw_vertices_seconds": 4.0,
        "backend.index_decode_seconds": 1.0,
        "backend.decode_normals_seconds": 2.0,
        "backend.build_shape_buffers_seconds": 1.5,
        "backend.pack_other_seconds": 2.5,
    }.items():
        assert summary["timings"][field] == {
            "median": value,
            "min": value,
            "max": value,
        }
