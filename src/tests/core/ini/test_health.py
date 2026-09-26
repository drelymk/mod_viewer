"""Focused coverage for the conservative INI health report."""

import json
import os
import tempfile
import zipfile


from app.mods import loader as mod_loader
from core.ini.health import analyze_mod
from core.mod_source import ZipModSource


def _write(path, value, binary=False):
    mode = "wb" if binary else "w"
    kwargs = {} if binary else {"encoding": "utf-8", "newline": ""}
    with open(path, mode, **kwargs) as fh:
        fh.write(value)


def _codes(report):
    return [item["code"] for item in report["issues"]]


def test_structure_resource_and_file_findings():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, (
            "[TextureOverrideComponent01]\n"
            "if $x == 1\n"
            "ib = ResourceComponent01IB\n"
            "endif\n"
            "endif\n"
            "vb0 = ResourceNoSection\n"
            "[ResourceComponent01IB]\n"
            "filename = missing.buf\n"
            "stride = 0\n"
            "[ResourceUnused]\n"
            "filename = spare.buf\n"))
        _write(os.path.join(tmp, "spare.buf"), b"x", binary=True)
        _write(os.path.join(tmp, "orphan.dds"), b"x", binary=True)
        report = analyze_mod(tmp)
        codes = _codes(report)

    assert ("malformed_condition_nesting" in codes), ("unmatched endif is reported")
    nesting = next(item for item in report["issues"]
                   if item["code"] == "malformed_condition_nesting")
    assert (nesting["severity"] == "error"), ("malformed condition nesting has error severity")
    assert (nesting["line"] == 5 and nesting["source"] == "endif"), ("condition error has a 1-based line and source excerpt")
    assert nesting["reason"] == "endif_without_if"
    assert ("missing_resource_section" in codes), ("direct local buffer binding without a declaration is reported")
    assert ("missing_resource_file" in codes), ("used resource with a missing file is an error")
    assert ("invalid_resource_stride" in codes), ("used resource with non-positive stride is an error")
    stride = next(item for item in report["issues"]
                  if item["code"] == "invalid_resource_stride")
    assert stride["stride"] == "0"
    assert ("unused_resource_section" in codes), ("unreferenced resource declaration is reported")
    assert ("unreferenced_asset_file" in codes), ("asset not declared by an active INI is reported")




def test_reference_graph_case_exactness_and_comments():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, (
            "[TextureOverrideComponent01]\n"
            "ib = resourceRuntime\n"
            "; ib = ResourceCommented\n"
            "[ResourceRuntime]\n"
            "source = RESOURCEBridge\n"
            "[ResourceBridge]\n"
            "source = ResourceComponent01IB\n"
            "[ResourceComponent01]\n"
            "filename = component01.buf\n"
            "[ResourceComponent01IB]\n"
            "filename = component01_ib.buf\n"
            "[ResourceCommented]\n"
            "filename = commented.buf\n"))
        for name in ("component01.buf", "component01_ib.buf", "commented.buf"):
            _write(os.path.join(tmp, name), b"x", binary=True)
        report = analyze_mod(tmp)
        unused = {item["resource"] for item in report["issues"]
                  if item["code"] == "unused_resource_section"}

    assert ("ResourceRuntime" not in unused and "ResourceBridge" not in unused
          and "ResourceComponent01IB" not in unused), ("case-insensitive transitive resource references are followed")
    assert ("ResourceComponent01" in unused), ("resource names are matched exactly, not by prefix")
    assert ("ResourceCommented" in unused), ("commented references do not make a resource used")


