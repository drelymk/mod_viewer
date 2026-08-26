"""Format-neutral direct Asset loading models."""

from dataclasses import dataclass, field
import os

from core.material_profiles import material_profile_for
from ..asset_textures import asset_texture_key


@dataclass(frozen=True, slots=True)
class AssetTexture:
    role: str | None
    path: str
    key: str
    label: str
    source: str = "explicit"
    uri: str | None = None

    def as_candidate(self):
        return {"tex_key": self.key, "file": self.key.split("/", 1)[-1],
                "label": self.label}


@dataclass(slots=True)
class AssetMeshPart:
    key: str
    label: str
    asset_type: str
    asset_path: str
    geometry_hash: str | None
    component_name: str | None
    classification: str | None
    component_ordinal: int | None
    first_index: int | None
    index_count: int | None
    positions: bytes
    indices: bytes
    uvs: bytes | None = None
    normals: bytes | None = None
    textures: dict[str, AssetTexture] = field(default_factory=dict)
    texture_candidates: tuple[AssetTexture, ...] = ()

    @property
    def asset_source(self):
        return {
            "type": self.asset_type,
            "asset": self.asset_path,
            "geometry_hash": self.geometry_hash,
            "component_name": self.component_name,
            "classification": self.classification,
            "component_ordinal": self.component_ordinal,
            "first_index": self.first_index,
            "index_count": self.index_count,
        }


class AssetLoadError(ValueError):
    def __init__(self, message):
        self.message = str(message)
        super().__init__(self.message)

    def __str__(self):
        return self.message


@dataclass(frozen=True, slots=True)
class AssetAdapterResult:
    parts: tuple["AssetMeshPart", ...]
    warnings: tuple[dict, ...] = ()


@dataclass(slots=True)
class AssetLoadResult:
    payload: dict
    parts: tuple[AssetMeshPart, ...]

    @classmethod
    def from_parts(cls, asset_type, root, record, parts, *, geometry,
                   warnings=()):
        payload = _build_asset_payload(
            asset_type, root, record, parts, geometry=geometry,
            warnings=warnings, asset_fill=False)
        return cls(payload, tuple(parts))


def _build_asset_payload(asset_type, root, record, parts, *, geometry,
                         warnings=(), asset_fill=False):
    game = {"GIMI": "genshin", "ZZMI": "zzz", "WWMI": "wuwa"}[asset_type]
    root_id = os.path.normcase(os.path.abspath(root))
    source = record.get("path", "") if isinstance(record, dict) else ""
    profile = material_profile_for(game)
    profiles = {profile.id: profile.to_metadata()}
    meshes = {}
    textures = {}
    pools = {}
    pool_ids = {}
    component_hashes = {}
    for part in parts:
        base = part.component_name or part.label
        component_hashes.setdefault(base, set()).add(part.geometry_hash)
    component_labels = {}
    for base, hashes in component_hashes.items():
        if len(hashes) <= 1:
            continue
        for geometry_hash in hashes:
            component_labels[(base, geometry_hash)] = (
                f"{base} [{geometry_hash or 'unknown'}]")
    for ordinal, part in enumerate(parts):
        mesh_key = (f"asset-fill::{part.key}" if asset_fill else part.key)
        for candidate in part.texture_candidates:
            # Candidate textures are published and selectable, but their
            # presence must not infer a semantic material role.
            textures[candidate.key] = candidate.uri or candidate.key
        base_component = part.component_name or part.label
        component = component_labels.get(
            (base_component, part.geometry_hash), base_component)
        entry = {
            "pos": geometry.add(part.positions),
            "idx": geometry.add(part.indices),
            "uv": geometry.add(part.uvs) if part.uvs else None,
            "normal": geometry.add(part.normals) if part.normals else None,
            "component": component,
            "display_name": part.label,
            "source": "ORIGINAL ASSET" if asset_fill else source,
            "conditions": [],
            "sources": [{"asset": part.asset_source}],
            "drawindexed": [part.index_count or 0, part.first_index or 0, 0],
            "tex_key": None,
            "normal_map_key": None,
            "normal_data_key": None,
            "light_map_key": None,
            "material_map_key": None,
            "texture_variants": [],
            "normal_map_variants": [],
            "normal_data_variants": [],
            "light_map_variants": [],
            "material_map_variants": [],
            "material_kind": "unknown",
            "material_kind_reliable": False,
            "material_kind_reason": "Asset preview has no mod material override.",
            "material_profile_id": profile.id,
            "asset_source": part.asset_source,
            "asset_fill": asset_fill,
            "fill_reason": ("missing_mod_coverage" if asset_fill
                            else None),
        }
        for role, texture in part.textures.items():
            textures[texture.key] = texture.uri or texture.key
            field = {
                "diffuse": "tex_key", "normal_map": "normal_map_key",
                "normal_data": "normal_data_key", "light_map": "light_map_key",
                "material_map": "material_map_key",
            }.get(role)
            if field:
                entry[field] = texture.key
        pool = []
        if part.texture_candidates:
            pool = [candidate.as_candidate()
                    for candidate in part.texture_candidates]
        pool.extend(texture.as_candidate() for texture in part.textures.values()
                    if texture.role == "diffuse")
        if pool:
            component = entry["component"]
            pool_id = pool_ids.get(component)
            if pool_id is None:
                pool_id = f"asset-pool-{len(pool_ids)}"
                pool_ids[component] = pool_id
                pools[pool_id] = []
            seen = {item.get("tex_key") for item in pools[pool_id]}
            for item in pool:
                key = item.get("tex_key")
                if key in seen:
                    continue
                pools[pool_id].append(item)
                seen.add(key)
            entry["texture_pool_id"] = pool_id
        meshes[mesh_key] = entry

    asset_metadata = {
        "source_kind": "asset-fill" if asset_fill else "asset",
        "game": {"id": game, "runtime": "asset",
                 "texture_api": asset_type.lower(),
                 "confidence": "authoritative"},
        "asset": {"type": asset_type, "path": source,
                  "root": root_id, "warnings": list(warnings)},
        "material_profiles": profiles,
    }
    if asset_fill:
        return {
            "meshes": meshes,
            "textures": textures,
            "texture_pools": pools,
            "geometry": None,
            "metadata": asset_metadata,
        }
    return {
        "meshes": meshes,
        "textures": textures,
        "texture_pools": pools,
        "controls": {"toggles": {}, "menu": {},
                     "present": {"target_inis": [], "item": None}},
        "state": {"rules": [], "defaults": {}},
        "geometry": None,
        "metadata": {**asset_metadata, "mesh_names": {}},
        "health": None,
        "asset_resolution": None,
    }


def build_asset_fill_payload(asset_type, root, record, parts, *, geometry,
                             warnings=()):
    """Build only the mesh data needed to append missing Asset parts."""
    return _build_asset_payload(
        asset_type, root, record, parts, geometry=geometry,
        warnings=warnings, asset_fill=True)


def make_texture(root, path, role, *, texture_source=None, source="explicit"):
    key = asset_texture_key(root, path, role or "diffuse")
    uri = texture_source(path, role) if texture_source else key
    if not uri:
        return None
    return AssetTexture(role, path, key,
                        os.path.splitext(os.path.basename(path))[0],
                        source, uri)


__all__ = [
    "AssetAdapterResult", "AssetLoadError", "AssetLoadResult",
    "AssetMeshPart", "AssetTexture", "build_asset_fill_payload",
    "make_texture",
]
