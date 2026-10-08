"""Texture identity, source publication, and CPU image decoding."""

import io
import os
import struct
import warnings

from ..resource_paths import _canonical, safe_resource_path


_MAX_IMAGE_PIXELS = 100_000_000
TEXTURE_ROLES = (
    "diffuse", "normal_map", "normal_data", "light_map", "material_map",
    "emission_map")


_SRGB_DXGI_TO_UNORM = {
    72: 71,  # BC1
    75: 74,  # BC2
    78: 77,  # BC3
    99: 98,  # BC7
}


def normalize_texture_role(role=None):
    """Return the canonical registry role used for one texture instance."""
    return role if role in TEXTURE_ROLES else "diffuse"


def texture_key(relative_path, role=None):
    """Identify a rendered texture by source path *and* usage role."""
    role = normalize_texture_role(role)
    relative_path = str(relative_path or "").replace("\\", "/")
    return f"{role}::{relative_path}"


def split_texture_key(key, default_role=None):
    """Return ``(role, relative_path)`` for new or legacy texture keys."""
    value = str(key or "")
    prefix, separator, relative_path = value.partition("::")
    if separator and prefix in TEXTURE_ROLES and relative_path:
        return prefix, relative_path
    return normalize_texture_role(default_role), value.replace("\\", "/")


def normalize_texture_key(key, default_role=None):
    """Canonicalize a new or legacy key without touching its source path."""
    role, relative_path = split_texture_key(key, default_role)
    return texture_key(relative_path, role) if relative_path else None


def texture_key_for_role(value, role):
    """Canonicalize a path/key while forcing the caller-owned semantic role."""
    if not value:
        return None
    _old_role, relative_path = split_texture_key(value, role)
    return texture_key(relative_path, role)


def _srgb_dds_as_unorm(data):
    """Return a memory-only DDS header with a typed sRGB format normalized."""
    if (len(data) < 148 or data[:4] != b"DDS "
            or data[84:88] != b"DX10"):
        return None
    dxgi_format = struct.unpack_from("<I", data, 128)[0]
    unorm_format = _SRGB_DXGI_TO_UNORM.get(dxgi_format)
    if unorm_format is None:
        return None
    rewritten = bytearray(data)
    struct.pack_into("<I", rewritten, 128, unorm_format)
    return bytes(rewritten)


def _open_texture_image(path, image_module, source_name=None):
    """Open a texture, retrying typed sRGB DDS files as unorm in memory."""
    try:
        image = image_module.open(
            io.BytesIO(bytes(path)) if isinstance(
                path, (bytes, bytearray, memoryview)) else path)
        image.load()
        return image
    except Exception:
        try:
            filename = (source_name if isinstance(source_name, str)
                        else os.fspath(path))
        except TypeError:
            return None
        if not filename.casefold().endswith(".dds"):
            return None
        try:
            if isinstance(path, (bytes, bytearray, memoryview)):
                data = bytes(path)
                header = data[:148]
            else:
                with open(path, "rb") as stream:
                    header = stream.read(148)
                    data = header + stream.read()
            rewritten_header = _srgb_dds_as_unorm(header)
            if rewritten_header is None:
                return None
            if len(data) < 148:
                return None
            data = bytearray(data)
            data[128:132] = rewritten_header[128:132]
            image = image_module.open(io.BytesIO(data))
            image.load()
            return image
        except Exception:
            return None


def _decode_texture_image(path, max_size=None, preserve_alpha=False,
                          source_name=None):
    """Decode a texture with the shared DDS and decompression safeguards."""
    try:
        if max_size is not None:
            max_size = int(max_size)
            if max_size <= 0:
                return None
    except (TypeError, ValueError):
        return None
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = (_open_texture_image(path, Image)
                     if source_name is None
                     else _open_texture_image(
                         path, Image, source_name=source_name))
            if image is None:
                return None
        image = image.convert("RGBA" if preserve_alpha else "RGB")
        if max_size is not None and max(image.size) > max_size:
            image.thumbnail((max_size, max_size), Image.LANCZOS)
        return image
    except Exception:
        return None


def load_texture_image(path, max_size=2048, preserve_alpha=False,
                       source_name=None):
    """Decode a texture once and return its bounded Pillow image."""
    return _decode_texture_image(
        path, max_size, preserve_alpha, source_name=source_name)


def load_texture_image_full(path, preserve_alpha=True, source_name=None):
    """Decode a texture at its authored dimensions without preview resizing."""
    return _decode_texture_image(
        path, None, preserve_alpha, source_name=source_name)


def encode_texture_file(mod_dir, abs_path, texture_role=None,
                        texture_source=None, source=None):
    """Resolve a picked file into ``{tex_key, file, role, uri}``."""
    texture_role = normalize_texture_role(texture_role)
    if source is not None and source.is_resource_reference(abs_path):
        resolved = abs_path
        rel = source.logical_path(abs_path)
        exists = source.is_file(resolved)
    else:
        try:
            rel = os.path.relpath(abs_path, mod_dir)
        except ValueError:
            return {"error": "Selected file is not inside the mod folder."}
        resolved = safe_resource_path(mod_dir, rel)
        selected = _canonical(abs_path)
        if (not resolved or _canonical(resolved) != selected):
            return {"error": "Selected file is not inside the mod folder."}
        exists = os.path.isfile(abs_path)
    if not exists:
        return {"error": "Selected file does not exist."}
    uri = texture_source(resolved, texture_role) if texture_source else None
    if not uri:
        return {"error": "Could not read this file as an image.",
                "error_code": "texture_load_failed",
                "file": str(rel).replace("\\", "/")}
    relative_path = str(rel).replace("\\", "/")
    return {"tex_key": texture_key(relative_path, texture_role),
            "file": relative_path, "role": texture_role, "uri": uri}


def encode_texture_key(mod_dir, key, texture_role=None, texture_source=None,
                       source=None):
    """Encode a role-aware registry key, accepting legacy path-only keys."""
    role, relative_path = split_texture_key(key, texture_role)
    resolved = (source.resolve_resource(relative_path)
                if source is not None
                else safe_resource_path(mod_dir, relative_path))
    if not resolved:
        return {"error": "Selected file is not inside the mod folder."}
    return encode_texture_file(
        mod_dir, resolved, role, texture_source=texture_source,
        source=source)