def test_resource_reachability_follows_authored_edges_only():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[TextureOverrideComponent01]\n"
            "ib = ResourcePosition\n"
            "[ResourcePosition]\n"
            "[ResourcePosition.B]\n"
            "filename = pose.buf\n"
            "[Present]\n"
            "ResourceCopied = copy ResourceCopied.B\n"
            "[ResourceCopied]\n"
            "[ResourceCopied.B]\n"
            "filename = copied-pose.buf\n"
            "[ResourceCycleA]\n"
            "source = ResourceCycleB\n"
            "[ResourceCycleB]\n"
            "source = ResourceCycleA\n"
            "[ResourceSelf]\n"
            "source = ResourceSelf\n"))
        _write(os.path.join(tmp, "pose.buf"), b"x", binary=True)
        _write(os.path.join(tmp, "copied-pose.buf"), b"x", binary=True)
        report = analyze_mod(tmp)
        unused = {item["resource"] for item in report["issues"]
                  if item["code"] == "unused_resource_section"}

    assert ("ResourcePosition" not in unused
          and "ResourcePosition.B" in unused), ("only authored edges make a B-suffixed resource reachable")
    assert ("ResourceCopied" not in unused
          and "ResourceCopied.B" not in unused), ("resource copies keep their authored targets reachable")
    assert ({"ResourceCycleA", "ResourceCycleB", "ResourceSelf"} <= unused), ("self references and unrooted cycles do not make resources used")


def test_reference_prefix_is_valid_and_target_is_checked():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[TextureOverrideComponent01]\n"
            "vb0 = reference ResourceComponent01Position\n"
            "vb1 = reference ResourceMissing\n"
            "vb2 = copy reference ResourceMissingCopy\n"
            "[ResourceComponent01Position]\n"
            "filename = component01.buf\n"
            "stride = 12\n"))
        _write(os.path.join(tmp, "component01.buf"), b"x", binary=True)
        report = analyze_mod(tmp)

    malformed = [item for item in report["issues"]
                 if item["code"] == "malformed_resource_reference"]
    missing = [item for item in report["issues"]
               if item["code"] == "missing_resource_section"]
    unused = {item["resource"] for item in report["issues"]
              if item["code"] == "unused_resource_section"}
    assert malformed == []
    assert [(item["resource"], item["line"]) for item in missing] == [
        ("ResourceMissing", 3),
        ("ResourceMissingCopy", 4),
    ]
    assert "ResourceComponent01Position" not in unused


def test_file_classification_and_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, "[TextureOverrideComponent01]\nib = ResourceIB\n[ResourceIB]\nfilename = active.buf\n")
        _write(os.path.join(tmp, "DISABLED-old.ini"),
               "[ResourceOld]\nfilename = inactive.dds\n")
        _write(os.path.join(tmp, "active.buf"), b"x", binary=True)
        _write(os.path.join(tmp, "inactive.dds"), b"x", binary=True)
        _write(os.path.join(tmp, "viewer.png"), b"x", binary=True)
        _write(os.path.join(tmp, "orphan.tga"), b"x", binary=True)
        _write(os.path.join(tmp, "active-20260901152230.dds"), b"x", binary=True)
        with open(os.path.join(tmp, ".mod_viewer.json"), "w", encoding="utf-8") as fh:
            json.dump({"textures": {"Component01::whole": {
                "tex_key": "viewer.png", "label": "viewer", "manual": True,
            }}}, fh)

        staged = (
            "[TextureOverrideComponent01]\nif $x == 1\nib = ResourceIB\n"
            "[ResourceIB]\nfilename = active.buf\n")
        report = analyze_mod(tmp, overrides={ini: staged})

    assert (report["files"] == {"unreferenced": 2, "inactive_only": 1,
                              "viewer_only": 1, "referenced": 1}), (f"active, inactive-only, viewer-only and unused assets classify separately ({report['files']})")
    assert ("malformed_condition_nesting" in _codes(report)), ("staged in-memory INI text is analyzed instead of stale disk text")


def test_zip_health_counts_deep_disabled_assets_as_inactive(tmp_path):
    archive_path = tmp_path / "health.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Export/active.ini",
            "[TextureOverrideComponent01]\ndrawindexed = 3, 0, 0\n")
        archive.writestr(
            "Export/variants/DISABLED-old.ini",
            "[ResourceOld]\nfilename = inactive.dds\n")
        archive.writestr("Export/variants/inactive.dds", b"inactive")

    source = ZipModSource(archive_path)
    report = analyze_mod(str(archive_path), source=source)

    assert report["files"]["inactive_only"] == 1
    assert report["files"]["unreferenced"] == 0


