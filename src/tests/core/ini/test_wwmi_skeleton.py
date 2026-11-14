import os

import pytest

from core.geometry.mesh_builder import build_mesh_result
from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, parse_sections
from core.ini.wwmi_skeleton import stock_wwmi_merged_layouts
from tests.support.wwmi_skeleton import stock_merged_skeleton_ini
from tests.support.wwmi_skeleton import modern_merged_skeleton_ini


def test_stock_layout_follows_direct_draws_and_survives_weight_changes(tmp_path):
    import struct

    text = stock_merged_skeleton_ini()
    sections = parse_sections("source-01.ini", text=text)
    layouts = stock_wwmi_merged_layouts(sections)
    expected = (("10101010", 12, 6, 0, 2), ("10101010", 18, 3, 2, 1))
    assert {value[1] for value in layouts.values()} == {expected}
    wider = parse_sections("source-01.ini", text=text.replace("vb4 = ResourceJoints", "vb4 = ResourceJoints\nvb6 = null"))
    assert stock_wwmi_merged_layouts(wider) == layouts
    guarded = parse_sections("source-01.ini", text=text + "\n[TextureOverrideCallback]\nif ResourcePoseA !== null\nendif\n")
    assert stock_wwmi_merged_layouts(guarded) == layouts
    groups = build_draw_groups(sections, extract_resources(sections))
    draws = [draw for group in groups for draw in group["draws"]]
    assert len(draws) == 2
    assert all(draw.skinning_merged_layout == expected for draw in draws)
    (tmp_path / "mesh.pos").write_bytes(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0))
    (tmp_path / "mesh.idx").write_bytes(struct.pack("<6I", 0, 1, 2, 0, 2, 1))
    (tmp_path / "mesh.uv").write_bytes(bytes(16 * 3))
    (tmp_path / "mesh.vec").write_bytes(bytes(8 * 3))
    (tmp_path / "mesh.blend").write_bytes(bytes([0, 0, 0, 0, 255, 0, 0, 0]) * 3)
    result = build_mesh_result(groups, str(tmp_path), geometry_only=True)
    assert len(result.skinning_manifest) == 2
    assert all(entry.merged_layout == expected for entry in result.skinning_manifest.values())
    (tmp_path / "mesh.blend").write_bytes(bytes([2, 0, 0, 0, 255, 0, 0, 0]) * 3)
    changed = build_mesh_result(groups, str(tmp_path), geometry_only=True)
    assert all(entry.merged_layout == expected for entry in changed.skinning_manifest.values())


def test_proxy_assignments_may_follow_unconditional_pixel_setup_only():
    text = modern_merged_skeleton_ini(remapped=True)
    triple = ("ResourceProxyBlend = ref ResourcePartBlend\nResourceProxyA = ref ResourcePartA\n"
              "ResourceProxyB = ref ResourcePartB\n")
    assert triple in text
    expected = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text))
    assert expected
    pixels = "if $enabled\nResource\\RabbitFX\\Diffuse = ref ResourceEffect\nendif\nrun = CommandList\\RabbitFX\\SetTextures\n"
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text.replace(triple, pixels + triple))) == expected
    conditional = text.replace(triple, "if $enabled\n" + triple + "endif\n")
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=conditional)) == {}


def _pixel_scratch_ini(remapped=True):
    text = modern_merged_skeleton_ini(remapped=remapped)
    text = text.replace("drawindexed = 3, 0, 0", "run = CommandListPixelApply\ndrawindexed = 3, 0, 0\nrun = CommandListPixelRestore")
    return text + """
[ResourcePixelSaved]
[CommandListPixelApply]
if $mode == 1
ResourcePixelSaved = ref ps-t60
ps-t60 = ref ResourceEffect
endif
[CommandListPixelRestore]
if $mode == 1
ps-t60 = ref ResourcePixelSaved
ResourcePixelSaved = null
endif
"""


