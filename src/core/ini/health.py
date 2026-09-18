"""Conservative, read-only health checks for a 3DMigoto mod folder.

The analyzer deliberately reports only facts it can establish from the INI
text and filesystem.  In particular, namespaced resources may be supplied by
an XXMI framework, so they are never diagnosed as missing local declarations.
"""

import json
import os
import re

from .document import IniDocument, OTHER
from . import migoto_semantics as semantics
from .sections import extract_ini_namespace
from ..mod_discovery import discover_ini_paths
from ..mod_source import ModSourceError, mod_source_for_path
from ..resource_paths import safe_resource_path
from ..textures import split_texture_key


_RESOURCE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.])Resource[A-Za-z0-9_.\\-]*(?![A-Za-z0-9_.])", re.I)
_LOCAL_RESOURCE_RE = re.compile(r"^Resource[A-Za-z0-9_.-]+$", re.I)
_REFERENCE_LHS_RE = re.compile(r"^(?:ib|vb\d+|ps-t\d+|cs-t\d+)$", re.I)
_RESOURCE_REFERENCE_RE = re.compile(
    r"^(?P<prefix>\S+)\s+(?P<resource>Resource[A-Za-z0-9_.\\-]+)\s*$", re.I)
# Viewer reconstruction handles literal triples; this is not 3DMigoto's
# operand grammar and must only drive viewer-compatibility findings.
_VIEWER_LITERAL_DRAWINDEXED_RE = re.compile(
    r"^\d+\s*,\s*\d+\s*,\s*-?\d+$")
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


def _relative(path, root, source=None):
    if source is None:
        source = getattr(path, "source", None)
    if source is not None and source.is_resource_reference(path):
        return source.logical_path(path)
    return os.path.relpath(path, root).replace("\\", "/")


def _path_key(path):
    return os.path.normcase(os.path.abspath(path))


def _load_document(path, override=None, document=None, source=None):
    if document is not None:
        return document
    if override is not None:
        return IniDocument.from_string(override, path=path)
    if source is not None:
        return IniDocument.from_string(source.read_text(path), path=path)
    return IniDocument.load(path)


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


def _rebased_filename(filename, ini_path, mod_dir, source=None):
    if source is None:
        source = getattr(ini_path, "source", None)
    ini_name = (source.logical_path(ini_path)
                if source is not None else ini_path)
    rel_dir = os.path.dirname(ini_name) if source is not None \
        else os.path.relpath(os.path.dirname(ini_path), mod_dir)
    return filename if rel_dir == os.curdir else os.path.join(rel_dir, filename)


def _normalized_key_chord(value):
    """Normalize enough key syntax to find chords that can fire together."""
    modifiers, keys = [], []
    for token in value.lower().split():
        normalized = token.replace("-", "_")
        if normalized in {"no_modifiers", "no_ctrl", "no_control",
                          "no_shift", "no_alt"}:
            continue
        if normalized in {"ctrl", "control", "shift", "alt"}:
            modifiers.append("ctrl" if normalized in {"ctrl", "control"}
                             else normalized)
        else:
            keys.append(normalized)
    if not keys:
        return None
    return "+".join(sorted(set(modifiers)) + keys)


