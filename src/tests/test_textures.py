"""Focused contracts for the shared texture subsystem."""

import subprocess
import sys
from pathlib import Path

from core import textures


def test_texture_keys_keep_generic_and_field_owned_roles_distinct():
    assert textures.texture_key("foo.dds", "normal_data") == (
        "normal_data::foo.dds")
    assert textures.normalize_texture_key(
        "normal_data::foo.dds", "diffuse") == "normal_data::foo.dds"
    assert textures.texture_key_for_role(
        "normal_data::foo.dds", "diffuse") == "diffuse::foo.dds"


def test_texture_keys_normalize_legacy_paths_and_unknown_roles():
    assert textures.normalize_texture_key(r"Texture\Foo.dds") == (
        "diffuse::Texture/Foo.dds")
    assert textures.normalize_texture_role("not-a-role") == "diffuse"
    assert textures.normalize_texture_transform("not-a-transform") == (
        "passthrough")
    assert textures.normalize_texture_key("") is None


def test_texture_module_does_not_depend_on_mesh_builder():
    root = Path(__file__).resolve().parents[2]
    texture_result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.path.insert(0, 'src'); "
            "import core.textures; "
            "assert 'core.mesh_builder' not in sys.modules"
        )],
        cwd=root, check=False, capture_output=True, text=True)
    resource_result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.path.insert(0, 'src'); "
            "import core.resource_paths; "
            "assert 'core.mesh_builder' not in sys.modules; "
            "assert 'core.textures' not in sys.modules"
        )],
        cwd=root, check=False, capture_output=True, text=True)
    assert texture_result.returncode == 0, texture_result.stderr
    assert resource_result.returncode == 0, resource_result.stderr


def test_reset_texture_cache_clears_state():
    textures._texture_cache["test"] = (b"png", 3)
    textures._texture_cache_bytes = 3
    textures._texture_cache_mod = "test"
    textures.reset_texture_cache()
    assert not textures._texture_cache
    assert textures._texture_cache_bytes == 0
    assert textures._texture_cache_mod is None