@pytest.mark.parametrize("remapped", [False, True])
def test_pixel_save_restore_helpers_preserve_draw_palette_proof(remapped):
    expected = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=modern_merged_skeleton_ini(remapped=remapped)))
    text = _pixel_scratch_ini(remapped)
    sections = parse_sections("source-01.ini", text=text)
    proofs = stock_wwmi_merged_layouts(sections)
    assert {key: value._replace(helpers=frozenset()) for key, value in proofs.items()} == expected
    draws = [draw for group in build_draw_groups(sections, extract_resources(sections)) for draw in group["draws"]]
    assert draws and all(draw.skinning_merged_layout == expected["textureoverridepart01"].layout for draw in draws)
    assert all(bool(draw.skinning_source.blend_remap) is remapped for draw in draws)
    # The same pixel-only operations may be inline around a bound draw.
    inline = text.replace("run = CommandListPixelApply", "ResourcePixelSaved = ref ps-t60\nps-t60 = ResourceEffect")
    inline = inline.replace("run = CommandListPixelRestore", "ps-t60 = ref ResourcePixelSaved\nResourcePixelSaved = null")
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=inline)) == proofs


@pytest.mark.parametrize("anchor,remapped", [
    ("[CommandListCleanup]\n", None), ("[CommandListBind]\n", None),
    ("cs-cb8 = ref vs-cb4\n", None),
    ("[CommandListCleanup]\n", False), ("[CommandListCleanup]\n", True),
    ("cs-cb8 = ref vs-cb4\n", True), ("[CommandListPublish]\n", True),
    ("[CommandListBind]\n", True), ("[CommandListInitialize]\n", True),
    ("[CommandListRemapPose]\n", True),
    ("run = CommandListBind\n", True),
])
def test_pixel_only_work_is_irrelevant_throughout_the_skeleton_program(anchor, remapped):
    text = stock_merged_skeleton_ini() if remapped is None else modern_merged_skeleton_ini(remapped=remapped)
    expected = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text))
    pixel = "if $mode == 1\nrun = CommandListPixelOuter\nelse\nps-t60 = null\nendif\n"
    changed = text.replace(anchor, anchor + pixel, 1) + """
[CommandListPixelOuter]
run = CommandListPixelInner
[CommandListPixelInner]
Resource\\RabbitFX\\Diffuse = null
run = CommandList\\RabbitFX\\SetTextures
"""
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=changed)) == expected


def test_unrelated_ini_skips_skeleton_template_projection(monkeypatch):
    from core.ini import wwmi_skeleton

    def unexpected(_commands):
        raise AssertionError("Unrelated INIs must not run skeleton template projection.")

    monkeypatch.setattr(wwmi_skeleton, "_without_pixel_state", unexpected)
    sections = parse_sections("source-01.ini", text="[TextureOverridePart01]\nhash = 10101010\ndrawindexed = 3, 0, 0\n")
    assert stock_wwmi_merged_layouts(sections) == {}


@pytest.mark.parametrize("unsafe", [
    "vb4 = ResourceEffect", "vs-cb4 = ResourceEffect", "ResourcePoseA = null",
    "$\\WWMIv1\\vg_offset = 9", "$status01 = 0", "run = CommandListUnknown",
])
def test_structural_projection_never_discards_non_pixel_helper_effects(unsafe):
    text = modern_merged_skeleton_ini(remapped=True)
    text = text.replace("[CommandListCleanup]\n", "[CommandListCleanup]\nrun = CommandListExtra\n")
    text += "\n[CommandListExtra]\nps-t60 = null\n" + unsafe + "\n"
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text)) == {}


