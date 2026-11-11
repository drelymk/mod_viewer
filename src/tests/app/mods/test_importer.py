"""Session import preserves exact geometry and leaves the source package intact."""

import re
import struct
from types import SimpleNamespace

import pytest

from app.bridge.mod_preview import ModPreview
from app.mods.analysis import analyze_mod_inis, build_mod_ini_snapshot
from app.mods.importer import ModImportError, plan_import
from app.mods.loader import _resolve_context
from app.session import edit as edit_session
from core.geometry.semantics import deduplicate_draws
from core.resource_paths import safe_resource_path
from tests.support.provenance import visible


def _variant_mod(root, *, layout=(40, 32, 20), processing="shared", disabled=(),
                 reordered=False, shared_blend=False, material_calls=False,
                 unconditional_auto=False, parentheses=False,
                 material_variants=False, unrelated_draw=False, split_body=False,
                 rounding_noise=False, command_style="spaced", direct_auto=False):
    root.mkdir(exist_ok=True)
    parts = ["; preserve this comment", "[Constants]", "global persist $mode = 0",
             "[KeyMode]", "key = k", "type = cycle", "$mode = 0,1,2"]
    for name, slot, suffix in [("Position", "vb0", "Position"),
                               ("Blend", "vb1", "Blend"),
                               ("Texcoord", "vb1", "Texcoord")]:
        parts.extend([f"[TextureOverrideUnit{name}]", f"hash = a100000{len(parts) % 9}",
                      f"run = CommandListUnit{name}", f"[CommandListUnit{name}]"])
        order = [2, 0, 1] if reordered and name != "Position" else list(range(3))
        if name == "Blend" and shared_blend:
            parts.append("vb1 = ResourceUnitBlend.0")
        else:
            for ordinal, variant in enumerate(order):
                predicate = f"$mode == {variant}"
                if parentheses:
                    predicate = f"({predicate})"
                parts.extend([f"{'if' if ordinal == 0 else 'else if'} {predicate}",
                              f"{slot} = ResourceUnit{suffix}.{variant}"])
                if name == "Blend" and processing == "variant":
                    parts.extend(["handling = skip", "draw = 6,0 ; processing comment"])
            parts.append("endif")
        if name == "Blend" and processing == "shared":
            parts.extend(["handling = skip", "draw = 6,0 ; processing comment"])
    parts.extend(["[TextureOverrideUnitIB]", "hash = a2000001",
                  "run = CommandListUnitIB", "[CommandListUnitIB]"])
    if unconditional_auto:
        parts.extend(["handling = skip", "drawindexed = auto ; source draw comment"])
    else:
        for variant in range(3):
            predicate = f"$mode == {variant}"
            if parentheses:
                predicate = f"({predicate})"
            parts.extend([f"{'if' if variant == 0 else 'else if'} {predicate}",
                          "handling = skip", "drawindexed = auto ; source draw comment"])
        parts.append("endif")
    for component, first in [("Head", 0), ("Body", 3)]:
        parts.extend([f"[TextureOverrideUnit{component}]", "hash = a2000001",
                      f"match_first_index = {first}", f"run = CommandListUnit{component}",
                      f"[CommandListUnit{component}]"])
        for ordinal, variant in enumerate([1, 2, 0] if reordered else range(3)):
            predicate = f"$mode == {variant}"
            if parentheses:
                predicate = f"({predicate})"
            resource = "null" if (variant, component) in disabled else f"ResourceUnit{component}IB.{variant}"
            parts.extend([f"{'if' if ordinal == 0 else 'else if'} {predicate}",
                          f"ib = {resource}",
                          f"ps-t0 = ResourceUnitNormalMap{variant if material_variants else ''}"])
            if material_calls:
                parts.append(r"run = CommandList\shared\MaterialHook")
        parts.append("endif")
    original = {}
    for variant in range(3):
        folder = root / f"variant{variant:02}"
        folder.mkdir()
        if material_variants:
            (folder / "NormalMap.dds").write_bytes(f"texture {variant}".encode())
            parts.extend([f"[ResourceUnitNormalMap{variant}]", f"filename = {folder.name}/NormalMap.dds"])
        order = [0, 1, 2, 3, 4, 5] if variant != 1 else [3, 4, 5, 2, 0, 1]
        rows = []
        for index in order:
            changed = variant == 2 and index >= 3
            position = struct.pack("<10f", float(index), float(index % 2), 0,
                                   0, 0, -1 if changed else 1, 1, 0, 0, 1)[:layout[0]]
            if rounding_noise and variant == 1:
                noisy = bytearray(position)
                struct.pack_into("<f", noisy, 20, 1.0000001192092896)
                struct.pack_into("<f", noisy, 24, 1.0000001192092896)
                position = bytes(noisy)
            blend = struct.pack("<4f4I", 1, 0, 0, 0, 7 if changed and not shared_blend else 2, 0, 0, 0)
            u, v = index / 6, (index % 3) / 3 + (0.125 if changed else 0)
            uv = (struct.pack("<2f", u, v) if layout[2] == 8
                  else struct.pack("<I4f", 0, u, v, 0, 0)[:layout[2]])
            rows.append((position, blend, uv))
        for column, name, stride in zip(range(3), ("Position", "Blend", "Texcoord"), layout):
            raw = b"".join(row[column] for row in rows)
            path = folder / f"Unit{name}.buf"
            path.write_bytes(raw)
            parts.extend([f"[ResourceUnit{name}.{variant}]", "type = Buffer",
                          f"stride = {stride}", f"filename = {folder.name}/{path.name} ; keep"])
        for component, ids in [("Head", (0, 1, 2)), ("Body", (3, 4, 5))]:
            if component == "Body" and split_body:
                ids = (0, 1, 2, 3, 4, 5)
            indices = [order.index(index) for index in ids]
            path = folder / f"Unit{component}.ib"
            path.write_bytes(struct.pack(f"<{len(indices)}H", *indices))
            original[variant, component] = [b"".join(rows[index]) for index in indices]
            parts.extend([f"[ResourceUnit{component}IB.{variant}]", "type = Buffer",
                          "format = DXGI_FORMAT_R16_UINT", f"filename = {folder.name}/{path.name}"])
    parts.extend(["[ResourceUnitNormalMap]", "filename = UnitNormalMap.dds"])
    if unrelated_draw:
        parts.extend(["[CommandListUnrelated]", "draw = 3,0 ; unrelated processing"])
    (root / "UnitNormalMap.dds").write_bytes(b"texture fixture")
    ini = root / "Unit.ini"
    # Untouched lines retain their individual terminators and the BOM.
    text = "\ufeff" + "\r\n".join(parts) + "\n; final comment"
    if direct_auto:
        text = text.replace("run = CommandListUnitIB\r\n[CommandListUnitIB]\r\n",
                            "match_priority = 1 ; owner metadata\r\n")
    if command_style == "compact":
        text = text.replace(" = ", "=")
    elif command_style == "pre":
        text = re.sub(r"(?m)^(draw(?:indexed)?|handling)(\s*=)", r"pre \1\2", text)
    ini.write_bytes(text.encode("utf-8"))
    return ini, original


