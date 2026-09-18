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
_LOCAL_RUN = re.compile(r"^(?:commandlist|customshader)[\w.-]+$", re.I)
_NAMESPACED_RUN = re.compile(
    r"^(?:commandlist|customshader)\\[^\s\\]+(?:\\[^\s\\]+)+$", re.I)
_BUILTIN_RUN = re.compile(r"^builtin(?:commandlist|customshader)[\w.-]+$", re.I)
_PLAIN_VARIABLE = re.compile(r"^\$[A-Za-z_][A-Za-z_0-9]*$")
_DECLARATION = re.compile(
    r"^(?:(?:global\s+(?:persist\s+)?|local\s+))(?P<name>\$[A-Za-z_][A-Za-z_0-9]*)\s*=",
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
}


def is_texture_override_match_key(lhs):
    return lhs.casefold() in _TEXTURE_MATCH_KEYS


def may_be_texture_override_match_key(lhs):
    # Newer forks may add match options. An unrecognized match_* keeps the
    # missing-hash diagnosis uncertain, but is not itself declared valid.
    return lhs.casefold().startswith("match_")


def unique_command_metadata(section_name, lhs):
    override_type = override_hash_kind(section_name)
    return bool(override_type and (lhs.casefold() == "hash" or
                (override_type == "texture" and
                 is_texture_override_match_key(lhs))))


def valid_override_hash(value, override_type):
    # Shader hash method may use either the traditional 64-bit value or CRC32C.
    widths = (8,) if override_type == "texture" else (8, 16)
    return any(re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{" + str(width) + r"}", value)
               for width in widths)


def classify_run_target(value):
    if _LOCAL_RUN.fullmatch(value):
        return "local"
    if _NAMESPACED_RUN.fullmatch(value):
        return "namespaced"
    if _BUILTIN_RUN.fullmatch(value):
        return "builtin"
    if not value or re.fullmatch(r"(?:resource|key|preset|textureoverride|shaderoverride)[\w.-]+", value, re.I):
        return "invalid"
    if re.search(r"\s", value) or value.startswith("\\"):
        return "invalid"
    return "unknown"


def key_binding_kind(value):
    """Only reject an empty binding or modifiers without a key token."""
    tokens = [token.casefold().replace("-", "_") for token in value.split()]
    modifiers = {"ctrl", "control", "shift", "alt", "no_modifiers",
                 "no_ctrl", "no_control", "no_shift", "no_alt"}
    return "invalid" if not tokens or all(t in modifiers for t in tokens) else "unknown"


def declaration(text):
    match = _DECLARATION.match(text)
    if match:
        return ("global" if text.casefold().startswith("global") else "local",
                match.group("name").casefold())
    return None


def plain_variable_assignment(lhs):
    return lhs.casefold() if _PLAIN_VARIABLE.fullmatch(lhs) else None
