"""Public mod-loading orchestration and payload regressions."""

import os
import struct
import tempfile

from app.mods.loader import load_mod


def test_nested_ini_resources_are_relative_to_their_ini():
    ini = """[TextureOverride{0}Position]
vb0 = Resource{0}Position
[TextureOverride{0}Texcoord]
vb1 = Resource{0}Texcoord
[TextureOverride{0}]
ib = Resource{0}IB
drawindexed = 3,0,0
[Resource{0}Position]
filename = p.buf
stride = 12
[Resource{0}Texcoord]
filename = t.buf
stride = 8
[Resource{0}IB]
filename = i.buf
format = R32_UINT
"""
    with tempfile.TemporaryDirectory() as root:
        for relative, name in (("", "Root"), ("nested", "Nested")):
            folder = os.path.join(root, relative)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{name.lower()}.ini"), "w",
                      encoding="utf-8") as stream:
                stream.write(ini.format(name))
            with open(os.path.join(folder, "i.buf"), "wb") as stream:
                stream.write(struct.pack("<3I", 0, 1, 2))
            with open(os.path.join(folder, "p.buf"), "wb") as stream:
                stream.write(struct.pack(
                    "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
            with open(os.path.join(folder, "t.buf"), "wb") as stream:
                stream.write(struct.pack("<6f", 0, 0, 1, 0, 0, 1))

        payload = load_mod(root)
        meshes = list(payload.get("meshes", {}).values())

        assert not payload.get("error")
        assert len(meshes) == 2
        assert {mesh.get("source") for mesh in meshes} == {"root", "nested"}
        source_inis = {
            source.get("ini")
            for mesh in meshes
            for source in mesh.get("sources", [])
        }
        assert "nested/nested.ini" in source_inis