def test_unsafe_paths_and_namespaced_resources():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, (
            "[TextureOverrideComponent01]\n"
            "ib = ResourceUnsafe\n"
            "vb0 = Resource\\Framework\\Position\n"
            "[ResourceUnsafe]\n"
            "filename = ..\\..\\escape.buf\n"))
        report = analyze_mod(tmp)
        codes = _codes(report)

    assert ("unsafe_resource_path" in codes), ("resource path beyond the loader's allowed escape depth is an error")
    namespaced_missing = [item for item in report["issues"]
                          if item["code"] == "missing_resource_section"
                          and "Framework" in item["message"]]
    assert (not namespaced_missing), ("namespaced framework resources are not guessed to be missing")


def test_statement_run_target_and_key_binding_findings():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[Constants]\n"
            "global $active = 1\n"
            "[KeyFirst]\n"
            "condition = $active\n"
            "key = no_modifiers ;\n"
            "type = cycle\n"
            "$first = 0,1\n"
            "'\n"
            "[KeySecond]\n"
            "condition = $active\n"
            "key = ;\n"
            "type = cycle\n"
            "$second = 0,1\n"
            "[TextureOverrideComponent01]\n"
            "ps-t8 = ef ResourceGlow\n"
            "run = CommandListMissing\n"
            "run = CommandListKnown\n"
            "run = CommandList\\Framework\\External\n"
            "run = BuiltInCommandListUnbindAllRenderTargets\n"
            "[CommandListKnown]\n"
            "ps-t8 = ref ResourceGlow\n"
            "[ResourceGlow]\n"
            "filename = glow.dds\n"))
        _write(os.path.join(tmp, "glow.dds"), b"DDS " + b"\0" * 124,
               binary=True)
        report = analyze_mod(tmp)

    by_code = {}
    for issue in report["issues"]:
        by_code.setdefault(issue["code"], []).append(issue)
    assert len(by_code["unexpected_key_statement"]) == 1
    assert by_code["unexpected_key_statement"][0]["line"] == 8
    assert len(by_code["malformed_resource_reference"]) == 1
    assert by_code["malformed_resource_reference"][0]["resource"] == (
        "ResourceGlow")
    assert len(by_code["duplicate_key_binding"]) == 1
    duplicate = by_code["duplicate_key_binding"][0]
    assert duplicate["other_section"] == "KeyFirst"
    assert duplicate["section"] == "KeySecond"
    assert [issue["target"] for issue in
            by_code["missing_local_run_target"]] == ["CommandListMissing"]


def test_drawindexed_viewer_limitation_does_not_label_valid_auto_as_unsupported():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[TextureOverrideComponent01]\n"
            "drawindexed = 3, 0, -4\n"
            "drawindexed = auto\n"
            "drawindexed = AUTO\n"
            "drawindexed = $count, $start, 0\n"))
        report = analyze_mod(tmp)

    issues = [item for item in report["issues"]
              if item["code"] == "unsupported_drawindexed_arguments"]
    assert [item["line"] for item in issues] == [5]
    assert [item["arguments"] for item in issues] == ["$count, $start, 0"]
    assert issues[0]["category"] == "viewer"
    assert issues[0]["severity"] == "warning"
    assert "viewer" in issues[0]["message"].lower()




def test_health_survives_geometry_failure():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), "[Present]\nendif\n")
        result = mod_loader.load_mod(tmp)

    assert ("error" in result and "health" in result), ("geometry failure still returns the health report")
    assert ("malformed_condition_nesting" in _codes(result["health"])), ("failure-path health report retains INI findings")