def _analyze_statements(doc, ini_rel, issues, global_variables,
                        run_targets, ini_namespace=None):
    """Check conservative statement-level mistakes outside condition syntax."""
    seen_keys = {}

    for line in doc.lines:
        if line.section is not None or line.kind in ("blank", "comment", "section"):
            continue
        lhs = line.text.split("=", 1)[0].strip().casefold()
        if line.kind == "assign" and lhs in {"namespace", "condition"}:
            continue
        issues.append(_issue(
            "statement_outside_section", "error", "ini",
            "Statement outside a section.", ini_rel,
            line=line.no + 1, source=line.raw.strip(),
        ))

    seen_sections = {}

    for section in doc.sections:
        name = section.name.casefold()
        is_key = name.startswith("key")
        previous_section = seen_sections.get(name)
        if previous_section is not None and not (
                ini_namespace and semantics.is_global_exact_section(section.name)):
            issues.append(_issue(
                "duplicate_section", "warning", "ini",
                f"Duplicate section [{section.name}]. 3DMigoto uses only the first section with this name.",
                ini_rel, section.name, section.header_no + 1,
                doc.lines[section.header_no].raw.strip(),
                first_line=previous_section.header_no + 1,
            ))
        else:
            seen_sections[name] = section
        kind = semantics.section_kind(section.name)
        if kind is None:
            issues.append(_issue(
                "unknown_section", "warning", "ini",
                f"[{section.name}] is not a recognized 3DMigoto section.",
                ini_rel, section.name, section.header_no + 1,
                doc.lines[section.header_no].raw.strip(),
            ))
        seen_lhs = {}
        bindings = []
        hash_lines = []
        texture_match = False
        local_variables = set()
        for line in section.lines:
            declaration = semantics.declaration(line.text)
            if (declaration and declaration[0] == "local" and line.depth == 0
                    and kind == "command"):
                local_variables.add(declaration[1])
            if is_key and line.kind == OTHER:
                issues.append(_issue(
                    "unexpected_key_statement", "error", "ini",
                    f"Unexpected statement in [{section.name}]: {line.text}",
                    ini_rel, section.name, line.no + 1, line.raw.strip(),
                ))
            elif (kind == "regular" and not semantics.allows_bare_statements(section.name)
                  and line.kind not in ("blank", "comment", "section")
                  and "=" not in line.text
                  and not (is_key and line.kind == OTHER)):
                issues.append(_issue(
                    "malformed_regular_statement", "error", "ini",
                    f"Statement in [{section.name}] needs a key=value pair.",
                    ini_rel, section.name, line.no + 1, line.raw.strip(),
                ))

            if "=" in line.text:
                draw_lhs, draw_rhs = (
                    part.strip() for part in line.text.split("=", 1))
                if (draw_lhs.lower() == "drawindexed"
                        and draw_rhs.casefold() != "auto"
                        and not _VIEWER_LITERAL_DRAWINDEXED_RE.fullmatch(draw_rhs)):
                    issues.append(_issue(
                        "unsupported_drawindexed_arguments", "warning", "viewer",
                        "The viewer cannot currently reconstruct this drawindexed "
                        "form as an authored draw; 3DMigoto may accept it.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        arguments=draw_rhs,
                    ))

            if line.kind != "assign" or "=" not in line.text:
                continue
            lhs, rhs = (part.strip() for part in line.text.split("=", 1))
            lowered_lhs = lhs.lower()

            variable = semantics.plain_variable_assignment(lhs)
            if (variable and line.depth == 0 and variable not in global_variables
                    and variable not in local_variables):
                issues.append(_issue(
                    "undeclared_variable", "error",
                    "controls" if name.startswith(("key", "preset")) else "ini",
                    f"{lhs} is assigned without a declaration.",
                    ini_rel, section.name, line.no + 1, line.raw.strip(),
                    variable=lhs,
                ))

            if ((kind == "regular" and not semantics.allows_duplicate_key(section.name, lhs))
                    or semantics.unique_command_metadata(section.name, lhs)):
                previous = seen_lhs.get(lowered_lhs)
                if previous is not None:
                    issues.append(_issue(
                        "duplicate_section_key", "warning", "ini",
                        f"[{section.name}] repeats {lhs}; 3DMigoto uses the first value.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        key=lhs, first_line=previous,
                    ))
                else:
                    seen_lhs[lowered_lhs] = line.no + 1
            if lowered_lhs == "hash":
                hash_lines.append((rhs, line))
            if semantics.is_texture_override_match_key(lhs):
                texture_match = line
            if is_key and lowered_lhs in {"key", "back"}:
                bindings.append((lowered_lhs, rhs, line))
                if semantics.key_binding_kind(rhs) == "invalid":
                    issues.append(_issue(
                        "invalid_key_binding", "error", "controls",
                        f"[{section.name}] has an empty {lhs} binding.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        binding=rhs, binding_type=lowered_lhs,
                    ))

            if _REFERENCE_LHS_RE.match(lhs):
                malformed = _RESOURCE_REFERENCE_RE.match(rhs)
                if malformed and malformed.group("prefix").lower() not in {
                        "copy", "ref"}:
                    issues.append(_issue(
                        "malformed_resource_reference", "error", "resources",
                        f"{lhs} uses an invalid resource reference prefix: "
                        f"{malformed.group('prefix')!r}.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        resource=malformed.group("resource"),
                        lhs=lhs, prefix=malformed.group("prefix"),
                    ))

            if lowered_lhs == "run":
                target = rhs.split(";", 1)[0].strip()
                target_kind = semantics.classify_run_target(target)
                if target_kind == "invalid":
                    issues.append(_issue(
                        "invalid_run_target", "error", "ini",
                        f"{target or 'Empty run target'} is not a command-list target.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        target=target, target_display=target or "Empty run target",
                    ))
                elif target_kind == "local" and target.casefold() not in run_targets:
                    issues.append(_issue(
                        "missing_local_run_target", "warning", "ini",
                        f"{target} is run but is not declared in this INI; "
                        "it may be supplied by the framework.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        target=target,
                    ))

            if is_key and lowered_lhs == "key":
                chord = _normalized_key_chord(rhs)
                if not chord:
                    continue
                previous = seen_keys.get(chord)
                if previous:
                    issues.append(_issue(
                        "duplicate_key_binding", "warning", "controls",
                        f"[{section.name}] and [{previous['section']}] share "
                        f"the key binding {rhs!r} and may activate together.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        key=rhs, other_section=previous["section"],
                        other_line=previous["line"],
                    ))
                else:
                    seen_keys[chord] = {
                        "section": section.name, "line": line.no + 1,
                    }

        if is_key and not bindings:
            header = doc.lines[section.header_no]
            issues.append(_issue(
                "missing_key_binding", "warning", "controls",
                f"[{section.name}] has no key= or back= binding.",
                ini_rel, section.name, header.no + 1, header.raw.strip(),
            ))
        override_type = semantics.override_hash_kind(section.name)
        if override_type:
            if override_type == "texture" and hash_lines and texture_match:
                issues.append(_issue(
                    "hash_match_conflict", "warning", "ini",
                    f"[{section.name}] cannot combine hash= with resource match options.",
                    ini_rel, section.name, texture_match.no + 1,
                    texture_match.raw.strip(),
                ))
            for value, line in hash_lines:
                if not semantics.valid_override_hash(value, override_type):
                    issues.append(_issue(
                        "invalid_hash", "error", "ini",
                        f"[{section.name}] has an invalid {override_type} hash.",
                        ini_rel, section.name, line.no + 1, line.raw.strip(),
                        value=value, override_type=override_type,
                    ))
            if not hash_lines and not (override_type == "texture" and texture_match):
                header = doc.lines[section.header_no]
                issues.append(_issue(
                    "missing_override_hash", "error", "ini",
                    f"[{section.name}] has no hash or resource match option.",
                    ini_rel, section.name, header.no + 1, header.raw.strip(),
                    override_type=override_type,
                ))


