"""Small, read-only subset of 3DMigoto's INI section semantics.

Section and duplicate-key rules follow DirectX11/IniHandler.cpp. Unknown
constructs are deliberately left unclassified by diagnostics.
"""

import re


_COMMAND_PREFIXES = (
    "shaderoverride", "shaderregex", "textureoverride", "customshader",
    "commandlist", "builtincustomshader", "builtincommandlist",
)
_COMMAND_EXACT = {
    "present", "clearrendertargetview", "cleardepthstencilview",
    "clearunorderedaccessviewuint", "clearunorderedaccessviewfloat", "constants",
}
_REGULAR_PREFIXES = ("resource", "key", "preset", "include")
_REGULAR_EXACT = {
    "logging", "system", "device", "stereo", "rendering", "hunting",
    "profile", "convergencemap", "loader",
}
_PLAIN_VARIABLE = re.compile(r"^\$[A-Za-z_][A-Za-z_0-9]*$")
_DECLARATION = re.compile(
    r"^(?P<kind>global(?:\s+persist)?|local)\s+"
    r"(?P<name>\$[A-Za-z_][A-Za-z_0-9]*)(?:\s*=.*)?$",
    re.I,
)


def section_kind(name):
    name = name.casefold()
    if name in _COMMAND_EXACT or name.startswith(_COMMAND_PREFIXES):
        return "command"
    if name in _REGULAR_EXACT or name.startswith(_REGULAR_PREFIXES):
        return "regular"
    return None


def allows_bare_statements(name):
    name = name.casefold()
    return section_kind(name) == "command" or name == "profile"


def allows_duplicate_key(section_name, lhs):
    section_name, lhs = section_name.casefold(), lhs.casefold()
    return ((section_name.startswith("key") and lhs in {"key", "back"})
            or section_name == "include")


def override_hash_kind(name):
    name = name.casefold()
    if name.startswith("shaderoverride"):
        return "shader"
    if name.startswith("textureoverride"):
        return "texture"
    return None


_TEXTURE_MATCH_KEYS = {
    "match_type", "match_width", "match_height", "match_depth",
    "match_mips", "match_array", "match_format", "match_msaa",
    "match_usage", "match_bind_flags", "match_cpu_access_flags",
    "match_misc_flags", "match_byte_width", "match_stride",
    "match_msaa_quality",
}
_SHADER_OVERRIDE_METADATA = {
    "hash", "allow_duplicate_hash", "depth_filter", "partner", "model",
    "disable_scissor", "filter_index",
}
_TEXTURE_OVERRIDE_METADATA = _TEXTURE_MATCH_KEYS | {
    "hash", "stereomode", "format", "width", "height",
    "width_multiply", "height_multiply", "iteration", "filter_index",
    "expand_region_copy", "deny_cpu_read", "match_priority",
    "match_first_vertex", "match_first_index", "match_first_instance",
    "match_vertex_count", "match_index_count", "match_instance_count",
}
_CUSTOM_SHADER_METADATA = {
    "vs", "hs", "ds", "gs", "ps", "cs", "max_executions_per_frame",
    "flags", "blend", "alpha", "mask", "alpha_to_coverage",
    "sample_mask", "blend_state_merge", "depth_enable",
    "depth_write_mask", "depth_func", "stencil_enable",
    "stencil_read_mask", "stencil_write_mask", "stencil_front",
    "stencil_back", "stencil_ref", "depth_stencil_state_merge",
    "fill", "cull", "front", "depth_bias", "depth_bias_clamp",
    "slope_scaled_depth_bias", "depth_clip_enable", "scissor_enable",
    "multisample_enable", "antialiased_line_enable",
    "rasterizer_state_merge", "topology", "sampler",
} | {f"{name}[{index}]" for name in ("blend", "alpha", "mask")
     for index in range(8)} | {f"blend_factor[{index}]" for index in range(4)}


def is_texture_override_match_key(lhs):
    return lhs.casefold() in _TEXTURE_MATCH_KEYS


def unique_command_metadata(section_name, lhs):
    name, key = section_name.casefold(), lhs.casefold()
    if name.startswith("shaderoverride"):
        return key in _SHADER_OVERRIDE_METADATA
    if name.startswith("textureoverride"):
        return key in _TEXTURE_OVERRIDE_METADATA
    if name.startswith(("customshader", "builtincustomshader")):
        return key in _CUSTOM_SHADER_METADATA
    return False


def valid_override_hash(value, override_type):
    # %16llx accepts up to 16 characters, including an optional 0x prefix.
    return (len(value) <= 16 and
            bool(re.fullmatch(r"[+-]?(?:0[xX])?[0-9a-fA-F]+", value)))


def classify_run_target(value):
    lowered = value.casefold()
    if not value:
        return "invalid"
    if lowered.startswith(("builtincommandlist", "builtincustomshader")):
        return "builtin"
    if lowered.startswith(("commandlist", "customshader")):
        return "namespaced" if "\\" in value else "local"
    if lowered.startswith(("resource", "key", "preset", "textureoverride", "shaderoverride")):
        return "invalid"
    return "unknown"


def key_binding_kind(value):
    """Only an empty binding is provably invalid without the full key table."""
    return "invalid" if not value.strip() else "unknown"


def declaration(text):
    match = _DECLARATION.match(text)
    if match:
        return ("global" if match.group("kind").casefold().startswith("global") else "local",
                match.group("name").casefold())
    return None


def is_global_exact_section(name):
    name = name.casefold()
    return name in _COMMAND_EXACT or name in _REGULAR_EXACT


def plain_variable_assignment(lhs):
    return lhs.casefold() if _PLAIN_VARIABLE.fullmatch(lhs) else None
