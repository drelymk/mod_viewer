"""Bounded geometry-buffer access regressions."""

from unittest.mock import patch

import pytest

from core.geometry import buffers


def test_buffer_store_reads_shared_file_once(tmp_path):
    path = tmp_path / "shared.buf"
    path.write_bytes(b"shared")
    store = buffers.BufferStore()

    with patch("builtins.open", wraps=open) as reader:
        assert store.raw(str(path)) == b"shared"
        assert store.raw(str(path)) == b"shared"

    assert reader.call_count == 1


def test_buffer_store_preserves_single_file_limit(tmp_path, monkeypatch):
    path = tmp_path / "large.buf"
    path.write_bytes(b"12345")
    monkeypatch.setattr(buffers, "_MAX_BUFFER_FILE_BYTES", 4)

    with pytest.raises(ValueError, match="Buffer file is too large"):
        buffers.BufferStore().raw(str(path))


def test_buffer_store_preserves_cumulative_limit_but_not_shared_reads(
        tmp_path, monkeypatch):
    first = tmp_path / "first.buf"
    second = tmp_path / "second.buf"
    first.write_bytes(b"123")
    second.write_bytes(b"456")
    monkeypatch.setattr(buffers, "_MAX_TOTAL_BUFFER_BYTES", 5)
    store = buffers.BufferStore()

    store.raw(str(first))
    store.raw(str(first))
    with pytest.raises(ValueError, match="2 GiB safety limit"):
        store.raw(str(second))
