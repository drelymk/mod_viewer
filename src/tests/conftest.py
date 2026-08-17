"""Shared pytest fixtures for app-layer filesystem tests."""

from pathlib import Path
import shutil
import tempfile

import pytest

from app import edit_session


@pytest.fixture
def tmp_path():
    """Use a repo-local temp folder when the system temp root is restricted."""
    parent = Path(__file__).resolve().parents[2] / ".pytest_tmp"
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="pytest-", dir=parent))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


@pytest.fixture
def api_root(tmp_path):
    """An isolated mod root whose staged edit-session state is always cleared."""
    root = str(tmp_path)
    yield root
    edit_session.discard(root)