def test_section_semantics_and_staged_findings(tmp_path):
    ini = tmp_path / "mod.ini"
    _write(ini, "[Present]\n")
    staged = (
        "namespace = Demo\ncondition = 1\nhash = 12345678\n"
        "[TextureOverrideComponent01]\nhash = 12345678\nps-t0 = ResourceA\n"
        "ps-t0 = ResourceB\n[textureoverridecomponent01]\nhash = abcdef12\n"
        "[TextureOverideTypo]\n[ResourceComponent01]\nfilename component01.buf\n"
        "stride = 4\nstride = 8\n[Include]\ninclude = a.ini\n"
        "include = b.ini\n[KeyMany]\nkey = no_ctrl no_shift no_alt ;\n"
        "key = ctrl F1\nback = F2\nback = F3\n"
        "[KeyEmpty]\n[CommandListActions]\nif $x == 1\n"
        "run = ResourceComponent01\nrun = CommandListMissing\nendif\n"
        "[ShaderOverrideBad]\nhash = xyz\n"
        "[TextureOverrideFuzzy]\nmatch_width = 128\n"
    )
    report = analyze_mod(str(tmp_path), overrides={str(ini): staged})
    by_code = {}
    for issue in report["issues"]:
        by_code.setdefault(issue["code"], []).append(issue)

    assert len(by_code["duplicate_section"]) == 1
    assert by_code["duplicate_section"][0]["first_line"] == 4
    assert len(by_code["unknown_section"]) == 1
    assert len(by_code["statement_outside_section"]) == 1
    assert len(by_code["malformed_regular_statement"]) == 1
    assert len(by_code["duplicate_section_key"]) == 1
    assert by_code["duplicate_section_key"][0]["key"] == "stride"
    assert len(by_code["missing_key_binding"]) == 1
    assert "invalid_key_binding" not in by_code
    assert len(by_code["invalid_run_target"]) == 1
    assert [issue["target"] for issue in by_code["missing_local_run_target"]] == ["CommandListMissing"]
    assert len(by_code["invalid_hash"]) == 1
    assert not [issue for issue in by_code.get("missing_override_hash", [])
                if issue["section"] == "TextureOverrideFuzzy"]


def test_override_and_key_missing_or_invalid(tmp_path):
    _write(tmp_path / "mod.ini", (
        "[ShaderOverrideMissing]\nhandling = skip\n"
        "[TextureOverrideMissing]\nhandling = skip\n"
        "[KeyInvalid]\nkey = no_ctrl no_shift\nback = \n"
    ))
    report = analyze_mod(str(tmp_path))
    assert {issue["override_type"] for issue in report["issues"]
            if issue["code"] == "missing_override_hash"} == {"shader", "texture"}
    assert {issue["binding_type"] for issue in report["issues"]
            if issue["code"] == "invalid_key_binding"} == {"back"}


def test_variable_assignments_resolve_namespace_and_local_scope(tmp_path):
    _write(tmp_path / "globals.ini", (
        "namespace = A\n[Constants]\nglobal persist $Shared\n"
        "global $Uninitialized\n"
    ))
    _write(tmp_path / "actions.ini", (
        "namespace = A\n[CommandListFirst]\n$early = 0\n"
        "local $early\n$shared = 1\n$Uninitialized = 2\n"
        "local $temp\n$temp = 2\n$missing = 3\n"
        "$\\Framework\\external = 4\n"
        "if $shared\nlocal $nested\n$nested = 1\nendif\n"
        "$nested = 2\n[CommandListSecond]\n$temp = 5\n"
    ))
    _write(tmp_path / "other.ini", (
        "namespace = B\n[CommandListOther]\n$shared = 1\n"
        "global $fake\n$fake = 2\n"
    ))
    report = analyze_mod(str(tmp_path))
    assert [(issue["section"], issue["variable"]) for issue in report["issues"]
            if issue["code"] == "undeclared_variable"] == [
                ("CommandListFirst", "$early"),
                ("CommandListFirst", "$missing"),
                ("CommandListFirst", "$nested"),
                ("CommandListSecond", "$temp"),
                ("CommandListOther", "$shared"),
                ("CommandListOther", "$fake"),
            ]