@pytest.mark.parametrize("before,after,extra", [
    ("ref ps-t60", "ref vb4", ""),
    ("ref ps-t60", "ref vs-cb4", ""),
    ("ref ps-t60", "ref cs-t0", ""),
    ("ref ps-t60", "ref ResourcePoseA", ""),
    ("[ResourcePixelSaved]\n", "[ResourcePixelSaved]\nfilename = saved.buf\n", ""),
    ("ResourcePixelSaved = null", "ResourcePixelSaved = copy ResourceEffect", ""),
    ("ResourcePixelSaved = null", "ResourcePixelSaved = ref ResourceEffect", ""),
    ("", "", "\n[CommandListOther]\ncs-t0 = ref ResourcePixelSaved\n"),
    ("", "", "\n[CommandListOther]\nvb0 = ref ResourcePixelSaved\n"),
    ("", "", "\n[CommandListOther]\npost cs-u0 = ref ResourcePixelSaved\n"),
    ("", "", "\n[CommandListOther]\npost vb4 = ref ResourcePixelSaved\n"),
    ("", "", "\n[CommandListOther]\nResourceAlias = ref ResourcePixelSaved\n"),
    ("ResourcePixelSaved", "ResourcePoseA", ""),
])
def test_pixel_scratch_role_rejects_non_pixel_or_protected_consumers(before, after, extra):
    text = _pixel_scratch_ini()
    if before:
        assert before in text
        text = text.replace(before, after)
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text + extra)) == {}


@pytest.mark.parametrize("before,after", [
    (r"vg_offset = 2", r"vg_offset = 3"),
    (r"vg_count = 1", r"vg_count = 0"),
    ("cs-cb8 = ref vs-cb4", "cs-cb8 = ref vs-cb5"),
    ("cs-u6 = ResourcePoseARW", "cs-u6 = ResourceOther"),
    ("match_first_index = 18", "hash = 30303030\nmatch_first_index = 18"),
    ("vb4 = ResourceJoints", "vb4 = ResourceOther"),
    ("run = CommandListBind", "post run = CommandListBind"),
    ("run = CommandListPublish", "run = CommandListUnknown"),
    ("run = CommandListPublish", "run = CommandListPublish\nResourceOther = copy ResourcePoseA"),
    ("run = CommandList\\RabbitFX\\Run", "run = CommandListUnknown"),
    ("ps-t17 = ResourceEffect", "vb4 = ResourceOther"),
    ("ps-t17 = ResourceEffect", "vs-cb4 = ResourceOther"),
    ("ps-t17 = ResourceEffect", "if $mode == 0\nrun = CommandListCleanup\nendif"),
    ("drawindexed = 3, 0, 0", "run = CommandListNested"),
])
def test_unknown_or_changed_skeleton_pipeline_has_no_layout_proof(before, after):
    sections = parse_sections("source-01.ini", text=stock_merged_skeleton_ini().replace(before, after))
    assert stock_wwmi_merged_layouts(sections) == {}


def test_local_external_overrides_sibling_inis_and_conflicting_dedup_drop_proof():
    from core.geometry.draw_call import DrawCall
    from core.geometry.semantics import deduplicate_draws
    from core.ini.sections import SrcLine

    text = stock_merged_skeleton_ini()
    for addition in (r"[CustomShader\WWMIv1\SkeletonMerger]", r"[CommandList\RabbitFX\Run]"):
        assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text + addition)) == {}
    sections = parse_sections("source-01.ini", text=text)
    sections["ResourceJoints"] = [SrcLine(str(line), "source-02.ini", 1) for line in sections["ResourceJoints"]]
    assert stock_wwmi_merged_layouts(sections) == {}
    first = DrawCall(count=3, skinning_merged_layout=(("10101010", 0, 3, 0, 2),))
    second = DrawCall(count=3)
    for draws in ([first, second], [second, first]):
        assert deduplicate_draws({"draws": draws})[0].skinning_merged_layout == ()
    from dataclasses import replace
    from core.geometry.skinning import SkinningSource
    shared = SkinningSource("stream.blend", 8, 4, "wwmi_u8_4", 7, bone_id_namespace="wwmi_merged")
    legacy = replace(shared, bone_id_namespace="model")
    ambiguous = deduplicate_draws({"draws": [DrawCall(count=3, skinning_source=shared),
                                             DrawCall(count=3, skinning_source=legacy)]})[0]
    assert ambiguous.skinning_source is None
    assert ambiguous.skinning_error == "ambiguous_skinning_source"


