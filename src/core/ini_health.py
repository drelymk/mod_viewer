"""Conservative, read-only health checks for a 3DMigoto mod folder.

The analyzer deliberately reports only facts it can establish from the INI
text and filesystem.  In particular, namespaced resources may be supplied by
an XXMI framework, so they are never diagnosed as missing local declarations.
"""

import json
import os
import re

from .ini_document import IniDocument
from .mesh_builder import safe_resource_path


_RESOURCE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])Resource[A-Za-z0-9_.\\-]*(?![A-Za-z0-9_.])", re.I)
_LOCAL_RESOURCE_RE = re.compile(r"^Resource[A-Za-z0-9_.-]+$", re.I)
_REFERENCE_LHS_RE = re.compile(r"^(?:ib|vb\d+|ps-t\d+|cs-t\d+)$", re.I)
_ASSET_EXTENSIONS = {
    ".buf", ".ib", ".vb", ".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp",
}
_IGNORED_FILES = {".mod_viewer.json"}


def _issue(code, severity, category, message, ini=None, section=None,
           line=None, source=None, **extra):
    value = {
        "code": code,
        "severity": severity,
        "category": category,
        "message": message,
    }
    if ini is not None:
        value["ini"] = ini
    if section is not None:
        value["section"] = section
    if line is not None:
        value["line"] = line
    if source:
        value["source"] = source
    value.update(extra)
    return value


def _relative(path, root):
    return os.path.relpath(path, root).replace("\\", "/")


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _load_document(path, override=None):
    return (IniDocument.from_string(override, path=path)
            if override is not None else IniDocument.load(path))


def _resource_sections(doc):
    """Return case-insensitive logical resources, retaining duplicate blocks."""
    resources = {}
    for sec in doc.sections:
        if not sec.name.lower().startswith("resource"):
            continue
        entry = resources.setdefault(sec.name.lower(), {
            "name": sec.name, "sections": [], "filenames": [], "stride_lines": [],
        })
        entry["sections"].append(sec)
        for line in sec.lines:
            if line.kind != "assign" or "=" not in line.text:
                continue
            lhs, rhs = (part.strip() for part in line.text.split("=", 1))
            if lhs.lower() == "filename":
                entry["filenames"].append((rhs, line))
            elif lhs.lower() == "stride":
                entry["stride_lines"].append((rhs, line))
    return resources


def _tokens(lines):
    for line in lines:
        if line.kind in ("blank", "comment", "section"):
            continue
        yield line, [match.group(0) for match in _RESOURCE_TOKEN_RE.finditer(line.text)]


def _rebased_filename(filename, ini_path, mod_dir):
    rel_dir = os.path.relpath(os.path.dirname(ini_path), mod_dir)
    return filename if rel_dir == os.curdir else os.path.join(rel_dir, filename)


def _analyze_document(doc, ini_rel, ini_path, mod_dir, issues, declared_files):
    for problem in doc.structure_errors():
        issues.append(_issue(
            "malformed_condition_nesting", "error", "conditions",
            problem["problem"], ini_rel, problem.get("section"),
            problem["line"] + 1,
            doc.lines[problem["line"]].raw.strip() if doc.lines else None,
        ))
    for problem in doc.syntax_errors():
        category = ("conditions" if problem["code"] != "malformed_section_header"
                    else "ini")
        issues.append(_issue(
            problem["code"], "error", category, problem["problem"],
            ini_rel, problem.get("section"), problem["line"] + 1,
            doc.lines[problem["line"]].raw.strip() if doc.lines else None,
        ))

    resources = _resource_sections(doc)
    declared = set(resources)
    roots, edges = set(), {name: set() for name in declared}

    for sec in doc.sections:
        owner = sec.name.lower() if sec.name.lower() in declared else None
        for line, names in _tokens(sec.lines):
            for token in names:
                target = token.lower()
                if target in declared:
                    if owner:
                        if owner != target:
                            edges[owner].add(target)
                    else:
                        roots.add(target)

            # Missing local declarations are only high-confidence on direct
            # buffer/texture bindings. Backslash-namespaced resources are
            # framework-provided and intentionally excluded.
            if line.kind == "assign" and "=" in line.text:
                lhs, rhs = (part.strip() for part in line.text.split("=", 1))
                if _REFERENCE_LHS_RE.match(lhs):
                    rhs = re.sub(r"^(?:copy|ref)\s+", "", rhs, flags=re.I).split()[0] if rhs else ""
                    if (_LOCAL_RESOURCE_RE.match(rhs) and rhs.lower() not in declared):
                        issues.append(_issue(
                            "missing_resource_section", "warning", "resources",
                            f"{rhs} is referenced but has no resource section in this INI.",
                            ini_rel, sec.name, line.no + 1, line.raw.strip(),
                            resource=rhs,
                        ))

    # Match build_draw_groups' established implicit rest-pose convention: a
    # computed ResourceX with no file may resolve through ResourceX.B even
    # though the INI contains no textual copy edge.
    for name, resource in resources.items():
        rest_pose = name + ".b"
        if not resource["filenames"] and rest_pose in declared:
            edges[name].add(rest_pose)

    reachable, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(edges.get(name, ()))

    for key, resource in resources.items():
        for raw_stride, line in resource["stride_lines"]:
            try:
                valid = int(raw_stride) > 0
            except ValueError:
                valid = False
            if not valid and key in reachable:
                issues.append(_issue(
                    "invalid_resource_stride", "error", "resources",
                    f"{resource['name']} has an invalid stride: {raw_stride!r}.",
                    ini_rel, resource["name"], line.no + 1, line.raw.strip(),
                    resource=resource["name"],
                ))

        owned = []
        for filename, line in resource["filenames"]:
            resolved = safe_resource_path(
                mod_dir, _rebased_filename(filename, ini_path, mod_dir))
            if resolved is None:
                if key in reachable:
                    issues.append(_issue(
                        "unsafe_resource_path", "error", "resources",
                        f"{resource['name']} uses a filename outside the allowed resource path: {filename}.",
                        ini_rel, resource["name"], line.no + 1, line.raw.strip(),
                        resource=resource["name"], filename=filename,
                    ))
                continue
            declared_files.add(_path_key(resolved))
            owned.append(filename.replace("\\", "/"))
            if key in reachable and not os.path.isfile(resolved):
                issues.append(_issue(
                    "missing_resource_file", "error", "resources",
                    f"{resource['name']} references a file that does not exist: {filename}.",
                    ini_rel, resource["name"], line.no + 1, line.raw.strip(),
                    resource=resource["name"], filename=filename,
                ))

        if key not in reachable:
            first = resource["sections"][0]
            issues.append(_issue(
                "unused_resource_section", "warning", "resources",
                f"{resource['name']} is not referenced in this INI.",
                ini_rel, resource["name"], first.header_no + 1,
                doc.lines[first.header_no].raw.strip(), resource=resource["name"],
                files=owned,
            ))