def _snapshot(root):
    return build_mod_ini_snapshot(
        edit_session.document_paths(str(root)), str(root),
        edit_session.documents_for(str(root)),
        source=edit_session.source_for(str(root)), require_documents=True)


@pytest.mark.parametrize("options", [
    {}, {"processing": "variant", "material_calls": True, "disabled": ((1, "Body"),)},
], ids=["shared-processing", "conditional-processing"])
def test_import_lifecycle_deduplicates_exact_records_and_keeps_controls_and_sources(
        tmp_path, monkeypatch, options):
    ini, original = _variant_mod(tmp_path, **options)
    disabled = set(options.get("disabled", ()))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    preview = ModPreview(SimpleNamespace(mod_folder=lambda path: path))
    monkeypatch.setattr(preview, "_load_asset_folders", lambda: [])
    result = preview.import_mod(str(tmp_path))
    assert "error" not in result, result
    assert result["metadata"]["import"]["shared_vertices"] == 9
    assert result["metadata"]["import"]["input_vertices"] == 18
    assert result["metadata"]["import"]["unique_draws"] == 3
    assert len(result["meshes"]) == 3
    assert edit_session.has_pending(str(tmp_path))
    staged = edit_session.peek(str(tmp_path), str(ini)).to_string()
    assert staged.startswith("\ufeff; preserve this comment\r\n")
    assert staged.endswith("\n; final comment")
    assert " ; keep\r\n" in staged
    assert "draw = 9, 0 ; processing comment\r\n" in staged
    assert "; source draw comment" in staged
    snapshot = _snapshot(tmp_path)
    parsed = analyze_mod_inis(snapshot)
    overrides = edit_session.ib_overrides_for(str(tmp_path))
    assert len(overrides) == 5
    for group in parsed.groups:
        draws = deduplicate_draws(group)
        component = "Head" if group["name"].endswith("Head") else "Body"
        assert len(draws) == (1 if component == "Head" else 2)
        assert sum(len(draw.sources) for draw in draws) == sum(
            (variant, component) not in disabled for variant in range(3))
        for variant in range(3):
            active = [draw for draw in draws if visible(draw.conditions, {"mode": str(variant)})]
            if (variant, component) in disabled:
                assert active == []
                continue
            assert len(active) == 1
            draw = active[0]
            indices = struct.unpack_from(f"<{draw.count}I", overrides[safe_resource_path(str(tmp_path), draw.ib_file)], draw.start * 4)
            files = [draw.position_file, draw.skinning_source.file, draw.texcoord_file]
            streams = [overrides[safe_resource_path(str(tmp_path), file)] for file in files]
            reconstructed = [b"".join(raw[index * stride:(index + 1) * stride]
                                      for raw, stride in zip(streams, (40, 32, 20))) for index in indices]
            assert reconstructed == original[variant, component]
    semantic = preview.get_semantic_state(str(tmp_path))
    assert len(semantic["meshes"]) == 3
    reloaded = preview.load_mod(str(tmp_path))
    assert set(reloaded["meshes"]) == set(result["meshes"])
    assert reloaded["metadata"]["import"] == result["metadata"]["import"]
    assert preview.import_mod(str(tmp_path))["metadata"]["import"] == result["metadata"]["import"]
    assert edit_session.export(str(tmp_path))["error"] == "Export for imported mods is not supported yet."
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    edit_session.discard(str(tmp_path))
    assert not edit_session.ib_overrides_for(str(tmp_path))
    assert edit_session.import_summary_for(str(tmp_path)) is None
    assert preview.load_mod(str(tmp_path))["metadata"].get("import") is None
    edit_session.discard(str(tmp_path))


