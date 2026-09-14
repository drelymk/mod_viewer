"""Generic, name-independent geometry association regressions."""

import struct

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_scan import _scan_sections_for_draws
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


def test_unproven_runtime_position_stays_unresolved():
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

    assert draw.position_file is None
    assert draw.geometry_resolution["error"] == "unresolved_runtime_position"


def test_split_families_use_index_coverage_and_record_counts(tmp_path):
    ini = "\n".join([
        "[TextureOverrideApple.LOD0]", "hash = 11111111",
        "vb0 = ResourceA",
        "draw = 10, 0", "[TextureOverrideChair.LOD0]",
        "hash = 11111111",
        "vb1 = ResourceB", "[TextureOverrideMoon.LOD0]",
        "ib = ResourceC", "drawindexed = 3, 0, 0",
        "hash = 11111111",
        "[TextureOverrideRiver.LOD1]", "hash = 22222222",
        "vb0 = ResourceD",
        "draw = 20, 0", "[TextureOverrideStone.LOD1]",
        "hash = 22222222",
        "vb1 = ResourceE", "[TextureOverrideCloud.LOD1]",
        "hash = 22222222", "ib = ResourceF", "drawindexed = 3, 0, 0",
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
        "[TextureOverrideFirst]", "hash = 11111111",
        "vb0 = ResourceAlpha",
        "draw = 10, 0", "[TextureOverrideSecond]",
        "hash = 11111111", "vb0 = ResourceBeta", "draw = 10, 0",
        "[TextureOverrideIndex]", "hash = 11111111",
        "ib = ResourceGamma", "drawindexed = 3, 0, 0",
        "[TextureOverrideTexcoord]", "hash = 11111111",
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
        "[TextureOverrideSmall.LOD0]", "hash = 11111111",
        "vb0 = ResourceAlpha",
        "draw = 10, 0", "[TextureOverrideSmallUV.LOD0]",
        "hash = 11111111", "vb1 = ResourceBeta",
        "[TextureOverrideLarge.LOD0]", "hash = 22222222",
        "vb0 = ResourceDelta", "draw = 20, 0",
        "[TextureOverrideLargeUV.LOD0]", "hash = 22222222",
        "vb1 = ResourceEpsilon",
        "[TextureOverrideIndexed.LOD0]", "hash = 22222222",
        "ib = ResourceGamma", "drawindexed = 3, 0, 0",
        "drawindexed = 3, 3, 0",
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


def test_cross_target_candidates_remain_unresolved(tmp_path):
    ini = "\n".join([
        "[TextureOverrideFirst]", "hash = 11111111",
        "vb0 = ResourceAlpha",
        "draw = 11, 0", "[TextureOverrideSecond]",
        "hash = 22222222", "vb0 = ResourceBeta", "draw = 20, 0",
        "[TextureOverrideIndexed]", "hash = 33333333",
        "ib = ResourceGamma", "drawindexed = 3, 0, 0",
        "[TextureOverrideUV]", "hash = 33333333",
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

    assert draw.position_file is None
    assert draw.geometry_resolution["error"] == "ambiguous_position"


def test_coherence_breaks_legal_position_tie_without_names(tmp_path):
    ini = "\n".join([
        "[TextureOverrideFirst]", "hash = 11111111",
        "vb0 = ResourceAlpha",
        "draw = 30, 0", "[TextureOverrideSecond]",
        "hash = 11111111", "vb0 = ResourceBeta", "draw = 30, 0",
        "[TextureOverrideIndexed]", "hash = 11111111",
        "ib = ResourceGamma", "drawindexed = 30, 0, 0",
        "[TextureOverrideUV]", "hash = 11111111",
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


def test_vertex_binding_events_preserve_order_conditions_and_null():
    sections = parse_sections("sample.ini", text="""
[TextureOverrideArbitrary]
hash = 12345678
if $mode == 0
vb0 = ResourceFirst
else
vb0 = null
endif
vb4 = ResourceFourth
drawindexed = 3, 0, 0
""")

    info = _scan_sections_for_draws(
        sections, gating_vars={"mode"})["TextureOverrideArbitrary"]
    events = info["vertex_binding_events"]

    assert [(event.slot, event.resource, event.order) for event in events] == [
        (0, "ResourceFirst", 0),
        (0, None, 1),
        (4, "ResourceFourth", 2),
    ]
    assert all(event.target_hash == "12345678" for event in events)
    assert events[0].conditions != events[1].conditions


def test_target_family_is_independent_of_section_order(tmp_path):
    sections_text = [
        ("[TextureOverrideTargetAPosition]\nhash = aaaaaaaa\n"
         "vb0 = ResourcePositionA\ndraw = 4, 0\n"),
        ("[TextureOverrideTargetATexcoord]\nhash = aaaaaaaa\n"
         "vb1 = ResourceTexcoordA\n"),
        ("[TextureOverrideTargetAIndex]\nhash = aaaaaaaa\n"
         "ib = ResourceIndexA\ndrawindexed = 3, 0, 0\n"),
        ("[TextureOverrideTargetBPosition]\nhash = bbbbbbbb\n"
         "vb0 = ResourcePositionB\ndraw = 6, 0\n"),
        ("[TextureOverrideTargetBTexcoord]\nhash = bbbbbbbb\n"
         "vb1 = ResourceTexcoordB\n"),
        ("[TextureOverrideTargetBIndex]\nhash = bbbbbbbb\n"
         "ib = ResourceIndexB\ndrawindexed = 3, 0, 0\n"),
    ]
    resources = [
        "[ResourcePositionA]\nfilename = position-a.buf\nstride = 12\n",
        "[ResourceTexcoordA]\nfilename = texcoord-a.buf\nstride = 20\n",
        "[ResourceIndexA]\nfilename = index-a.ib\nformat = DXGI_FORMAT_R32_UINT\n",
        "[ResourcePositionB]\nfilename = position-b.buf\nstride = 12\n",
        "[ResourceTexcoordB]\nfilename = texcoord-b.buf\nstride = 20\n",
        "[ResourceIndexB]\nfilename = index-b.ib\nformat = DXGI_FORMAT_R32_UINT\n",
    ]
    for name, kind, count, values in [
        ("position-a.buf", "position", 4, None),
        ("texcoord-a.buf", "texcoord", 4, None),
        ("index-a.ib", "index", 3, (0, 1, 2)),
        ("position-b.buf", "position", 6, None),
        ("texcoord-b.buf", "texcoord", 6, None),
        ("index-b.ib", "index", 3, (0, 1, 2)),
    ]:
        _write_buffers(tmp_path, [(name, kind, count, values)])

    def resolve(order):
        text = "\n".join(sections_text[index] for index in order)
        parsed = parse_sections("sample.ini", text=text + "\n" +
                                "\n".join(resources))
        groups = build_draw_groups(
            parsed, extract_resources(parsed), mod_dir=tmp_path)
        return {
            group["ib_file"]: (group["position_file"],
                               group["texcoord_file"])
            for group in groups
        }

    expected = {
        "index-a.ib": ("position-a.buf", "texcoord-a.buf"),
        "index-b.ib": ("position-b.buf", "texcoord-b.buf"),
    }
    assert resolve(range(len(sections_text))) == expected
    assert resolve(reversed(range(len(sections_text)))) == expected


def test_tiny_sibling_ib_cannot_steal_another_target_position(tmp_path):
    ini = "\n".join([
        "[TextureOverrideTinyTarget]", "hash = aaaaaaaa",
        "vb0 = ResourceTinyPosition", "vb1 = ResourceTinyTexcoord",
        "ib = ResourceTinyIndex", "drawindexed = 3, 0, 0",
        "[TextureOverrideBodyPosition]", "hash = bbbbbbbb",
        "vb0 = ResourceBodyPosition", "draw = 64, 0",
        "[TextureOverrideBodyTexcoord]", "hash = bbbbbbbb",
        "vb1 = ResourceBodyTexcoord",
        "[TextureOverrideBodyTiny]", "hash = bbbbbbbb",
        "ib = ResourceBodyTinyIndex", "drawindexed = 3, 0, 0",
        "[TextureOverrideBodyLarge]", "hash = bbbbbbbb",
        "ib = ResourceBodyLargeIndex", "drawindexed = 3, 0, 0",
        "[ResourceTinyPosition]", "filename = tiny-position.buf",
        "stride = 12", "[ResourceTinyTexcoord]",
        "filename = tiny-texcoord.buf", "stride = 20",
        "[ResourceTinyIndex]", "filename = tiny.ib",
        "format = DXGI_FORMAT_R32_UINT", "[ResourceBodyPosition]",
        "filename = body-position.buf", "stride = 12",
        "[ResourceBodyTexcoord]", "filename = body-texcoord.buf",
        "stride = 20", "[ResourceBodyTinyIndex]",
        "filename = body-tiny.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourceBodyLargeIndex]", "filename = body-large.ib",
        "format = DXGI_FORMAT_R32_UINT",
    ])
    sections = parse_sections("sample.ini", text=ini)
    _write_buffers(tmp_path, [
        ("tiny-position.buf", "position", 3, None),
        ("tiny-texcoord.buf", "texcoord", 3, None),
        ("tiny.ib", "index", 3, (0, 1, 2)),
        ("body-position.buf", "position", 64, None),
        ("body-texcoord.buf", "texcoord", 64, None),
        ("body-tiny.ib", "index", 3, (0, 1, 2)),
        ("body-large.ib", "index", 3, (61, 62, 63)),
    ])

    groups = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)
    by_ib = {group["ib_file"]: group for group in groups}

    assert by_ib["body-tiny.ib"]["position_file"] == "body-position.buf"
    assert by_ib["body-large.ib"]["position_file"] == "body-position.buf"


def test_captured_vb0_keeps_target_provenance(tmp_path):
    ini = "\n".join([
        "[TextureOverrideCapture]", "hash = bbbbbbbb",
        "ResourceRuntime = copy vb0",
        "[TextureOverrideDraw]", "hash = bbbbbbbb",
        "ib = ResourceIndex", "vb0 = ResourceRuntime",
        "drawindexed = 3, 0, 0", "[TextureOverridePosition]",
        "hash = bbbbbbbb", "vb0 = ResourcePosition",
        "[TextureOverrideTexcoord]", "hash = bbbbbbbb",
        "vb1 = ResourceTexcoord", "[ResourceIndex]",
        "filename = index.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourcePosition]", "filename = position.buf", "stride = 12",
        "[ResourceTexcoord]", "filename = texcoord.buf", "stride = 20",
        "[ResourceRuntime]",
    ])
    sections = parse_sections("sample.ini", text=ini)
    _write_buffers(tmp_path, [
        ("index.ib", "index", 3, (0, 1, 2)),
        ("position.buf", "position", 3, None),
        ("texcoord.buf", "texcoord", 3, None),
    ])

    draw = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0][
            "draws"][0]

    assert draw.geometry_resolution["position_resource"] == "ResourceRuntime"
    assert draw.position_file == "position.buf"
    assert draw.geometry_resolution["source"] == "captured_slot"


def test_transformed_runtime_position_uses_authored_lineage(tmp_path):
    ini = "\n".join([
        "[TextureOverrideDraw]", "hash = cccccccc",
        "ib = ResourceIndex", "run = CommandListDraw",
        "[CommandListDraw]", "run = CommandListShapeKeys",
        "vb0 = ResourceShapeKeyedPosition", "drawindexed = 3, 0, 0",
        "[CommandListShapeKeys]", "cs-t1 = ResourcePositionBuffer",
        "cs-t2 = ResourceBlend", "cs-u0 = ResourceShapeKeyedPosition",
        "cs-u0 = null", "cs-t1 = null", "cs-t2 = null",
        "[TextureOverrideTexcoord]", "hash = cccccccc",
        "vb1 = ResourceTexcoord", "[ResourceIndex]",
        "filename = index.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourcePositionBuffer]", "filename = position.buf",
        "stride = 12", "[ResourceBlend]", "filename = blend.buf",
        "stride = 32", "[ResourceShapeKeyedPosition]",
        "[ResourceTexcoord]", "filename = texcoord.buf", "stride = 20",
    ])
    sections = parse_sections("sample.ini", text=ini)
    _write_buffers(tmp_path, [
        ("index.ib", "index", 3, (0, 1, 2)),
        ("position.buf", "position", 3, None),
        ("blend.buf", "position", 3, None),
        ("texcoord.buf", "texcoord", 3, None),
    ])

    draw = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0][
            "draws"][0]

    assert draw.geometry_resolution["position_resource"] == \
        "ResourceShapeKeyedPosition"
    assert draw.position_file == "position.buf"
    assert draw.geometry_resolution["source"] == "transform_lineage"


def test_unproven_cross_target_position_candidates_do_not_guess(tmp_path):
    ini = "\n".join([
        "[TextureOverrideUnknown]", "hash = aaaaaaaa",
        "ib = ResourceIndex", "drawindexed = 3, 0, 0",
        "[TextureOverridePositionA]", "hash = bbbbbbbb",
        "vb0 = ResourcePositionA", "draw = 10, 0",
        "[TextureOverridePositionB]", "hash = cccccccc",
        "vb0 = ResourcePositionB", "draw = 20, 0",
        "[TextureOverrideTexcoord]", "hash = aaaaaaaa",
        "vb1 = ResourceTexcoord", "[ResourceIndex]",
        "filename = index.ib", "format = DXGI_FORMAT_R32_UINT",
        "[ResourcePositionA]", "filename = position-a.buf",
        "stride = 12", "[ResourcePositionB]",
        "filename = position-b.buf", "stride = 12",
        "[ResourceTexcoord]", "filename = texcoord.buf", "stride = 20",
    ])
    sections = parse_sections("sample.ini", text=ini)
    _write_buffers(tmp_path, [
        ("index.ib", "index", 3, (0, 1, 2)),
        ("position-a.buf", "position", 10, None),
        ("position-b.buf", "position", 20, None),
        ("texcoord.buf", "texcoord", 3, None),
    ])

    draw = build_draw_groups(
        sections, extract_resources(sections), mod_dir=tmp_path)[0][
            "draws"][0]

    assert draw.position_file is None
    assert draw.geometry_resolution["error"] == "ambiguous_position"
