"""Focused coverage for the conservative INI health report."""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import mod_loader
from core.ini_health import analyze_mod


FAILS = []


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


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

    check("malformed_condition_nesting" in codes,
          "unmatched endif is reported")
    nesting = next(item for item in report["issues"]
                   if item["code"] == "malformed_condition_nesting")
    check(nesting["severity"] == "error",
          "malformed condition nesting has error severity")
    check(nesting["line"] == 5 and nesting["source"] == "endif",
          "condition error has a 1-based line and source excerpt")
    check("missing_resource_section" in codes,
          "direct local buffer binding without a declaration is reported")
    check("missing_resource_file" in codes,
          "used resource with a missing file is an error")
    check("invalid_resource_stride" in codes,
          "used resource with non-positive stride is an error")
    check("unused_resource_section" in codes,
          "unreferenced resource declaration is reported")
    check("unreferenced_asset_file" in codes,
          "asset not declared by an active INI is reported")


def test_condition_syntax_and_header_errors():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), (
            "[Body]\n"
            "if ($x == 1\n"
            "else\n"
            "else\n"
            "elif $x == 2\n"
            "endif extra\n"
            "endif\n"
            "[]\n"
            "[Missing\n"
            "[Trailing] junk\n"))
        report = analyze_mod(tmp)

    by_code = {}
    for issue in report["issues"]:
        by_code.setdefault(issue["code"], []).append(issue)
    check(len(by_code.get("malformed_condition_nesting", [])) == 2,
          "duplicate else and elif-after-else are health errors")
    check(len(by_code.get("unbalanced_condition_parentheses", [])) == 1,
          "unbalanced conditional parentheses are a health error")
    check(len(by_code.get("malformed_condition_syntax", [])) == 1,
          "trailing endif content is a health error")
    check(len(by_code.get("malformed_section_header", [])) == 3,
          "empty, unclosed and trailing-content headers are health errors")
    check(all(issue["severity"] == "error" for issue in report["issues"]),
          "new INI syntax findings all use error severity")


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

    check("ResourceRuntime" not in unused and "ResourceBridge" not in unused
          and "ResourceBodyIB" not in unused,
          "case-insensitive transitive resource references are followed")
    check("ResourceBody" in unused,
          "resource names are matched exactly, not by prefix")
    check("ResourceCommented" in unused,
          "commented references do not make a resource used")


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

    check("ResourcePosition" not in unused and "ResourcePosition.B" not in unused,
          "implicit .B rest-pose resources follow the loader's convention")
    check({"ResourceCycleA", "ResourceCycleB", "ResourceSelf"} <= unused,
          "self references and unrooted cycles do not make resources used")


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

    check(report["files"] == {"unreferenced": 1, "inactive_only": 1,
                              "viewer_only": 1, "referenced": 1},
          f"active, inactive-only, viewer-only and unused assets classify separately ({report['files']})")
    check("malformed_condition_nesting" in _codes(report),
          "staged in-memory INI text is analyzed instead of stale disk text")


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

    check("unsafe_resource_path" in codes,
          "resource path beyond the loader's allowed escape depth is an error")
    namespaced_missing = [item for item in report["issues"]
                          if item["code"] == "missing_resource_section"
                          and "Framework" in item["message"]]
    check(not namespaced_missing,
          "namespaced framework resources are not guessed to be missing")


def test_encoding_line_endings_and_allowed_parent_path():
    with tempfile.TemporaryDirectory() as tmp:
        mod = os.path.join(tmp, "mod")
        os.mkdir(mod)
        ini = os.path.join(mod, "mod.ini")
        _write(ini, ("\ufeff[TextureOverrideBody]\r\n"
                     "if $x == 1\r\n"
                     "ib = ResourceShared\r\n"
                     "[ResourceShared]\r\n"
                     "filename = ..\\shared.buf\r\n"))
        _write(os.path.join(tmp, "shared.buf"), b"x", binary=True)
        _write(os.path.join(mod, "broken.ini"), b"\xff\xfe", binary=True)
        report = analyze_mod(mod)

    check("unreadable_ini" in _codes(report),
          "non-UTF-8 INI becomes a report error without aborting the scan")
    nesting = next(item for item in report["issues"]
                   if item["code"] == "malformed_condition_nesting")
    check(nesting["line"] == 2 and nesting["source"] == "if $x == 1",
          "UTF-8 BOM and CRLF input retain correct 1-based locations")
    check("unsafe_resource_path" not in _codes(report)
          and "missing_resource_file" not in _codes(report),
          "one-level parent resource path follows the loader's allowed escape rule")


def test_health_survives_geometry_failure():
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "mod.ini"), "[Present]\nendif\n")
        result = mod_loader.load_mod(tmp)

    check("error" in result and "__health__" in result,
          "geometry failure still returns the health report")
    check("malformed_condition_nesting" in _codes(result["__health__"]),
          "failure-path health report retains INI findings")


def main():
    tests = (
        test_structure_resource_and_file_findings,
        test_condition_syntax_and_header_errors,
        test_reference_graph_case_exactness_and_comments,
        test_implicit_rest_pose_and_unrooted_cycles,
        test_file_classification_and_overrides,
        test_unsafe_paths_and_namespaced_resources,
        test_encoding_line_endings_and_allowed_parent_path,
        test_health_survives_geometry_failure,
    )
    for test in tests:
        test()
    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