@pytest.mark.parametrize("damage", ["indices", "stream", "overlap", "sandbox", "selector_write", "uv_encoding"])
def test_import_rejects_unsafe_or_incompatible_input_without_mutation(tmp_path, damage):
    ini, _original = _variant_mod(tmp_path)
    if damage == "indices":
        (tmp_path / "variant02" / "UnitBody.ib").write_bytes(struct.pack("<3H", 0, 1, 99))
    elif damage == "stream":
        (tmp_path / "variant02" / "UnitBlend.buf").write_bytes(bytes(32))
    elif damage == "uv_encoding":
        (tmp_path / "variant02" / "UnitTexcoord.buf").write_bytes(b"".join(
            struct.pack("<5f", index / 6, index % 2, 99, 9999, 0) for index in range(6)))
    else:
        text = ini.read_text(encoding="utf-8")
        if damage == "overlap":
            text = text.replace("$mode == 1", "$mode == 0")
        elif damage == "selector_write":
            text = text.replace("ib = ResourceUnitHeadIB.0", "ib = ResourceUnitHeadIB.0\n$mode = 2")
        else:
            text = text.replace("variant02/UnitPosition.buf", "../../../outside.buf")
        ini.write_text(text, encoding="utf-8")
    context = _resolve_context(str(tmp_path))
    baseline = {path: doc.to_string() for path, doc in context.docs.items()}
    with pytest.raises((ModImportError, OSError)):
        plan_import(context)
    assert {path: doc.to_string() for path, doc in context.docs.items()} == baseline


