"""Generic, name-independent geometry association regressions."""

import struct

from core.ini.draw_groups import build_draw_groups
from core.ini.sections import extract_resources, parse_sections


def _write_buffers(root, entries):
    for name, kind, count, values in entries:
        path = root / name
        if kind == "position":
            path.write_bytes(b"".join(
                struct.pack("<3f", float(index), float(index), float(index))
                for index in range(count)))
        elif kind == "texcoord":
            path.write_bytes(b"".join(
                struct.pack("<ff", 0.1 * (index % 10),
                            0.2 * (index % 5)) + b"\0" * 12
                for index in range(count)))
        else:
            path.write_bytes(struct.pack(f"<{len(values)}I", *values))


def test_complete_draw_state_does_not_need_semantic_names():
    sections = parse_sections("sample.ini", text="\n".join([
        "[TextureOverridePotato]",
        "ib = ResourceOne",
        "vb0 = ResourceTwo",
        "vb7 = ResourceThree",
        "drawindexed = 3, 0, 0",
        "",
        "[ResourceOne]",
        "filename = one.ib",
        "format = DXGI_FORMAT_R32_UINT",
        "[ResourceTwo]",
        "filename = two.buf",
        "stride = 12",
        "[ResourceThree]",
        "filename = three.buf",
        "stride = 20",
    ]))

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]
    assert (draw.ib_file, draw.position_file, draw.texcoord_file) == (
        "one.ib", "two.buf", "three.buf")
    assert draw.geometry_resolution["source"] == "direct"


def test_runtime_shared_position_uses_file_backed_structural_source():
    sections = parse_sections("sample.ini", text="""[CommandListShared]
vb0 = ResourceRuntimePosition
vb2 = ResourceSharedUV

[TextureOverrideIndexed]
ib = ResourceIndex
run = CommandListShared
drawindexed = 3, 0, 0

[ResourceRuntimePosition]

[ResourceSharedUV]
filename = shared-uv.buf
stride = 20

[ResourceIndex]
filename = index.ib
format = DXGI_FORMAT_R32_UINT

[ResourcePositionSource]
filename = position.buf
format = R32G32B32_FLOAT
stride = 12
""")

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]

    assert draw.position_file == "position.buf"
    assert draw.texcoord_file == "shared-uv.buf"


def test_split_families_use_index_coverage_and_record_counts(tmp_path):
    ini = "\n".join([
        "[TextureOverrideApple.LOD0]", "vb0 = ResourceA",
        "draw = 10, 0", "[TextureOverrideChair.LOD0]",
        "vb1 = ResourceB", "[TextureOverrideMoon.LOD0]",
        "ib = ResourceC", "drawindexed = 3, 0, 0",
        "[TextureOverrideRiver.LOD1]", "vb0 = ResourceD",
        "draw = 20, 0", "[TextureOverrideStone.LOD1]",
        "vb1 = ResourceE", "[TextureOverrideCloud.LOD1]",
        "ib = ResourceF", "drawindexed = 3, 0, 0",
        "[ResourceA]", "filename = a.buf", "stride = 12",
        "[ResourceB]", "filename = b.buf", "stride = 20",
        "[ResourceC]", "filename = c.ib",
        "format = DXGI_FORMAT_R32_UINT", "[ResourceD]",
        "filename = d.buf", "stride = 12", "[ResourceE]",
        "filename = e.buf", "stride = 20", "[ResourceF]",
        "filename = f.ib", "format = DXGI_FORMAT_R32_UINT",
    ])
    _write_buffers(tmp_path, [
        ("a.buf", "position", 10, None),
        ("b.buf", "texcoord", 10, None),
        ("c.ib", "index", 3, (0, 1, 2)),
        ("d.buf", "position", 20, None),
        ("e.buf", "texcoord", 20, None),
        ("f.ib", "index", 3, (17, 18, 19)),
    ])
    sections = parse_sections("sample.ini", text=ini)
    groups = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)

    assert {
        group["ib_file"]: (group["position_file"], group["texcoord_file"])
        for group in groups
    } == {
        "c.ib": ("a.buf", "b.buf"),
        "f.ib": ("d.buf", "e.buf"),
    }


def test_equal_file_backed_candidates_remain_ambiguous(tmp_path):
    ini = "\n".join([
        "[TextureOverrideFirst]", "vb0 = ResourceAlpha",
        "draw = 10, 0", "[TextureOverrideSecond]",
        "vb0 = ResourceBeta", "draw = 10, 0",
        "[TextureOverrideIndex]", "ib = ResourceGamma",
        "drawindexed = 3, 0, 0", "[TextureOverrideTexcoord]",
        "vb1 = ResourceDelta", "[ResourceAlpha]",
        "filename = alpha.buf", "stride = 12", "[ResourceBeta]",
        "filename = beta.buf", "stride = 12", "[ResourceGamma]",
        "filename = gamma.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourceDelta]", "filename = delta.buf", "stride = 20",
    ])
    _write_buffers(tmp_path, [
        ("alpha.buf", "position", 10, None),
        ("beta.buf", "position", 10, None),
        ("gamma.ib", "index", 3, (0, 1, 2)),
        ("delta.buf", "texcoord", 10, None),
    ])
    sections = parse_sections("sample.ini", text=ini)
    group = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0]
    draw = group["draws"][0]

    assert draw.geometry_resolution["error"] == "ambiguous_position"
    assert draw.position_file is None