def _filename_paths(doc, mod_dir, ini_path=None):
    result = set()
    for sec in doc.sections:
        for line in sec.lines:
            if line.kind != "assign" or "=" not in line.text:
                continue
            lhs, rhs = (part.strip() for part in line.text.split("=", 1))
            if lhs.lower() != "filename":
                continue
            relative = (_rebased_filename(rhs, ini_path, mod_dir)
                        if ini_path else rhs)
            resolved = safe_resource_path(mod_dir, relative)
            if resolved is not None:
                result.add(_path_key(resolved))
    return result


def _viewer_texture_paths(mod_dir):
    result = set()
    try:
        with open(os.path.join(mod_dir, ".mod_viewer.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        textures = data.get("textures", {}) if isinstance(data, dict) else {}
        for state in textures.values() if isinstance(textures, dict) else ():
            key = state.get("tex_key") if isinstance(state, dict) else None
            resolved = safe_resource_path(mod_dir, key)
            if resolved is not None:
                result.add(_path_key(resolved))
    except (OSError, ValueError, TypeError):
        pass
    return result


def _inventory_files(mod_dir):
    for base, dirs, files in os.walk(mod_dir):
        dirs.sort()
        files.sort()
        for name in files:
            if name.lower() in _IGNORED_FILES or name.lower().endswith(".bak"):
                continue
            if os.path.splitext(name)[1].lower() in _ASSET_EXTENSIONS:
                yield os.path.join(base, name)


def analyze_mod(mod_dir, ini_paths=None, overrides=None):
    """Return a JSON-ready health report for active INIs in ``mod_dir``.

    Any staged text in ``overrides`` is analyzed instead of the disk version.
    A bad INI becomes a report issue and never prevents the remaining files
    from being checked.
    """
    overrides = overrides or {}
    if ini_paths is None:
        ini_paths = [os.path.join(mod_dir, name) for name in sorted(os.listdir(mod_dir))
                     if name.lower().endswith(".ini")
                     and not name.upper().startswith("DISABLED")]

    issues, declared_files = [], set()
    for path in ini_paths:
        ini_rel = _relative(path, mod_dir)
        try:
            doc = _load_document(path, overrides.get(path))
        except (OSError, UnicodeError) as exc:
            issues.append(_issue(
                "unreadable_ini", "error", "ini",
                f"Could not read this INI as UTF-8: {exc}", ini=ini_rel,
            ))
            continue
        declared_files.update(_filename_paths(doc, mod_dir, path))
        _analyze_document(doc, ini_rel, path, mod_dir, issues, declared_files)

    inactive_files = set()
    for name in sorted(os.listdir(mod_dir)):
        if not (name.lower().endswith(".ini") and name.upper().startswith("DISABLED")):
            continue
        try:
            inactive_files.update(_filename_paths(
                IniDocument.load(os.path.join(mod_dir, name)), mod_dir))
        except (OSError, UnicodeError):
            pass

    viewer_files = _viewer_texture_paths(mod_dir)
    file_counts = {"unreferenced": 0, "inactive_only": 0, "viewer_only": 0, "referenced": 0}
    for path in _inventory_files(mod_dir):
        key = _path_key(path)
        if key in declared_files:
            file_counts["referenced"] += 1
        elif key in viewer_files:
            file_counts["viewer_only"] += 1
        elif key in inactive_files:
            file_counts["inactive_only"] += 1
        else:
            file_counts["unreferenced"] += 1
            rel = _relative(path, mod_dir)
            issues.append(_issue(
                "unreferenced_asset_file", "warning", "files",
                f"{rel} is not declared by any active INI.", filename=rel,
            ))

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (
        order.get(item["severity"], 9), item.get("ini", ""),
        item.get("line", 0), item["code"], item.get("filename", "")))
    summary = {
        "errors": sum(item["severity"] == "error" for item in issues),
        "warnings": sum(item["severity"] == "warning" for item in issues),
        "issues": len(issues),
        "unused_files": file_counts["unreferenced"],
        "unused_resources": sum(item["code"] == "unused_resource_section" for item in issues),
    }
    return {"summary": summary, "files": file_counts, "issues": issues}