@pytest.mark.parametrize("failure", ["load", "buffer-limit"])
def test_failed_import_rolls_back_documents_and_converted_bytes(tmp_path, monkeypatch, failure):
    ini, _original = _variant_mod(tmp_path)
    preview = ModPreview(SimpleNamespace(mod_folder=lambda path: path))
    preview.authoritative_context(str(tmp_path), defer_assets=True)
    baseline = edit_session.peek(str(tmp_path), str(ini)).to_string()
    if failure == "load":
        monkeypatch.setattr(preview, "load_mod", lambda *_args: {"error": "fixture failure"})
    else:
        monkeypatch.setattr("core.geometry.buffers._MAX_BUFFER_FILE_BYTES", 1)
    result = preview.import_mod(str(tmp_path))
    assert (result == {"error": "fixture failure"} if failure == "load"
            else "Buffer file is too large" in result["error"])
    assert edit_session.peek(str(tmp_path), str(ini)).to_string() == baseline
    assert not edit_session.ib_overrides_for(str(tmp_path))
    assert not edit_session.has_pending(str(tmp_path))
    edit_session.discard(str(tmp_path))


@pytest.mark.parametrize("options", [
    {"processing": "variant", "disabled": ((0, "Body"), (1, "Body"), (2, "Body")),
     "material_calls": True},
    {"disabled": ((1, "Body"),), "material_calls": True},
    {"reordered": True},
    {"shared_blend": True},
    {"layout": (24, 32, 12)},
    {"layout": (40, 32, 8)},
    {"parentheses": True},
    {"unconditional_auto": True, "disabled": ((1, "Body"),)},
    {"material_variants": True, "material_calls": True},
    {"unrelated_draw": True},
    {"direct_auto": True},
    {"command_style": "compact"},
    {"command_style": "compact", "direct_auto": True},
    {"command_style": "pre"},
    {"command_style": "pre", "direct_auto": True},
], ids=["variant-processing-and-disabled-component", "mixed-null", "reordered-selectors",
        "shared-stream", "compact-position-and-uv", "short-uv", "parentheses", "shared-auto",
        "distinct-materials", "unrelated-processing", "direct-auto", "compact-commands",
        "direct-compact-commands", "pre-commands", "direct-pre-commands"])
