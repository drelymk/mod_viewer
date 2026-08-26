"""Texture-role evidence and condition-aware assignment precedence."""

from dataclasses import dataclass, field, replace
import re

from ..geometry.identity import normalize_geometry_hash
from .dnf import DNF_TRUE, dnf_and, dnf_not, dnf_or, normalize_dnf, parse_condition_dnf


def _freeze_dnf(dnf):
    return tuple(
        tuple((clause["var"], clause["value"], bool(clause["negate"]))
              for clause in group)
        for group in (dnf or ()))


def _thaw_dnf(conditions):
    return [[{"var": var, "value": value, "negate": negate}
             for var, value, negate in group]
            for group in (conditions or ())]


@dataclass(frozen=True, slots=True)
class TextureReplacement:
    """One condition-aware original-hash to replacement-resource binding."""

    original_hash: str
    resource: str
    conditions: tuple = ()
    source_section: str = ""
    file: str | None = None

    @classmethod
    def from_dnf(cls, original_hash, resource, conditions, source_section):
        return cls(original_hash, resource, _freeze_dnf(conditions),
                   source_section)

    @property
    def dnf(self):
        return _thaw_dnf(self.conditions)


@dataclass(slots=True)
class TextureOverrideIndex:
    """Resource/hash and reverse hash/replacement views from one INI."""

    hashes_by_resource: dict = field(default_factory=dict)
    replacements_by_hash: dict = field(default_factory=dict)

    def with_resource_files(self, resources):
        lookup = {str(name).casefold(): info
                  for name, info in (resources or {}).items()}
        replacements = {}
        for texture_hash, items in self.replacements_by_hash.items():
            resolved = []
            for item in items:
                info = lookup.get(item.resource.casefold(), {})
                resolved.append(replace(
                    item, file=info.get("filename")))
            replacements[texture_hash] = tuple(resolved)
        return TextureOverrideIndex(
            hashes_by_resource=dict(self.hashes_by_resource),
            replacements_by_hash=replacements)


_AUX_MAP_CHANNELS = {
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}
_SEMANTIC_TEXTURE_ROLES = {
    "diffuse": "diffuse",
    "normalmap": "normal_map",
    "lightmap": "light_map",
    "materialmap": "material_map",
}
_SEMANTIC_TEXTURE_RESOURCE_RE = re.compile(
    r"^Resource[\\/]"
    r"(?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]"
    r"(?P<role>Diffuse|NormalMap|LightMap|MaterialMap)$", re.I)
_LEGACY_TEXTURE_RESOURCE_RE = re.compile(
    r"^Resource.+(?P<role>Diffuse|NormalMap|LightMap|MaterialMap)"
    r"(?P<variant>\.\d+)?$", re.I)


def _collect_texture_override_index(sections, toggle_vars, alias_map,
                                    var_prefix=None):
    """Index TextureOverride hashes and conditional ``this`` assignments."""
    hashes_by_resource = {}
    replacements_by_hash = {}
    for section, lines in sections.items():
        if not str(section).lower().startswith("textureoverride"):
            continue
        hashes = set()
        cond_stack = []
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            match = re.match(r"(?:else\s+if|elif)\s+(.*)$", line, re.I)
            if match:
                if cond_stack:
                    frame = cond_stack[-1]
                    branch = parse_condition_dnf(
                        match.group(1).strip(), alias_map)
                    frame["cur"] = dnf_and(
                        dnf_not(frame["seen"]), branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                continue
            if line.lower().startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), alias_map)
                cond_stack.append({"cur": branch, "seen": branch})
                continue
            if line.lower() == "else":
                if cond_stack:
                    cond_stack[-1]["cur"] = dnf_not(
                        cond_stack[-1]["seen"])
                continue
            if line.lower() == "endif":
                if cond_stack:
                    cond_stack.pop()
                continue
            match = re.match(r"hash\s*=\s*(\S+)", line, re.I)
            if match:
                texture_hash = normalize_geometry_hash(match.group(1))
                if texture_hash:
                    hashes.add(texture_hash)
                continue
            match = re.match(r"this\s*=\s*(?:ref\s+)?(\S+)", line, re.I)
            if not match or not hashes:
                continue
            combined = DNF_TRUE
            for frame in cond_stack:
                combined = dnf_and(combined, frame["cur"])
            conditions = normalize_dnf(combined, toggle_vars, var_prefix)
            resource = match.group(1)
            resource_key = resource.casefold()
            hashes_by_resource.setdefault(resource_key, set()).update(hashes)
            for texture_hash in hashes:
                replacement = TextureReplacement.from_dnf(
                    texture_hash, resource, conditions, str(section))
                replacements_by_hash.setdefault(texture_hash, []).append(
                    replacement)
    return TextureOverrideIndex(
        hashes_by_resource={
            resource: tuple(sorted(hashes))
            for resource, hashes in hashes_by_resource.items()},
        replacements_by_hash={
            texture_hash: tuple(items)
            for texture_hash, items in replacements_by_hash.items()})


