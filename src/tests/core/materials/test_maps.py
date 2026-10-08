"""Authored material maps and draw-time texture binding regressions."""


import pytest
from PIL import Image

from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, parse_sections
from core.textures import load_texture_image_full
from tests.support.model_data import standard_component_resources, triangle_geometry
from tests.support.provenance import build_mesh_fixture, texture_file, visible


@pytest.mark.parametrize("binding", ["semantic", "slot"])
def test_auxiliary_material_maps(binding):
    normal, light, material = (
        r"Resource\ZZMI\NormalMap", r"Resource\ZZMI\LightMap",
        r"Resource\ZZMI\MaterialMap")
    markers = ""
    if binding == "slot":
        markers = "\n".join(f"ps-t{slot} = {role}" for slot, role in
                            enumerate((normal, light, material), start=1))
        normal, light, material = "ps-t1", "ps-t2", "ps-t3"
    sections = parse_sections("mod.ini", text=f"""[KeyDetail]
type = cycle
$detail = 0,1
[KeyMetal]
type = cycle
$metal = 0,1

[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
{markers}
if $detail == 0
{normal} = ref ResourceNormalA
else
{normal} = ref ResourceNormalB
endif
{light} = ref ResourceLight
if $metal == 1
{material} = ref ResourceMaterial
endif
drawindexed = 3, 0, 0

[ResourceNormalA]
filename = normal-a.dds
[ResourceNormalB]
filename = normal-b.dds
[ResourceLight]
filename = light.dds
[ResourceMaterial]
filename = material.dds
""" + standard_component_resources())
    draw = build_draw_groups(sections, extract_resources(sections))[0]["draws"][0]

    assert [[item["file"] for item in draw.texture_rules("normal_map")
             if visible(item["conditions"], {"detail": value})]
            for value in ("0", "1")] == [["normal-a.dds"], ["normal-b.dds"]]
    assert draw.texture_default("light_map") == "light.dds"
    assert [[item["file"] for item in draw.texture_rules("material_map")
             if visible(item["conditions"], {"metal": value})]
            for value in ("0", "1")] == [[], ["material.dds"]]
    assert draw.texture_default("material_map") is None


def test_packed_light_map_passthrough_preserves_authored_rgb(tmp_path):
    path = tmp_path / "packed.png"
    Image.new("RGB", (1, 1), (210, 12, 94)).save(path)
    packed = load_texture_image_full(str(path))

    assert packed.getpixel((0, 0)) == (210, 12, 94, 255)


def test_diffuse_assignment_lifecycle_reaches_mesh_payload(tmp_path):
    sections = parse_sections("mod.ini", text=r"""[KeyColor]
type = cycle
$color = 0,1,2

[TextureOverrideComponent01]
ib = ResourceComponent01IB
vb0 = ResourceComponent01Position
vb1 = ResourceComponent01Texcoord
drawindexed = 3, 0, 0
Resource\GIMI\Diffuse = ref ResourceDiffuseA
drawindexed = 3, 3, 0
if $color == 1
Resource\GIMI\Diffuse = ref ResourceDiffuseB
endif
if $color == 2
Resource\GIMI\Diffuse = ref ResourceDiffuseC
endif
drawindexed = 3, 6, 0
Resource\GIMI\Diffuse = ref ResourceDiffuseB
drawindexed = 3, 9, 0
Resource\GIMI\Diffuse = ref ResourceDiffuseA
drawindexed = 3, 12, 0

[ResourceDiffuseA]
filename = diffuse-a.png
[ResourceDiffuseB]
filename = diffuse-b.png
[ResourceDiffuseC]
filename = diffuse-c.png
""" + standard_component_resources(
        position_file="p.buf", texcoord_file="t.buf", ib_file="i.buf"))
    geometry = triangle_geometry()
    geometry["i.buf"] *= 5
    for name, data in geometry.items():
        (tmp_path / name).write_bytes(data)
    for name in ("diffuse-a.png", "diffuse-b.png", "diffuse-c.png"):
        Image.new("RGB", (1, 1), (20, 40, 60)).save(tmp_path / name)
    groups = build_draw_groups(sections, extract_resources(sections))
    meshes, _ = build_mesh_fixture(groups, str(tmp_path))
    by_start = {mesh["drawindexed"][1]: mesh for mesh in meshes.values()}

    assignments = by_start[6]["texture_variants"]
    assert [texture_file(item["tex_key"]) for item in assignments] == [
        "diffuse-a.png", "diffuse-b.png", "diffuse-c.png"]

    def selected(start, color):
        mesh = by_start[start]
        key = next((item["tex_key"]
                    for item in reversed(mesh.get("texture_variants", []))
                    if visible(item["conditions"], {"color": color})), mesh["tex_key"])
        return texture_file(key)

    assert [selected(start, color) for start, color in (
        (0, "0"), (3, "0"), (6, "0"), (6, "1"), (6, "2"), (9, "2"), (12, "2"),
    )] == [None, "diffuse-a.png", "diffuse-a.png", "diffuse-b.png",
           "diffuse-c.png", "diffuse-b.png", "diffuse-a.png"]
