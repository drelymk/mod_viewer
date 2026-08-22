"""Focused contracts for mod-authored resource path sandboxing."""

import os

import pytest

from core.resource_paths import _within, safe_resource_path




def test_resource_path_allows_only_one_parent_level(tmp_path):
    root = str(tmp_path)
    assert safe_resource_path(root, os.path.join("..", "shared.dds")) == (
        os.path.realpath(os.path.abspath(os.path.join(root, "..", "shared.dds"))))
    assert safe_resource_path(
        root, os.path.join("..", "..", "outside.dds")) is None


def test_resource_path_rejects_absolute_drive_and_empty_values(tmp_path):
    root = str(tmp_path)
    assert safe_resource_path(root, "") is None
    assert safe_resource_path(root, os.path.abspath(os.path.join(
        root, "absolute.dds"))) is None
    assert safe_resource_path(root, r"C:\absolute\file.dds") is None
    assert safe_resource_path(root, r"C:/absolute/file.dds") is None
    assert safe_resource_path(root, r"\\server\share\file.dds") is None


def test_resource_path_commonpath_rejects_incompatible_drives():
    assert not _within(r"D:\Mods\Alice", r"C:\Mods")


def _make_symlink(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlink creation is unavailable: {error}")


def test_resource_path_canonicalizes_symlink_inside_boundary(tmp_path):
    root = tmp_path / "mod"
    textures = root / "textures"
    root.mkdir()
    textures.mkdir()
    link = root / "textures-link"
    _make_symlink(link, textures)

    expected = os.path.realpath(textures / "private.dds")
    assert safe_resource_path(str(root), "textures-link/private.dds") == expected


def test_resource_path_rejects_symlink_escape_from_mod(tmp_path):
    root = tmp_path / "mod"
    root.mkdir()
    link = root / "outside-link"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _make_symlink(link, outside)

    assert safe_resource_path(str(root), "outside-link/private.dds") is None


def test_resource_path_rejects_symlink_escape_from_allowed_parent(tmp_path):
    root = tmp_path / "mod"
    root.mkdir()
    link = tmp_path / "parent-link"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _make_symlink(link, outside)

    assert safe_resource_path(str(root), "../parent-link/private.dds") is None