@pytest.mark.parametrize("remapped,influences,map_size,width", [(False, 4, 512, 2), (True, 4, 512, 2), (True, 8, 1024, 4)])
def test_merge_status_and_proxy_decode_lifecycle(tmp_path, remapped, influences, map_size, width):
    import struct

    from core.geometry.buffers import BufferStore
    from core.geometry.skinning import decode_skinning, read_blend_remap_tables, SkinningPreviewError

    text = modern_merged_skeleton_ini(remapped=remapped, influences=influences, map_size=map_size)
    if remapped and map_size == 512:
        text = text.replace("$\\WWMIv1\\blend_remap_bone_count = 512\n", "")
    if width == 4:
        text = text.replace("[ResourceReverse]\ntype = Buffer\nformat = R16_UINT",
                            "[ResourceReverse]\ntype = Buffer\nformat = R32_UINT")
        text = text.replace("[ResourceForward]\ntype = Buffer\nformat = R16_UINT",
                            "[ResourceForward]\ntype = Buffer\nformat = R32_UINT")
    sections = parse_sections("source-01.ini", text=text)
    proofs = stock_wwmi_merged_layouts(sections)
    assert len(proofs) == 2
    groups = build_draw_groups(sections, extract_resources(sections))
    draws = [draw for group in groups for draw in group["draws"]]
    assert len(draws) == 2
    source = draws[0].skinning_source
    assert source.bone_id_namespace == ("wwmi_vertex_vg" if remapped else "wwmi_merged")
    assert bool(source.blend_remap) is remapped
    raw = bytes([0] * influences + [128, 127] + [0] * (influences - 2)) * 3
    vertex = struct.pack(f"<{influences}H", 0, 256, *([0] * (influences - 2))) * 3
    (tmp_path / "mesh.blend").write_bytes(raw)
    (tmp_path / "full-vg.buf").write_bytes(vertex)
    if remapped:
        reverse, forward = [0] * (map_size * 2), [0] * (map_size * 2)
        reverse[map_size + 256] = 1
        forward[map_size + 1] = 256
        fmt = f"<{map_size * 2}{'H' if width == 2 else 'I'}"
        (tmp_path / "reverse.buf").write_bytes(struct.pack(fmt, *reverse))
        (tmp_path / "forward.buf").write_bytes(struct.pack(fmt, *forward))
    def resolve(file):
        return str(tmp_path / file)

    buffers = BufferStore()
    tables = read_blend_remap_tables(source, resolve, os.path.isfile, buffers)
    decoded = decode_skinning(source, raw, (2, 0), vertex if remapped else None, **tables)
    ids = struct.unpack(f"<{influences * 2}I", decoded.indices)
    assert ids[:2] == ((0, 256) if remapped else (0, 0))
    assert len(decoded.weights) == influences * 2 * 4
    assert source.bone_id_domain != "model"
    if remapped:
        damaged = bytearray(tables["forward_map_data"])
        struct.pack_into("<H" if width == 2 else "<I", damaged, (map_size + 1) * width, 255)
        with pytest.raises(SkinningPreviewError, match="disagree"):
            decode_skinning(source, raw, (0,), vertex, **{**tables, "forward_map_data": damaged})
        with pytest.raises(SkinningPreviewError, match="truncated"):
            decode_skinning(source, raw, (0,), vertex, **{**tables, "reverse_map_data": b""})
        with pytest.raises(SkinningPreviewError, match="layout"):
            decode_skinning(source, raw, (0,), vertex[:-2], **tables)
        # The remapper's extra threads only read zeros and drop writes past the
        # buffers; a short count would leave real vertices unremapped.
        from dataclasses import replace
        for declared, ok in ((4, True), (2, False)):
            counted = replace(source, blend_remap=replace(source.blend_remap, vertex_count=declared))
            if ok:
                assert decode_skinning(counted, raw, (2, 0), vertex, **tables).indices == decoded.indices
            else:
                with pytest.raises(SkinningPreviewError, match="layout"):
                    decode_skinning(counted, raw, (0,), vertex, **tables)