def test_generic_variant_layouts_preserve_each_visible_draw(tmp_path, options):
    ini, original = _variant_mod(tmp_path, **options)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    context = _resolve_context(str(tmp_path))
    plan = plan_import(context)
    snapshot = build_mod_ini_snapshot(context.ini_paths, str(tmp_path), plan.documents,
                                      source=context.source)
    groups = analyze_mod_inis(snapshot).groups
    strides = options.get("layout", (40, 32, 20))
    disabled = set(options.get("disabled", ()))
    for component in ("Head", "Body"):
        draws = [draw for group in groups if group["name"].endswith(component)
                 for draw in deduplicate_draws(group)]
        for variant in range(3):
            active = [draw for draw in draws if visible(draw.conditions, {"mode": str(variant)})]
            if (variant, component) in disabled:
                assert active == []
                continue
            assert len(active) == 1
            draw = active[0]
            indices = struct.unpack_from(f"<{draw.count}I", plan.buffers[safe_resource_path(str(tmp_path), draw.ib_file)], draw.start * 4)
            streams = [plan.buffers[safe_resource_path(str(tmp_path), file)]
                       for file in (draw.position_file, draw.skinning_source.file, draw.texcoord_file)]
            assert [b"".join(raw[index * stride:(index + 1) * stride]
                             for raw, stride in zip(streams, strides)) for index in indices] == original[variant, component]
            expected_map = f"variant{variant:02}/NormalMap.dds" if options.get("material_variants") else "UnitNormalMap.dds"
            assert draw.normal_map_default_file.replace("\\", "/") == expected_map
    staged = plan.documents[str(ini)].to_string()
    if options.get("processing") == "variant":
        assert staged.count("draw = 9, 0 ; processing comment") == 3
    if options.get("material_calls"):
        assert staged.count(r"run = CommandList\shared\MaterialHook") == 6
    if options.get("unrelated_draw"):
        assert "draw = 3,0 ; unrelated processing" in staged
    if options.get("direct_auto"):
        converted = plan.documents[str(ini)]
        owner = converted.section("TextureOverrideUnitIB")
        assert any("owner metadata" in line.raw for line in owner.lines)
        assert staged.count("owner metadata") == 1
        for component in ("Head", "Body"):
            component_section = converted.section(f"TextureOverrideUnit{component}")
            assert [line.text.replace(" ", "") for line in component_section.lines
                    if line.text.startswith("hash")] == ["hash=a2000001"]
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize("command", ["run = CommandListExternal", "$state = 1", "post handling = skip"])
def test_unsupported_auto_commands_report_the_authored_location_without_mutation(tmp_path, command):
    ini, _original = _variant_mod(tmp_path)
    text = ini.read_text(encoding="utf-8")
    text = text.replace("drawindexed = auto ; source draw comment",
                        f"{command}\ndrawindexed = auto ; source draw comment", 1)
    ini.write_text(text, encoding="utf-8")
    context = _resolve_context(str(tmp_path))
    baseline = context.docs[str(ini)].to_string()
    line_no = next(index for index, line in enumerate(text.splitlines(), 1) if line == command)
    with pytest.raises(ModImportError) as error:
        plan_import(context)
    assert str(error.value) == (
        f"Unit.ini [CommandListUnitIB], line {line_no}: unsupported automatic draw command: {command}")
    assert context.docs[str(ini)].to_string() == baseline
    assert ini.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("material_variants", [False, True])
def test_import_shares_partial_outfits_and_retains_exact_shading_variants(tmp_path, monkeypatch, material_variants):
    ini, original = _variant_mod(tmp_path, split_body=True, material_variants=material_variants)
    baseline = ini.read_bytes()
    preview = ModPreview(SimpleNamespace(mod_folder=lambda path: path))
    monkeypatch.setattr(preview, "_load_asset_folders", lambda: [])
    result = preview.import_mod(str(tmp_path))
    assert "error" not in result, result
    parts = [entry for entry in result["meshes"].values() if entry["identity"]["component"].endswith("Body")]
    assert len(parts) == (6 if material_variants else 3)
    assert all("import_part" not in entry for entry in result["meshes"].values())
    assert result["metadata"]["import"]["unique_draws"] == 4
    staged = edit_session.peek(str(tmp_path), str(ini))
    assert {section.name for section in staged.sections} == {
        section.name for section in _resolve_context(str(tmp_path)).docs[str(ini)].sections}
    assert all(source["section"] == "TextureOverrideUnitBody" for entry in parts for source in entry["sources"])
    for variant in range(3):
        active = [entry for entry in parts if visible(entry.get("conditions", []), {"mode": str(variant)})]
        assert len(active) == 2
    snapshot = _snapshot(tmp_path)
    parsed = analyze_mod_inis(snapshot)
    buffers = edit_session.ib_overrides_for(str(tmp_path))
    body = next(group for group in parsed.groups if group["name"].endswith("Body"))
    for variant in range(3):
        reconstructed = []
        for draw in body["draws"]:
            if not visible(draw.conditions, {"mode": str(variant)}):
                continue
            expected_map = f"variant{variant:02}/NormalMap.dds" if material_variants else "UnitNormalMap.dds"
            assert draw.normal_map_default_file.replace("\\", "/") == expected_map
            indices = struct.unpack_from(f"<{draw.count}I", buffers[safe_resource_path(str(tmp_path), draw.ib_file)], draw.start * 4)
            streams = [buffers[safe_resource_path(str(tmp_path), file)] for file in
                       (draw.position_file, draw.skinning_source.file, draw.texcoord_file)]
            reconstructed.extend(b"".join(raw[index * stride:(index + 1) * stride]
                                         for raw, stride in zip(streams, (40, 32, 20))) for index in indices)
        assert reconstructed == original[variant, "Body"]
    semantics = preview.get_semantic_state(str(tmp_path))
    assert set(semantics["meshes"]) == set(result["meshes"])
    assert all("import_part" not in entry for entry in semantics["meshes"].values())
    reloaded = preview.load_mod(str(tmp_path))
    assert set(reloaded["meshes"]) == set(result["meshes"])
    assert ini.read_bytes() == baseline
    assert edit_session.export(str(tmp_path))["error"]
    edit_session.discard(str(tmp_path))


