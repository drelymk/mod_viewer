"""Shared pytest fixtures for app-layer filesystem tests."""

import pytest

from app.session import edit as edit_session


@pytest.fixture
def api_root(tmp_path):
    """An isolated mod root whose staged edit-session state is always cleared."""
    root = str(tmp_path)
    yield root
    edit_session.discard(root)
