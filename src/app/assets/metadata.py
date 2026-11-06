"""Read-only conventions shared by native Asset metadata adapters."""

from core.textures import TEXTURE_ROLES


ROLE_LABELS = dict(zip(TEXTURE_ROLES, (
    "Diffuse", "NormalMap", "NormalData", "LightMap", "MaterialMap", "EmissionMap")))
_ROLES = {label.casefold(): role for role, label in ROLE_LABELS.items()}


def entries(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("objects", "components", "entries", "records"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def texture_role(label):
    return _ROLES.get(str(label).replace("_", "").replace(" ", "").casefold())


def authored_roles(entry, position):
    ranges = entry.get("texture_assignment_roles")
    roles = ranges.get(str(position)) if isinstance(ranges, dict) else None
    return tuple(role for role in roles if role in TEXTURE_ROLES) if isinstance(roles, list) else ()


def authored_range(entry, position):
    return bool(authored_roles(entry, position))


def authored_role(entry, position, role):
    return role in authored_roles(entry, position)
