import pytest

from tools import benchmark_texture_pipeline as benchmark


@pytest.mark.parametrize(
    ("concurrency", "expected_median", "expected_min", "expected_max"),
    [(1, 1.5, 1.0, 2.0), (2, 3.0, 2.5, 3.5)],
)
def test_summarize_runs_reports_timing_statistics(
        concurrency, expected_median, expected_min, expected_max):
    runs = [
        {
            "concurrency": 1,
            "backend": {"api_load_seconds": 1.0},
            "browser": {"first_model_frame_seconds": 0.1},
        },
        {
            "concurrency": 1,
            "backend": {"api_load_seconds": 2.0},
            "browser": {"first_model_frame_seconds": 0.2},
        },
        {
            "concurrency": 2,
            "backend": {"api_load_seconds": 2.5},
            "browser": {"first_model_frame_seconds": 0.3},
        },
        {
            "concurrency": 2,
            "backend": {"api_load_seconds": 3.5},
            "browser": {"first_model_frame_seconds": 0.4},
        },
    ]

    summary = benchmark._summarize_runs(runs, concurrency)

    assert summary["repeats"] == 2
    assert summary["timings"]["backend.api_load_seconds"] == {
        "median": expected_median,
        "min": expected_min,
        "max": expected_max,
    }