def test_split_family_uses_complete_indexed_draw_scope(tmp_path):
    ini = "\n".join([
        "[TextureOverrideSmall.LOD0]", "vb0 = ResourceAlpha",
        "draw = 10, 0", "[TextureOverrideSmallUV.LOD0]",
        "vb1 = ResourceBeta", "[TextureOverrideLarge.LOD0]",
        "vb0 = ResourceDelta", "draw = 20, 0",
        "[TextureOverrideLargeUV.LOD0]", "vb1 = ResourceEpsilon",
        "[TextureOverrideIndexed.LOD0]", "ib = ResourceGamma",
        "drawindexed = 3, 0, 0", "drawindexed = 3, 3, 0",
        "[ResourceAlpha]", "filename = alpha.buf", "stride = 12",
        "[ResourceBeta]", "filename = beta.buf", "stride = 20",
        "[ResourceDelta]", "filename = delta.buf", "stride = 12",
        "[ResourceEpsilon]", "filename = epsilon.buf", "stride = 20",
        "[ResourceGamma]", "filename = gamma.ib",
        "format = DXGI_FORMAT_R32_UINT",
    ])
    _write_buffers(tmp_path, [
        ("alpha.buf", "position", 10, None),
        ("beta.buf", "texcoord", 10, None),
        ("delta.buf", "position", 20, None),
        ("epsilon.buf", "texcoord", 20, None),
        ("gamma.ib", "index", 6, (0, 1, 2, 17, 18, 19)),
    ])
    sections = parse_sections("sample.ini", text=ini)
    group = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0]

    assert [draw.position_file for draw in group["draws"]] == [
        "delta.buf", "delta.buf"]
    assert [draw.texcoord_file for draw in group["draws"]] == [
        "epsilon.buf", "epsilon.buf"]


def test_unrelated_declared_count_does_not_beat_smaller_valid_domain(tmp_path):
    ini = "\n".join([
        "[TextureOverrideFirst]", "vb0 = ResourceAlpha",
        "draw = 11, 0", "[TextureOverrideSecond]",
        "vb0 = ResourceBeta", "draw = 20, 0",
        "[TextureOverrideIndexed]", "ib = ResourceGamma",
        "drawindexed = 3, 0, 0", "[TextureOverrideUV]",
        "vb1 = ResourceDelta", "[ResourceAlpha]",
        "filename = alpha.buf", "stride = 12", "[ResourceBeta]",
        "filename = beta.buf", "stride = 12", "[ResourceGamma]",
        "filename = gamma.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourceDelta]", "filename = delta.buf", "stride = 20",
    ])
    _write_buffers(tmp_path, [
        ("alpha.buf", "position", 10, None),
        ("beta.buf", "position", 20, None),
        ("gamma.ib", "index", 3, (0, 1, 2)),
        ("delta.buf", "texcoord", 10, None),
    ])
    sections = parse_sections("sample.ini", text=ini)
    draw = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0][
            "draws"][0]

    assert draw.position_file == "alpha.buf"


def test_coherence_breaks_legal_position_tie_without_names(tmp_path):
    ini = "\n".join([
        "[TextureOverrideFirst]", "vb0 = ResourceAlpha",
        "draw = 30, 0", "[TextureOverrideSecond]",
        "vb0 = ResourceBeta", "draw = 30, 0",
        "[TextureOverrideIndexed]", "ib = ResourceGamma",
        "drawindexed = 30, 0, 0", "[TextureOverrideUV]",
        "vb1 = ResourceDelta", "[ResourceAlpha]",
        "filename = alpha.buf", "stride = 12", "[ResourceBeta]",
        "filename = beta.buf", "stride = 12", "[ResourceGamma]",
        "filename = gamma.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourceDelta]", "filename = delta.buf", "stride = 20",
    ])
    _write_buffers(tmp_path, [
        ("alpha.buf", "position", 30, None),
        ("beta.buf", "position", 30, None),
        ("gamma.ib", "index", 30, tuple(range(30))),
        ("delta.buf", "texcoord", 30, None),
    ])
    beta_positions = []
    for triangle in range(10):
        if triangle == 9:
            beta_positions.extend(((1000.0, 0.0, 0.0),
                                   (0.0, 1000.0, 0.0),
                                   (0.0, 0.0, 1000.0)))
        else:
            beta_positions.extend(((float(triangle), 0.0, 0.0),
                                   (float(triangle) + 1.0, 0.0, 0.0),
                                   (float(triangle), 1.0, 0.0)))
    (tmp_path / "beta.buf").write_bytes(b"".join(
        struct.pack("<3f", *point) for point in beta_positions))
    sections = parse_sections("sample.ini", text=ini)
    draw = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0][
            "draws"][0]

    assert draw.position_file == "alpha.buf"
    assert draw.geometry_resolution["coherence_selected"] is True


def test_drawindexed_auto_keeps_an_explicit_whole_index_buffer():
    sections = parse_sections("sample.ini", text="\n".join([
        "[TextureOverrideApple]", "ib = ResourceIndex",
        "vb0 = ResourcePosition", "vb1 = ResourceTexcoord",
        "drawindexed = auto", "[ResourceIndex]",
        "filename = index.buf", "format = DXGI_FORMAT_R32_UINT",
        "[ResourcePosition]", "filename = position.buf",
        "stride = 12", "[ResourceTexcoord]",
        "filename = texcoord.buf", "stride = 20",
    ]))

    draw = build_draw_groups(
        sections, extract_resources(sections))[0]["draws"][0]
    assert draw.count is None
    assert draw.geometry_resolution["source"] == "direct"
