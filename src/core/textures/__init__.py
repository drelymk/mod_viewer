"""Texture decoding and role-aware texture identity API."""

from .pipeline import (
    TEXTURE_ROLES,
    TEXTURE_TRANSFORMS,
    encode_texture_data_uri,
    encode_texture_file,
    encode_texture_key,
    load_texture_image,
    normalize_texture_key,
    normalize_texture_role,
    normalize_texture_transform,
    render_texture_png,
    reset_texture_cache,
    set_texture_profile_hook,
    split_texture_key,
    texture_key,
    texture_key_for_role,
)

__all__ = [
    "TEXTURE_ROLES",
    "TEXTURE_TRANSFORMS",
    "encode_texture_data_uri",
    "encode_texture_file",
    "encode_texture_key",
    "load_texture_image",
    "normalize_texture_key",
    "normalize_texture_role",
    "normalize_texture_transform",
    "render_texture_png",
    "reset_texture_cache",
    "set_texture_profile_hook",
    "split_texture_key",
    "texture_key",
    "texture_key_for_role",
]
