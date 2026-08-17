"""Shared helpers and fixtures for provenance-related test modules."""

import os
import struct

from core.mesh_builder import GeometryBlob, build_mesh_result, split_texture_key


def geometry_values(blob, reference):
    start = reference["offset"]
    end = start + reference["length"]
    raw = blob.data[start:end]
    return struct.unpack(f"<{len(raw) // 4}f", raw)


def texture_file(key):
    return split_texture_key(key)[1] if key else key


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def build_mesh_fixture(groups, mod_dir):
    geometry = GeometryBlob()
    result = build_mesh_result(groups, mod_dir, geometry=geometry)
    return result.meshes, geometry


def visible(conds, bindings):
    if conds == []:
        return True
    return any(all((bindings.get(c["var"]) == c["value"]) != c["negate"] for c in group)
               for group in conds)

DIFFUSE_NO_REF_INI = """[TextureOverrideXPosition]
vb0 = ResourceXPosition

[TextureOverrideXBlend]
vb1 = ResourceXBlend

[TextureOverrideXTexcoord]
vb1 = ResourceXTexcoord

[TextureOverrideXA]
ib = ResourceXAIB
Resource\\GIMI\\Diffuse = ResourceXDiffuse
run = CommandList\\GIMI\\SetTextures
drawindexed = 100, 0, 0

[ResourceXPosition]
filename = pos.buf
stride = 40

[ResourceXBlend]
filename = blend.buf
stride = 32

[ResourceXTexcoord]
filename = tc.buf
stride = 20

[ResourceXAIB]
filename = a.ib
format = DXGI_FORMAT_R32_UINT

[ResourceXDiffuse]
filename = diffuseX.dds
"""


IB_R16_INI = DIFFUSE_NO_REF_INI.replace("DXGI_FORMAT_R32_UINT", "DXGI_FORMAT_R16_UINT")