_TOGGLES = "\n[CommandListToggles]\nif $mode == 1\n$flag = 2\nendif\n"
_EXTRA = "\n[CommandListExtra]\nif $mode == 1\ndrawindexed = 3, 3, 0\nendif\n"
_OPTIONAL_DRAW = "if $mode == 1\ndrawindexed = 3, 3, 0\nendif\n"


def _variant(text, edits, extra=""):
    for before, after in edits:
        assert before in text
        text = text.replace(before, after, 1)
    return text + extra


@pytest.mark.parametrize("remapped", [False, True])
@pytest.mark.parametrize("edits,extra", [
    # Mesh scale changes pose magnitude only, never palette IDs.
    ([("custom_mesh_scale = 1.0", "custom_mesh_scale = $size"),
      ("global $enabled = 1", "global persist $size = 0.8\nglobal $enabled = 1")], ""),
    ([("vs-cb4 = ref ResourcePoseA", "vs-cb4 = ResourcePoseA")], ""),
    ([("vb3 = ResourceColors\n", "vb3 = ResourceColors\nvb6 = null\n")], ""),
    ([("ps-t17 = ResourceEffect", "ps-t17=ResourceEffect-01.a")], "\n[ResourceEffect-01.a]\nfilename = effect.dds\n"),
    ([("match_index_count = 6\n", "match_index_count = 6\nmatch_priority = -1\n$\\ns\\flag = 0\n"
                                  "if $mode == 1\nps-t0 = ResourceEffect\nendif\n")], ""),
    ([("match_index_count = 6\nif $enabled\n", "match_index_count = 6\nif $enabled && $mode >= 0\n"
                                               "if $mode == 1\nps-t1 = ResourceEffect\nendif\n")], ""),
    ([("run = CommandListCleanup\nendif\nendif\n", "run = CommandListCleanup\nendif\n$active = 1\nendif\n"
                                                    "ps-t0 = ResourceEffect\nCP3\n")], ""),
    ([("[CommandListCleanup]\nvb0 = ref ResourceSaved\n", "[CommandListCleanup]\nvb0 = ref ResourceSaved\n$active = 1\n")], ""),
    ([("drawindexed = 3, 0, 0", "run = CommandListToggles\n$mode == 1,2\ndrawindexed = 3, 0, 0")], _TOGGLES),
    ([(_OPTIONAL_DRAW, "run = CommandListExtra\n")], _EXTRA),
    ([("endif\nif ResourcePoseA !== null\nhandling = skip\nendif\nendif\n[ResourcePoseA]",
       "endif\nendif\nps-t0 = ResourceEffect\n[ResourcePoseA]")], ""),
])
def test_exporter_variants_keep_the_exact_layout_proof(remapped, edits, extra):
    text = modern_merged_skeleton_ini(remapped=remapped)
    expected = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text))
    assert len(expected) == 2
    sections = parse_sections("source-01.ini", text=_variant(text, edits, extra))
    proofs = stock_wwmi_merged_layouts(sections)
    assert {name: proof._replace(helpers=frozenset()) for name, proof in proofs.items()} == expected
    draws = [draw for group in build_draw_groups(sections, extract_resources(sections)) for draw in group["draws"]]
    assert draws and all(draw.skinning_merged_layout == expected["textureoverridepart01"].layout for draw in draws)


@pytest.mark.parametrize("template", [stock_merged_skeleton_ini, modern_merged_skeleton_ini])
def test_layout_past_the_u8_palette_keeps_its_exact_proof(template):
    # Exporters merge every component even past 256 bones; the merger drops the
    # unreachable writes, so the 256-bone buffer still covers every u8 Blend ID.
    text = template().replace("$\\WWMIv1\\vg_offset = 2\n$\\WWMIv1\\vg_count = 1",
                              "$\\WWMIv1\\vg_offset = 2\n$\\WWMIv1\\vg_count = 255")
    assert "vg_count = 255" in text and "array = 768" in text
    proofs = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text))
    assert len(proofs) == 2
    assert {proof.layout[-1][3:] for proof in proofs.values()} == {(2, 255)}
    # Each component palette still has to fit one native 256-bone skeleton CB.
    oversized = text.replace("vg_count = 255", "vg_count = 257")
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=oversized)) == {}