def _analyze_document(doc, ini_rel, ini_path, mod_dir, issues, declared_files,
                      source=None, global_variables=frozenset(),
                      run_targets=frozenset(), ini_namespace=None):
    for problem in doc.structure_errors():
        issues.append(_issue(
            "malformed_condition_nesting", "error", "conditions",
            problem["problem"], ini_rel, problem.get("section"),
            problem["line"] + 1,
            doc.lines[problem["line"]].raw.strip() if doc.lines else None,
            reason=problem.get("reason"),
            **({"count": problem["count"]} if "count" in problem else {}),
        ))
    for problem in doc.syntax_errors():
        category = ("conditions" if problem["code"] != "malformed_section_header"
                    else "ini")
        issues.append(_issue(
            problem["code"], "error", category, problem["problem"],
            ini_rel, problem.get("section"), problem["line"] + 1,
            doc.lines[problem["line"]].raw.strip() if doc.lines else None,
            reason=problem.get("reason"),
            **({"count": problem["count"]} if "count" in problem else {}),
        ))
    _analyze_statements(
        doc, ini_rel, issues, global_variables, run_targets, ini_namespace)

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
                    resource=resource["name"], stride=raw_stride,
                ))

        owned = []
        for filename, line in resource["filenames"]:
            resolved = safe_resource_path(
                mod_dir, _rebased_filename(
                    filename, ini_path, mod_dir, source=source)) \
                if source is None else source.resolve_resource(
                    _rebased_filename(filename, ini_path, mod_dir, source=source))
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
            exists = source.is_file if source is not None else os.path.isfile
            if key in reachable and not exists(resolved):
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


def _filename_paths(doc, mod_dir, ini_path=None, source=None):
    result = set()
    for sec in doc.sections:
        for line in sec.lines:
            if line.kind != "assign" or "=" not in line.text:
                continue
            lhs, rhs = (part.strip() for part in line.text.split("=", 1))
            if lhs.lower() != "filename":
                continue
            relative = (_rebased_filename(rhs, ini_path, mod_dir, source=source)
                        if ini_path else rhs)
            resolved = (source.resolve_resource(relative)
                        if source is not None
                        else safe_resource_path(mod_dir, relative))
            if resolved is not None:
                result.add(_path_key(resolved))
    return result


