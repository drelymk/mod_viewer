"""Focused coverage for the conservative INI health report."""

import json
import os
import tempfile


from app import mod_loader
from core.ini_health import analyze_mod


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
            "[TextureOverrideBody]\n"
            "if $x == 1\n"
            "ib = ResourceBodyIB\n"
            "endif\n"
            "endif\n"
            "vb0 = ResourceNoSection\n"
            "[ResourceBodyIB]\n"
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
    assert ("missing_resource_section" in codes), ("direct local buffer binding without a declaration is reported")
    assert ("missing_resource_file" in codes), ("used resource with a missing file is an error")
    assert ("invalid_resource_stride" in codes), ("used resource with non-positive stride is an error")
    assert ("unused_resource_section" in codes), ("unreferenced resource declaration is reported")
    assert ("unreferenced_asset_file" in codes), ("asset not declared by an active INI is reported")




def test_reference_graph_case_exactness_and_comments():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, (
            "[TextureOverrideBody]\n"
            "ib = resourceRuntime\n"
            "; ib = ResourceCommented\n"
            "[ResourceRuntime]\n"
            "source = RESOURCEBridge\n"
            "[ResourceBridge]\n"
            "source = ResourceBodyIB\n"
            "[ResourceBody]\n"
            "filename = body.buf\n"
            "[ResourceBodyIB]\n"
            "filename = body_ib.buf\n"
            "[ResourceCommented]\n"
            "filename = commented.buf\n"))
        for name in ("body.buf", "body_ib.buf", "commented.buf"):
            _write(os.path.join(tmp, name), b"x", binary=True)
        report = analyze_mod(tmp)
        unused = {item["resource"] for item in report["issues"]
                  if item["code"] == "unused_resource_section"}

    assert ("ResourceRuntime" not in unused and "ResourceBridge" not in unused
          and "ResourceBodyIB" not in unused), ("case-insensitive transitive resource references are followed")
    assert ("ResourceBody" in unused), ("resource names are matched exactly, not by prefix")
    assert ("ResourceCommented" in unused), ("commented references do not make a resource used")


def test_implicit_rest_pose_and_unrooted_cycles():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[TextureOverrideBody]\n"
            "ib = ResourcePosition\n"
            "[ResourcePosition]\n"
            "[ResourcePosition.B]\n"
            "filename = pose.buf\n"
            "[ResourceCycleA]\n"
            "source = ResourceCycleB\n"
            "[ResourceCycleB]\n"
            "source = ResourceCycleA\n"
            "[ResourceSelf]\n"
            "source = ResourceSelf\n"))
        _write(os.path.join(tmp, "pose.buf"), b"x", binary=True)
        report = analyze_mod(tmp)
        unused = {item["resource"] for item in report["issues"]
                  if item["code"] == "unused_resource_section"}

    assert ("ResourcePosition" not in unused and "ResourcePosition.B" not in unused), ("implicit .B rest-pose resources follow the loader's convention")
    assert ({"ResourceCycleA", "ResourceCycleB", "ResourceSelf"} <= unused), ("self references and unrooted cycles do not make resources used")


def test_file_classification_and_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, "[TextureOverrideBody]\nib = ResourceIB\n[ResourceIB]\nfilename = active.buf\n")
        _write(os.path.join(tmp, "DISABLED-old.ini"),
               "[ResourceOld]\nfilename = inactive.dds\n")
        _write(os.path.join(tmp, "active.buf"), b"x", binary=True)
        _write(os.path.join(tmp, "inactive.dds"), b"x", binary=True)
        _write(os.path.join(tmp, "viewer.png"), b"x", binary=True)
        _write(os.path.join(tmp, "orphan.tga"), b"x", binary=True)
        with open(os.path.join(tmp, ".mod_viewer.json"), "w", encoding="utf-8") as fh:
            json.dump({"textures": {"Body::whole": {
                "tex_key": "viewer.png", "label": "viewer", "manual": True,
            }}}, fh)

        staged = (
            "[TextureOverrideBody]\nif $x == 1\nib = ResourceIB\n"
            "[ResourceIB]\nfilename = active.buf\n")
        report = analyze_mod(tmp, overrides={ini: staged})

    assert (report["files"] == {"unreferenced": 1, "inactive_only": 1,
                              "viewer_only": 1, "referenced": 1}), (f"active, inactive-only, viewer-only and unused assets classify separately ({report['files']})")
    assert ("malformed_condition_nesting" in _codes(report)), ("staged in-memory INI text is analyzed instead of stale disk text")


def test_unsafe_paths_and_namespaced_resources():
    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "mod.ini")
        _write(ini, (
            "[TextureOverrideBody]\n"
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




def test_health_survives_geometry_failure():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), "[Present]\nendif\n")
        result = mod_loader.load_mod(tmp)

    assert ("error" in result and "health" in result), ("geometry failure still returns the health report")
    assert ("malformed_condition_nesting" in _codes(result["health"])), ("failure-path health report retains INI findings")