@pytest.mark.parametrize("edits,extra", [
    ([("drawindexed = 3, 0, 0", "run = CommandListToggles\ndrawindexed = 3, 0, 0")],
     _TOGGLES.replace("$flag = 2", "vb4 = ResourcePoints")),
    ([("run = CommandListChecks\nrun = CommandListBind", "run = CommandListExtra\nrun = CommandListChecks\nrun = CommandListBind")], _EXTRA),
    ([(_OPTIONAL_DRAW, "run = CommandList\\Other\\Draw\n")], ""),
    ([("match_index_count = 6\n", "match_index_count = 6\nif $mode == 1\nvb4 = ResourcePoints\nendif\n")], ""),
    ([("run = CommandListCleanup\nendif\nendif\n", "run = CommandListCleanup\nendif\nendif\nvs-cb4 = ResourcePoints\n")], ""),
    ([("ps-t17 = ResourceEffect", "ResourcePoseA = ref ps-t17")], ""),
])
def test_unsafe_exporter_look_alikes_stay_unproven(edits, extra):
    text = _variant(modern_merged_skeleton_ini(), edits, extra)
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text)) == {}


def test_remap_prepared_for_a_moved_component_is_ignored_unless_consumed():
    text = modern_merged_skeleton_ini(remapped=True)
    triple = ("ResourceProxyBlend = ref ResourcePartBlend\nResourceProxyA = ref ResourcePartA\n"
              "ResourceProxyB = ref ResourcePartB\n")
    moved = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text.replace(triple, "")))
    assert moved and all(proof.remap is None for proof in moved.values())
    consumed = text.replace(triple, "ps-t5 = ResourcePartA\n")
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=consumed)) == {}
    # A ref to never-declared resources leaves the proxy null in 3DMigoto.
    undeclared = text.replace(triple, triple.replace("ResourcePart", "ResourceGone"))
    proofs = stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=undeclared))
    assert proofs and all(proof.remap is None for proof in proofs.values())


@pytest.mark.parametrize("before,after", [
    ("$merge = 2", "$merge = 1"),
    ("$status02 = 0\nResourcePoseA", "ResourcePoseA"),
    ("if vs-cb4 == 1234.5 && vs-cb3 == 1234.5", "if vs-cb3 == 1234.5"),
    ("ResourceProxyA = ref ResourcePartA", "ResourceProxyA = ref ResourcePartB"),
    ("cs-t37 = ResourceForward", "cs-t37 = ResourceMissing"),
    ("ResourcePartBlend = copy_desc ResourceJoints", "ResourcePartBlend = copy_desc ResourcePoints"),
    ("$\\WWMIv1\\blend_remap_id = 1", "$\\WWMIv1\\blend_remap_id = 9999"),
    ("filename = mesh.blend\n[CommandListInitialize]", "filename = other.blend\n[CommandListInitialize]"),
    ("run = CommandListInitialize\nrun = CommandListPublish", "run = CommandListPublish"),
    ("[ResourceForward]\ntype = Buffer", "[ResourceForward]\ntype = Buffer\nstride = 2"),
    ("run = CommandListInitialize\nrun = CommandListPublish", "run = CommandListInitialize\n$vertices = 1\nrun = CommandListPublish"),
])
def test_modified_merge_status_or_proxy_program_stays_unproven(before, after):
    text = modern_merged_skeleton_ini(remapped=True)
    assert before in text
    assert stock_wwmi_merged_layouts(parse_sections("source-01.ini", text=text.replace(before, after))) == {}