def _viewer_texture_paths(mod_dir, source=None):
    result = set()
    try:
        if source is not None:
            path = source.resolve_resource(".mod_viewer.json")
            if not path or not source.is_file(path):
                return result
            data = json.loads(source.read_text(path))
        else:
            with open(os.path.join(mod_dir, ".mod_viewer.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        textures = data.get("textures", {}) if isinstance(data, dict) else {}
        for state in textures.values() if isinstance(textures, dict) else ():
            if not isinstance(state, dict):
                continue
            for field in ("tex_key", "normal_map", "normal_data",
                          "light_map", "material_map", "emission_map"):
                key = state.get(field)
                _role, relative_path = split_texture_key(key)
                resolved = (source.resolve_resource(relative_path)
                            if source is not None
                            else safe_resource_path(mod_dir, relative_path))
                if resolved is not None:
                    result.add(_path_key(resolved))
    except (OSError, ValueError, TypeError, UnicodeError):
        pass
    return result


def _inventory_files(mod_dir, source=None):
    if source is not None:
        for relative in source.list_files():
            name = relative.rsplit("/", 1)[-1]
            if name.lower() in _IGNORED_FILES or name.lower().endswith(".bak"):
                continue
            if os.path.splitext(name)[1].lower() in _ASSET_EXTENSIONS:
                yield source.resolve_resource(relative)
        return
    for base, dirs, files in os.walk(mod_dir):
        dirs.sort()
        files.sort()
        for name in files:
            if name.lower() in _IGNORED_FILES or name.lower().endswith(".bak"):
                continue
            if os.path.splitext(name)[1].lower() in _ASSET_EXTENSIONS:
                yield os.path.join(base, name)


def analyze_mod(mod_dir, ini_paths=None, overrides=None, documents=None,
                source=None):
    """Return a JSON-ready health report for active INIs in ``mod_dir``.

    Any staged text in ``overrides`` is analyzed instead of the disk version.
    A bad INI becomes a report issue and never prevents the remaining files
    from being checked.
    """
    overrides = overrides or {}
    documents = documents or {}
    if source is None:
        source = mod_source_for_path(mod_dir)
    if ini_paths is None:
        ini_paths = discover_ini_paths(mod_dir, source=source)

    issues, declared_files = [], set()
    loaded = []
    for path in ini_paths:
        ini_rel = _relative(path, mod_dir, source=source)
        try:
            doc = documents.get(path)
            if doc is None:
                doc = documents.get(_path_key(path))
            doc = _load_document(
                path, overrides.get(path), document=doc, source=source)
        except (OSError, UnicodeError, ModSourceError) as exc:
            issues.append(_issue(
                "unreadable_ini", "error", "ini",
                f"Could not read this INI as UTF-8: {exc}", ini=ini_rel,
                detail=str(exc),
            ))
            continue
        loaded.append((path, ini_rel, doc))

    scoped = []
    namespace_globals, namespace_runs = {}, {}
    unqualified_globals, unqualified_runs = set(), set()
    for path, ini_rel, doc in loaded:
        namespace = (extract_ini_namespace(document=doc) or "").casefold()
        doc_globals, doc_runs = set(), set()
        for section in doc.sections:
            name = section.name.casefold()
            if name.startswith(("commandlist", "customshader")):
                doc_runs.add(name)
            if name == "constants":
                for line in section.lines:
                    declaration = semantics.declaration(line.text)
                    if declaration and declaration[0] == "global":
                        doc_globals.add(declaration[1])
        scoped.append((path, ini_rel, doc, namespace, doc_globals, doc_runs))
        if namespace:
            namespace_globals.setdefault(namespace, set()).update(doc_globals)
            namespace_runs.setdefault(namespace, set()).update(doc_runs)
        else:
            unqualified_globals.update(doc_globals)
            unqualified_runs.update(doc_runs)

    for path, ini_rel, doc, namespace, doc_globals, doc_runs in scoped:
        if namespace:
            visible_globals = namespace_globals[namespace] | unqualified_globals
            visible_runs = namespace_runs[namespace] | unqualified_runs
        else:
            # Mod analysis deliberately isolates unnamespaced sibling INIs.
            visible_globals, visible_runs = doc_globals, doc_runs
        declared_files.update(_filename_paths(
            doc, mod_dir, path, source=source))
        _analyze_document(
            doc, ini_rel, path, mod_dir, issues, declared_files, source=source,
            global_variables=visible_globals, run_targets=visible_runs,
            ini_namespace=namespace)

    inactive_files = set()
    for path in discover_ini_paths(mod_dir, disabled=True, source=source):
        try:
            inactive_files.update(_filename_paths(
                _load_document(path, source=source), mod_dir, path,
                source=source))
        except (OSError, UnicodeError, ModSourceError):
            pass

    viewer_files = _viewer_texture_paths(mod_dir, source=source)
    file_counts = {"unreferenced": 0, "inactive_only": 0, "viewer_only": 0, "referenced": 0}
    for path in _inventory_files(mod_dir, source=source):
        key = _path_key(path)
        if key in declared_files:
            file_counts["referenced"] += 1
        elif key in viewer_files:
            file_counts["viewer_only"] += 1
        elif key in inactive_files:
            file_counts["inactive_only"] += 1
        else:
            file_counts["unreferenced"] += 1
            rel = _relative(path, mod_dir, source=source)
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
