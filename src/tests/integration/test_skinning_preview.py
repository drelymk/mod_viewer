"""On-demand skin preview integration coverage."""

import struct
from types import SimpleNamespace

import pytest

from app.bridge.mod_preview import ModPreview
from core.geometry.mesh_builder import GeometryBlob, build_mesh_result
from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, merge_sections


class _Access:
    def mod_folder(self, path):
        return path


def _write_mod(tmp_path):
    ini = tmp_path / "mod.ini"
    ini.write_text(
        """[TextureOverrideBodyBlend]
ib = ResourceBodyIB
vb0 = ResourceBodyPosition
vb1 = ResourceBodyBlend
vb2 = ResourceBodyTexcoord
drawindexed = 6, 0, 0

[TextureOverrideBodyTexcoord]
vb1 = ResourceBodyTexcoord

[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT

[ResourceBodyPosition]
filename = body.pos
stride = 12

[ResourceBodyBlend]
filename = body.blend
stride = 32

[ResourceBodyTexcoord]
filename = body.tc
stride = 20
""",
        encoding="utf-8",
    )
    (tmp_path / "body.ib").write_bytes(struct.pack(
        "<6I", 2, 0, 1, 1, 3, 2))
    (tmp_path / "body.pos").write_bytes(b"".join(
        struct.pack("<3f", float(i), 0., 0.) for i in range(4)))
    (tmp_path / "body.tc").write_bytes(b"\0" * 20 * 4)
    (tmp_path / "body.blend").write_bytes(b"".join(
        struct.pack("<4f4I", .6, .3, .1, 0., 7, 8, 9, 0)
        for _ in range(4)))
    return ini


def test_get_skinning_preview_matches_rendered_compaction(tmp_path, monkeypatch):
    ini = _write_mod(tmp_path)
    sections = merge_sections([str(ini)])
    groups = build_draw_groups(sections, extract_resources(sections))
    geometry = GeometryBlob()
    rendered = build_mesh_result(groups, str(tmp_path), geometry=geometry)
    entry = rendered.meshes["BodyBlend-1"]
    published = {}

    def publish(blob, *, replace=True):
        published["blob"] = bytes(blob)
        published["replace"] = replace
        return "/geometry/skin-test"

    context = SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=[str(ini)], docs={}, metadata={},
        asset_folders=[])
    preview = ModPreview(_Access())
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _path: (str(tmp_path), {}, {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_geometry", publish)
    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_mod",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview must not load the model")),
    )

    result = preview.get_skinning_preview(str(tmp_path), "BodyBlend-1")

    position_length = entry["pos"]["length"]
    assert result["status"] == "ok"
    assert result["vertex_count"] == position_length // 12
    assert result["encoding"] == "gimi_f32_u32_4"
    assert result["bone_ids"] == [7, 8, 9]
    assert result["data"]["indices"]["offset"] == 0
    assert result["data"]["weights"]["offset"] == 4 * 4 * 4
    assert published["replace"] is False
    assert struct.unpack_from("<4I", published["blob"], 0) == (7, 8, 9, 0)
    assert struct.unpack_from("<4f", published["blob"], 4 * 4 * 4) == pytest.approx(
        (.6, .3, .1, 0.))
