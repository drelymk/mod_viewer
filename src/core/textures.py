"""Texture identity and fallback image processing."""

import base64
import io
import os
import threading
import time
import warnings
from collections import OrderedDict

from .resource_paths import safe_resource_path


_TEXTURE_CACHE_LIMIT = 256 * 1024 * 1024
_MAX_IMAGE_PIXELS = 100_000_000
TEXTURE_ROLES = (
    "diffuse", "normal_map", "normal_data", "light_map", "material_map")
TEXTURE_TRANSFORMS = (
    "passthrough", "normal_xy_reconstruct",
)
_texture_cache = OrderedDict()
_texture_cache_bytes = 0
_texture_cache_mod = None
_texture_cache_lock = threading.RLock()
_texture_profile_hook = None


def normalize_texture_role(role=None):
    """Return the canonical registry role used for one texture instance."""
    return role if role in TEXTURE_ROLES else "diffuse"


def normalize_texture_transform(transform=None):
    """Return a known image transform, defaulting to raw packed data."""
    return transform if transform in TEXTURE_TRANSFORMS else "passthrough"


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


def set_texture_profile_hook(hook):
    """Install an optional texture-stage callback and return the previous one."""
    global _texture_profile_hook
    previous = _texture_profile_hook
    _texture_profile_hook = hook
    return previous


def _profile_texture(stage, seconds=0.0, **details):
    hook = _texture_profile_hook
    if hook is None:
        return
    try:
        hook(stage, seconds, details)
    except Exception:
        pass


def _profile_started():
    return time.perf_counter() if _texture_profile_hook is not None else None


def _profile_elapsed(stage, started, **details):
    if started is not None:
        _profile_texture(stage, time.perf_counter() - started, **details)


def _begin_texture_cache(mod_dir):
    global _texture_cache_mod, _texture_cache_bytes
    root = os.path.normcase(os.path.abspath(mod_dir))
    with _texture_cache_lock:
        if root != _texture_cache_mod:
            _texture_cache.clear()
            _texture_cache_bytes = 0
            _texture_cache_mod = root


def reset_texture_cache():
    """Clear the process-local rendered-PNG cache."""
    global _texture_cache_bytes, _texture_cache_mod
    with _texture_cache_lock:
        _texture_cache.clear()
        _texture_cache_bytes = 0
        _texture_cache_mod = None


def _cache_texture(key, png):
    global _texture_cache_bytes
    size = len(png)
    if size > _TEXTURE_CACHE_LIMIT:
        return
    with _texture_cache_lock:
        previous = _texture_cache.pop(key, None)
        if previous is not None:
            _texture_cache_bytes -= previous[1]
        _texture_cache[key] = (png, size)
        _texture_cache_bytes += size
        while _texture_cache_bytes > _TEXTURE_CACHE_LIMIT:
            _old_key, (_old_png, old_size) = _texture_cache.popitem(last=False)
            _texture_cache_bytes -= old_size


def _reconstruct_normal_z(img):
    """Expand a game-style two-channel XY normal into tangent-space RGB."""
    source = img.convert("RGB").tobytes()
    result = bytearray(len(source))
    for offset in range(0, len(source), 3):
        x = source[offset] / 127.5 - 1.0
        y = source[offset + 1] / 127.5 - 1.0
        z = max(0.0, 1.0 - x * x - y * y) ** 0.5
        result[offset] = source[offset]
        result[offset + 1] = source[offset + 1]
        result[offset + 2] = round((z * 0.5 + 0.5) * 255.0)
    from PIL import Image
    return Image.frombytes("RGB", img.size, bytes(result))


def _apply_texture_transform(img, texture_transform):
    texture_transform = normalize_texture_transform(texture_transform)
    if texture_transform == "normal_xy_reconstruct":
        return _reconstruct_normal_z(img)
    return img


