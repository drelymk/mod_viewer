"""Focused contracts for mod-authored resource path sandboxing."""

import os

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
