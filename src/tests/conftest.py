"""Shared pytest fixtures for app-layer filesystem tests."""

import pytest

from app.session import edit as edit_session


def pytest_addoption(parser):
    parser.addoption(
        "--test-shard", type=int, default=1,
        help="Run one deterministic shard of the collected tests.")
    parser.addoption(
        "--test-shard-count", type=int, default=1,
        help="Total number of deterministic test shards.")


def pytest_collection_modifyitems(config, items):
    shard_count = config.getoption("--test-shard-count")
    shard = config.getoption("--test-shard")
    if shard_count < 1 or not 1 <= shard <= shard_count:
        raise pytest.UsageError(
            "--test-shard must be between 1 and --test-shard-count")
    if shard_count == 1:
        return
    items[:] = [
        item for index, item in enumerate(items)
        if index % shard_count == shard - 1
    ]


@pytest.fixture
def api_root(tmp_path):
    """An isolated mod root whose staged edit-session state is always cleared."""
    root = str(tmp_path)
    yield root
    edit_session.discard(root)