def test_multi_ini_global_and_run_lookup_keeps_unnamespaced_siblings_isolated(tmp_path):
    _write(tmp_path / "global.ini", (
        "[Constants]\nglobal $fallback = 0\n"
        "[CommandListGlobal]\n[CustomShaderGlobal]\n"
    ))
    _write(tmp_path / "a.ini", (
        "namespace = Demo\n[Constants]\nglobal $shared = 0\n"
        "[CommandListScoped]\n[CustomShaderScoped]\n"
    ))
    _write(tmp_path / "b.ini", (
        "namespace = Demo\n[CommandListConsumer]\n"
        "$shared = 1\n$fallback = 2\n"
        "run = CommandListScoped\nrun = CommandListGlobal\n"
        "run = CustomShaderScoped\nrun = CustomShaderGlobal\n"
    ))
    _write(tmp_path / "isolated.ini", (
        "[CommandListIsolated]\n$fallback = 1\n"
        "run = CommandListGlobal\n"
    ))
    _write(tmp_path / "other.ini", (
        "namespace = Other\n[CommandListOther]\n"
        "$shared = 1\nrun = CommandListScoped\n"
    ))
    report = analyze_mod(str(tmp_path))
    assert [(issue["ini"], issue["variable"]) for issue in report["issues"]
            if issue["code"] == "undeclared_variable"] == [
                ("isolated.ini", "$fallback"),
                ("other.ini", "$shared"),
            ]
    assert [(issue["ini"], issue["target"]) for issue in report["issues"]
            if issue["code"] == "missing_local_run_target"] == [
                ("isolated.ini", "CommandListGlobal"),
                ("other.ini", "CommandListScoped"),
            ]


def test_duplicate_override_metadata_but_not_repeated_commands(tmp_path):
    _write(tmp_path / "mod.ini", (
        "[TextureOverrideComponent01]\nhash = abcdef12\nmatch_width = 10\n"
        "match_width = 20\nps-t0 = ResourceA\nps-t0 = ResourceB\n"
        "[ShaderOverrideComponent01]\nhash = 1\nfilter_index = 1\n"
        "filter_index = 2\n"
        "[CustomShaderComponent01]\nvs = component01.hlsl\nvs = other.hlsl\n"
    ))
    report = analyze_mod(str(tmp_path))
    duplicates = [issue for issue in report["issues"]
                  if issue["code"] == "duplicate_section_key"]
    assert [(issue["key"], issue["first_line"], issue["line"])
            for issue in duplicates] == [
                ("match_width", 3, 4),
                ("filter_index", 9, 10),
                ("vs", 12, 13),
            ]


def test_reviewed_hash_run_key_and_regular_statement_edges(tmp_path):
    _write(tmp_path / "mod.ini", (
        "[TextureOverrideShort]\nhash = a\n"
        "[ShaderOverrideShort]\nhash = 1\n"
        "[TextureOverrideTypo]\nmatch_widht = 100\n"
        "[TextureOverridePriority]\nmatch_priority = 1\n"
        "[TextureOverrideQuality]\nmatch_msaa_quality = 2\n"
        "[TextureOverrideConflict]\nhash = 123\nmatch_width = 10\n"
        "[ResourceRegular]\ndraw = something\n"
        "[KeyModifiers]\nkey = ctrl\nkey = no_ctrl\n"
        "key = no_ctrl no_shift\n"
        "[CommandList Foo]\nrun = CommandList Foo\nrun =\n"
    ))
    report = analyze_mod(str(tmp_path))
    by_code = {}
    for issue in report["issues"]:
        by_code.setdefault(issue["code"], []).append(issue)
    assert "invalid_hash" not in by_code
    assert "invalid_key_binding" not in by_code
    assert "malformed_regular_statement" not in by_code
    assert [(issue["section"], issue["code"]) for issue in
            by_code["missing_override_hash"]] == [
                ("TextureOverrideTypo", "missing_override_hash"),
                ("TextureOverridePriority", "missing_override_hash"),
            ]
    assert len(by_code["hash_match_conflict"]) == 1
    assert [(issue["target"], issue["target_display"]) for issue in
            by_code["invalid_run_target"]] == [("", "Empty run target")]


def test_namespaced_duplicate_global_section_is_not_assumed_ignored(tmp_path):
    _write(tmp_path / "mod.ini", (
        "namespace = Demo\n[Present]\nrun = CommandListA\n"
        "[Present]\nrun = CommandListB\n"
        "[TextureOverrideA]\nhash = 1\n"
        "[textureoverridea]\nhash = 2\n"
    ))
    report = analyze_mod(str(tmp_path))
    assert [issue["section"] for issue in report["issues"]
            if issue["code"] == "duplicate_section"] == ["textureoverridea"]