def test_partial_import_keeps_geometric_normal_fallback_whole(tmp_path):
    _ini, _original = _variant_mod(tmp_path, split_body=True)
    for path in tmp_path.rglob("UnitPosition.buf"):
        data = bytearray(path.read_bytes())
        for offset in range(0, len(data), 40):
            struct.pack_into("<fff", data, offset + 12, 0, 0, 0)
        path.write_bytes(data)
    plan = plan_import(_resolve_context(str(tmp_path)))
    parsed = analyze_mod_inis(build_mod_ini_snapshot(
        list(plan.documents), str(tmp_path), plan.documents))
    body = next(group for group in parsed.groups if group["name"].endswith("Body"))
    assert all(draw.count == 6 for draw in body["draws"])


def test_whole_part_import_merges_export_rounding_but_preserves_protected_attributes(tmp_path, monkeypatch):
    ini, original = _variant_mod(tmp_path, split_body=True, rounding_noise=True)
    baseline = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    preview = ModPreview(SimpleNamespace(mod_folder=lambda path: path))
    monkeypatch.setattr(preview, "_load_asset_folders", lambda: [])
    result = preview.import_mod(str(tmp_path))
    assert "error" not in result, result
    assert result["metadata"]["import"]["canonicalized_vertices"] == 6
    assert len(result["meshes"]) == 4
    body = next(group for group in analyze_mod_inis(_snapshot(tmp_path)).groups if group["name"].endswith("Body"))
    buffers = edit_session.ib_overrides_for(str(tmp_path))
    for variant in range(3):
        actual = []
        for draw in body["draws"]:
            if not visible(draw.conditions, {"mode": str(variant)}):
                continue
            indices = struct.unpack_from(f"<{draw.count}I", buffers[safe_resource_path(str(tmp_path), draw.ib_file)], draw.start * 4)
            streams = [buffers[safe_resource_path(str(tmp_path), file)] for file in
                       (draw.position_file, draw.skinning_source.file, draw.texcoord_file)]
            actual.extend(b"".join(raw[index * stride:(index + 1) * stride]
                                  for raw, stride in zip(streams, (40, 32, 20))) for index in indices)
        for before, after in zip(original[variant, "Body"], actual, strict=True):
            assert before[:12] + before[36:] == after[:12] + after[36:]
            assert all(abs(left - right) <= 1e-6 for left, right in zip(
                struct.unpack_from("<6f", before, 12), struct.unpack_from("<6f", after, 12)))
    assert baseline == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert edit_session.peek(str(tmp_path), str(ini)).has_bom
    edit_session.discard(str(tmp_path))