def render_texture_png(path, max_size=2048, preserve_alpha=False,
                       texture_role=None, texture_transform="passthrough"):
    """Decode and explicitly transform an image into PNG bytes."""
    try:
        texture_role = normalize_texture_role(texture_role)
        texture_transform = normalize_texture_transform(texture_transform)
        stat = os.stat(path)
        cache_key = (os.path.normcase(os.path.abspath(path)), stat.st_size,
                     stat.st_mtime_ns, max_size, preserve_alpha, texture_role,
                     texture_transform)
        cache_started = _profile_started()
        with _texture_cache_lock:
            cached = _texture_cache.pop(cache_key, None)
            if cached is not None:
                _texture_cache[cache_key] = cached
                _profile_elapsed(
                    "cache_hit", cache_started,
                    path=cache_key[0], role=texture_role,
                    transform=texture_transform, bytes=len(cached[0]))
                return cached[0]
        _profile_elapsed("cache_miss", cache_started,
                         path=cache_key[0], role=texture_role,
                         transform=texture_transform)
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        stage_started = _profile_started()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(path)
            img.load()
        _profile_elapsed("decode", stage_started,
                         path=cache_key[0], role=texture_role,
                         transform=texture_transform)
        packed_passthrough = (
            texture_transform == "passthrough"
            and texture_role != "diffuse")
        keep_source_alpha = preserve_alpha or packed_passthrough
        stage_started = _profile_started()
        try:
            img = img.convert('RGBA' if keep_source_alpha else 'RGB')
            if preserve_alpha and img.getchannel('A').getextrema()[1] == 0:
                return None
        finally:
            _profile_elapsed(
                "rgb_rgba_conversion", stage_started,
                path=cache_key[0], role=texture_role,
                transform=texture_transform)
        if max(img.size) > max_size:
            stage_started = _profile_started()
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            _profile_elapsed(
                "resize", stage_started,
                path=cache_key[0], role=texture_role,
                transform=texture_transform)
        if texture_transform != "passthrough":
            stage_started = _profile_started()
            try:
                img = _apply_texture_transform(img, texture_transform)
            finally:
                _profile_elapsed(
                    "normal_z_reconstruction",
                    stage_started,
                    path=cache_key[0], role=texture_role,
                    transform=texture_transform)
        stage_started = _profile_started()
        try:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
        finally:
            _profile_elapsed(
                "png_encoding", stage_started,
                path=cache_key[0], role=texture_role,
                transform=texture_transform,
                bytes=len(png) if "png" in locals() else 0)
        _cache_texture(cache_key, png)
        _profile_texture(
            "encoded", 0.0, path=cache_key[0], role=texture_role,
            transform=texture_transform,
            bytes=len(png))
        return png
    except Exception as error:
        print(f"  texture skipped: {error}")
        return None


def encode_texture_data_uri(path, max_size=2048, preserve_alpha=False,
                            texture_role=None,
                            texture_transform="passthrough"):
    """Return the historical base64 data URI compatibility representation."""
    png = render_texture_png(
        path, max_size=max_size, preserve_alpha=preserve_alpha,
        texture_role=texture_role, texture_transform=texture_transform)
    if png is None:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _texture_source_uri(texture_source, path, role, transform):
    """Call old two-argument and new transform-aware source callbacks."""
    if texture_source is None:
        return None
    try:
        return texture_source(path, role, transform=transform)
    except TypeError as first_error:
        try:
            return texture_source(path, role)
        except TypeError:
            raise first_error


def encode_texture_file(mod_dir, abs_path, texture_role=None,
                        texture_source=None, texture_profile=None,
                        texture_transform=None):
    """Resolve a picked file into ``{tex_key, file, role, uri}``."""
    texture_role = normalize_texture_role(texture_role)
    if texture_transform is None and (texture_source is None
                                      or texture_profile is not None):
        from .texture_profiles import texture_profile_for
        texture_transform = texture_profile_for(texture_profile).recipe_for(
            texture_role)
    if texture_transform is not None:
        texture_transform = normalize_texture_transform(texture_transform)
    _begin_texture_cache(mod_dir)
    try:
        rel = os.path.relpath(abs_path, mod_dir)
    except ValueError:
        return {"error": "Selected file is not inside the mod folder."}
    resolved = safe_resource_path(mod_dir, rel)
    selected = os.path.abspath(abs_path)
    if (not resolved
            or os.path.normcase(os.path.normpath(resolved))
            != os.path.normcase(os.path.normpath(selected))):
        return {"error": "Selected file is not inside the mod folder."}
    if not os.path.isfile(abs_path):
        return {"error": "Selected file does not exist."}
    if texture_source is None:
        uri = encode_texture_data_uri(
            abs_path, texture_role=texture_role,
            texture_transform=texture_transform)
    else:
        uri = _texture_source_uri(
            texture_source, abs_path, texture_role, texture_transform)
    if not uri:
        return {"error": "Could not read this file as an image."}
    relative_path = rel.replace(os.sep, "/")
    return {"tex_key": texture_key(relative_path, texture_role),
            "file": relative_path, "role": texture_role, "uri": uri}


def encode_texture_key(mod_dir, key, texture_role=None, texture_source=None,
                       texture_profile=None, texture_transform=None):
    """Encode a role-aware registry key, accepting legacy path-only keys."""
    role, relative_path = split_texture_key(key, texture_role)
    resolved = safe_resource_path(mod_dir, relative_path)
    if not resolved:
        return {"error": "Selected file is not inside the mod folder."}
    return encode_texture_file(
        mod_dir, resolved, role, texture_source=texture_source,
        texture_profile=texture_profile, texture_transform=texture_transform)