def _semantic_texture_role(resource):
    """Return a role only for a framework-owned semantic resource name."""
    match = _SEMANTIC_TEXTURE_RESOURCE_RE.fullmatch(str(resource or ""))
    return (_SEMANTIC_TEXTURE_ROLES[match.group("role").casefold()]
            if match else None)


def _legacy_texture_evidence(resource, sections, section_lookup):
    """Return ``(role, family)`` for a declared legacy texture resource."""
    match = _LEGACY_TEXTURE_RESOURCE_RE.fullmatch(str(resource or ""))
    if not match or _semantic_texture_role(resource):
        return None
    section_name = section_lookup.get(str(resource).casefold())
    if section_name is None:
        return None
    if not any(re.match(r"^filename\s*=\s*\S+", raw, re.I)
               for raw in sections[section_name]):
        return None
    role = _SEMANTIC_TEXTURE_ROLES[match.group("role").casefold()]
    family = str(resource)
    if match.group("variant"):
        family = family[:-len(match.group("variant"))]
    return role, family.casefold()


def _legacy_texture_role(resource, sections, section_lookup):
    """Compatibility view returning only the constrained legacy role."""
    evidence = _legacy_texture_evidence(resource, sections, section_lookup)
    return evidence[0] if evidence else None


def _collect_structural_slot_role_hints(sections):
    """Collect unambiguous ``ps-tN`` roles from semantic resource markers."""
    observations = {}
    for lines in sections.values():
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            slot = re.match(
                r"^ps-t(?P<slot>\d+)\s*=\s*(?:ref\s+)?(?P<resource>\S+)",
                line, re.I)
            if not slot:
                continue
            role = _semantic_texture_role(slot.group("resource"))
            if role:
                observations.setdefault(int(slot.group("slot")), set()).add(role)
    return {slot: next(iter(roles)) for slot, roles in observations.items()
            if len(roles) == 1}


_collect_slot_role_hints = _collect_structural_slot_role_hints


def _condition_group_is_consistent(group):
    """Return whether one DNF conjunction can be satisfied."""
    equal = {}
    not_equal = set()
    for clause in group:
        key = clause["var"]
        value = clause["value"]
        if clause["negate"]:
            not_equal.add((key, value))
            if equal.get(key) == value:
                return False
        else:
            previous = equal.setdefault(key, value)
            if previous != value or (key, value) in not_equal:
                return False
    return True


def _condition_difference(condition, excluded):
    """Return ``condition AND NOT excluded`` in the parser's DNF form."""
    left = condition or DNF_TRUE
    right = excluded or DNF_TRUE
    result = [group for group in dnf_and(left, dnf_not(right))
              if _condition_group_is_consistent(group)]
    if not result:
        return None
    if any(not group for group in result):
        return []
    return result


_TEXTURE_SOURCE_PRIORITY = {
    "semantic": 30,
    "slot": 20,
    "legacy_slot": 10,
}


def _effective_role_assignments(assignments):
    """Apply semantic, structural-slot, then legacy-slot precedence."""
    result = []
    for item in assignments:
        priority = _TEXTURE_SOURCE_PRIORITY.get(item.get("source"), 0)
        higher = [candidate for candidate in assignments
                  if _TEXTURE_SOURCE_PRIORITY.get(
                      candidate.get("source"), 0) > priority]
        if not higher:
            result.append(item)
            continue
        condition = item.get("cond") or []
        for candidate in higher:
            condition = _condition_difference(
                condition, candidate.get("cond") or [])
            if condition is None:
                break
        if condition is not None:
            result.append({**item, "cond": condition})
    return result


__all__ = [
    "TextureReplacement", "TextureOverrideIndex",
]
