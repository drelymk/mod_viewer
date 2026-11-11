"""Plan a lossless, session-only consolidation of static GIMI variant buffers."""

import os
import re
import struct
from dataclasses import dataclass

from core.geometry.buffers import BufferStore, _detect_uv_best
from core.geometry.draw_call import _freeze
from core.geometry.import_parts import consolidate_index_ranges
from core.geometry.vertex_attributes import VertexAttributeSource, decode_normals
from core.geometry.import_vertices import ImportVertices
from core.ini.command_phases import parse_command_line
from core.ini.document import ELIF, ELSE, ENDIF, IF, IniDocument
from core.ini.draw_scan import (
    _buffer_resource_at_draw, _reachable_execution_sections, _scan_sections_for_draws,
)
from core.ini.draw_resources import _collect_resource_copy_sources, _resolve_component_buffers
from core.ini.condition import And, Cmp, Operand, Paren, parse
from core.ini.migoto_semantics import unique_command_metadata
from core.ini.sections import extract_resources
from core.ini.texture_roles import _condition_group_is_consistent
from core.resource_paths import safe_resource_path


class ModImportError(ValueError):
    """The selected mod cannot be consolidated without guessing its behavior."""


@dataclass
class ImportPlan:
    documents: dict
    buffers: dict
    summary: dict


def _add_buffer(output, path, data):
    _require(path not in output, "Distinct imported streams share one physical file.")
    output[path] = data


def _require(condition, detail):
    if not condition:
        raise ModImportError(detail)


def _resource(resources, name):
    info = resources.get_ci(name) if name else None
    _require(info is not None and info.get("filename"),
             "Import requires literal, file-backed geometry resources.")
    return info


def _path(record, info):
    path = safe_resource_path(os.path.dirname(record.path), info["filename"])
    _require(path is not None, "A geometry resource is outside the resource sandbox.")
    return path


def _rewrite_property(doc, section_name, key, value):
    section = doc.section(section_name)
    matches = [line for line in section.lines
               if line.text.partition("=")[0].strip().casefold() == key]
    _require(len(matches) == 1, "Import requires unambiguous resource declarations.")
    line = matches[0]
    match = re.fullmatch(r"(\s*[^=]+=\s*)([^;]*?)(\s*(?:;.*)?)", line.raw)
    _require(match is not None, "A resource declaration could not be rewritten.")
    line.raw = match[1] + value + match[3]


def _selector_values(expression):
    """Recognize conjunctions through the shared, lossless condition syntax."""
    def visit(node):
        if isinstance(node, Paren):
            return visit(node.inner)
        if isinstance(node, And):
            return [item for part in node.parts for item in visit(part)]
        _require(isinstance(node, Cmp) and node.op == "=="
                 and isinstance(node.left, Operand) and node.left.is_var
                 and isinstance(node.right, Operand)
                 and re.fullmatch(r"-?\d+", node.right.text),
                 "Import requires literal equality outfit selectors.")
        return [(node.left.var.casefold(), int(node.right.text))]

    values = visit(parse(expression))
    _require(len(dict(values)) == len(values), "An outfit selector repeats a variable.")
    return dict(values)


def _normalize_selectors(doc, predicates):
    """Open proven disjoint equality arms without expanding their negations."""
    values = [_selector_values(expression) for expression in predicates]
    _require(all(any(variable in right and right[variable] != value
                     for variable, value in left.items())
                 for index, left in enumerate(values) for right in values[index + 1:]),
             "Outfit selectors overlap; import cannot preserve their branch priority.")
    def compact(text):
        return re.sub(r"\s+", "", text).casefold()

    expected = [compact(text) for text in predicates]
    patches = []
    for section in doc.sections:
        for first in section.lines:
            if first.kind != IF or compact(re.sub(r"^if\s+", "", first.text, flags=re.I)) != expected[0]:
                continue
            branches = []
            safe_to_open = True
            for line in section.lines:
                if line.no <= first.no:
                    continue
                if line.kind == ENDIF and line.depth == first.depth:
                    break
                text = line.text.casefold()
                if not text:
                    continue
                if line.depth == first.depth and line.kind == ELIF:
                    branches.append(line)
                else:
                    command = parse_command_line(line.raw)
                    key, separator, _value = command.text.casefold().partition("=")
                    if not (line.depth == first.depth + 1 and separator
                            and command.phase != "post"
                            and (key.strip() in {"vb0", "vb1", "ib", "handling", "draw", "drawindexed"}
                                 or re.fullmatch(r"ps-t\d+", key.strip()))):
                        safe_to_open = False
                    _require(not key.strip().startswith("$"),
                             "Outfit selectors modify variables during buffer selection.")
            expressions = [re.sub(r"^(?:else\s+if|elif)\s+", "", line.text, flags=re.I)
                           for line in branches]
            # An opaque helper or a differently ordered chain stays exactly as
            # authored. Only pure, proven-disjoint selector chains are opened.
            if not safe_to_open or [expected[0], *map(compact, expressions)] != expected:
                continue
            for line, expression in zip(branches, expressions):
                indent = line.raw[:len(line.raw) - len(line.raw.lstrip())]
                patches.append((line.no, [indent + "endif", indent + "if " + expression]))
    for line_no, lines in sorted(patches, reverse=True):
        doc.replace_lines(line_no, line_no + 1, lines)


def _aligned_history(history, conditions):
    """Resolve each outfit through the canonical ordered binding rules."""
    history = [{**item, "conditions": _canonical_conditions(item["conditions"])}
               for item in history]
    missing = object()
    result = []
    for guard in conditions:
        resource = _buffer_resource_at_draw(history, _canonical_conditions(guard), missing)
        _require(resource is not missing,
                 "A buffer binding cannot be proven for every outfit variant.")
        result.append({"resource": resource, "conditions": guard})
    return result


def _canonical_conditions(conditions):
    """Remove contradictions and redundant inequalities before implication checks."""
    result = []
    for group in conditions or [[]]:
        if not _condition_group_is_consistent(group):
            continue
        equal = {clause["var"]: clause["value"] for clause in group if not clause["negate"]}
        clean = [clause for clause in group if not clause["negate"] or clause["var"] not in equal]
        if clean not in result:
            result.append(clean)
    _require(bool(result), "An outfit selector is contradictory.")
    return [] if [] in result else result


def _guard_expression(conditions):
    """Render a proven conjunction without inventing an execution predicate."""
    _require(len(conditions) == 1 and bool(conditions[0]),
             "An automatic draw needs an explicit outfit conjunction.")
    return " && ".join(f"${clause['var']} {'!=' if clause['negate'] else '=='} {clause['value']}"
                       for clause in conditions[0])


def _authored_normals_available(data, stride, ranges):
    """Splitting must not change a draw's geometric-normal fallback policy."""
    if stride != 40:
        return False
    source = VertexAttributeSource("import-position.buf", stride, 12, "f32x3")
    for raw in ranges:
        if raw is None:
            continue
        indices = sorted({value[0] for value in struct.iter_unpack("<I", raw)})
        if decode_normals(source, data, indices) is None:
            return False
    return True


def _plan_record(record, buffers):
    doc = IniDocument.from_string(record.document.to_string(), path=record.path)
    _require(not doc.structure_errors(), "Import requires a structurally valid INI.")
    _require(not any(section.name.casefold().startswith("customshader")
                     for section in doc.sections),
             "Compute and custom-shader buffer imports are not supported yet.")
    resources = extract_resources(record.sections)
    scan = _scan_sections_for_draws(record.sections, resources=resources)
    resolved = _resolve_component_buffers(
        scan, resources, _collect_resource_copy_sources(record.sections, resources),
        sections=record.sections, resource_source=buffers.source)
    families = list(resolved["component_vertex_binding_history"].values())
    _require(len(families) == 1 and all(families[0].get(role)
                                      for role in ("position", "blend", "texcoord")),
             "Import requires one resolved Position, Blend and Texcoord family per INI.")
    selectors = families[0]
    blend_resources = {item["resource"].casefold() for item in selectors["blend"]}
    lookup = {name.casefold(): name for name in record.sections}
    blend_sections = set()
    for name, state in scan.items():
        if name.casefold().startswith("textureoverride") and any(
                str(item["resource"]).casefold() in blend_resources
                for item in state["vertex_binding_history"]):
            blend_sections.update(_reachable_execution_sections(record.sections, name, lookup))
    owners = [(name, state) for name, state in scan.items()
              if name.casefold().startswith("textureoverride") and state["auto_draws"]]
    _require(len(owners) == 1, "Import requires one shared automatic draw family.")
    _owner_name, owner = owners[0]
    variant_conditions = [draw.conditions for draw in owner["auto_draws"]]
    if len(variant_conditions) == 1 and not variant_conditions[0]:
        basis = max(selectors.values(), key=len)
        variant_conditions = [binding["conditions"] for binding in basis]
    guards = [_freeze(condition) for condition in variant_conditions]
    _require(0 < len(guards) <= 64 and len(set(guards)) == len(guards),
             "Import requires distinct, bounded outfit variants.")
    histories = [_aligned_history(selectors[role], variant_conditions)
                 for role in ("position", "blend", "texcoord")]
    auto_history = [{"resource": index, "conditions": draw.conditions}
                    for index, draw in enumerate(owner["auto_draws"])]
    auto_indices = [item["resource"] for item in _aligned_history(auto_history, variant_conditions)]
    _require(all(source.get("phase", "pre") == "pre" for source in owner["auto_sources"]),
             "Post-phase imports are not supported yet.")
    components = [(name, state) for name, state in scan.items()
                  if name.casefold().startswith("textureoverride")
                  and state["index_binding_history"]]
    _require(bool(components), "No component index buffers were found.")
    family = owner["geometry_match_at_end"]
    for _name, state in components:
        match = state["geometry_match_at_end"]
        _require(match and family and match.hash == family.hash
                 and match.first_index is not None and not state["draws"]
                 and not state["auto_draws"]
                 and all(item.get("phase", "pre") == "pre" for item in state["index_binding_history"]),
                 "The component index-buffer family is incomplete or ambiguous.")
        state["index_binding_history"] = _aligned_history(state["index_binding_history"], variant_conditions)
    auto_lines = {source["line_no"] - 1 for source in owner["auto_sources"]}
    authored_draws = {line.no for line in doc.lines
                      if re.match(r"drawindexed\s*=", parse_command_line(line.raw).text, re.I)}
    _require(auto_lines == authored_draws,
             "Import cannot combine automatic and explicit indexed draws yet.")
    source_sections = {source["section"] for source in owner["auto_sources"]}
    _require(len(source_sections) == 1, "Import requires one automatic draw command list.")
    template = doc.section(next(iter(source_sections)))
    _require(template is not None, "The automatic draw command list is missing.")
    template_lines = []
    for line in template.lines:
        command = parse_command_line(line.raw)
        key, separator, value = command.text.casefold().partition("=")
        if command.phase is None and unique_command_metadata(template.name, key.strip()):
            continue
        _require(not command.text or line.kind in (IF, ELIF, ELSE, ENDIF)
                 or (command.phase != "post" and separator
                     and (key.strip(), value.strip()) in {("handling", "skip"), ("drawindexed", "auto")}),
                 f"{record.relative_path} [{template.name}], line {line.no + 1}: "
                 f"unsupported automatic draw command: {line.text}")
        template_lines.append(line)
    first_line = min(auto_lines)
    prefix_count = len(owner["auto_guards"][0]) - doc.lines[first_line].depth
    prefix = owner["auto_guards"][0][:prefix_count]
    _require(prefix_count >= 0 and all(guard[:prefix_count] == prefix
                                     for guard in owner["auto_guards"]),
             "Automatic draw call paths do not share one execution guard.")
    predicates = ([re.sub(r"^(?:else\s+if|elif|if)\s+", "", guard[-1][-1], flags=re.I)
                   for guard in owner["auto_guards"]] if len(owner["auto_draws"]) > 1
                  else [_guard_expression(condition) for condition in variant_conditions]
                  if len(variant_conditions) > 1 else [])

    vertices = ImportVertices()
    shared = vertices.streams
    mappings = []
    filenames = {}
    output = {}
    input_paths = set()
    input_vertices = 0
    strides = None
    uv_layout = None
    for variant in range(len(guards)):
        names = [history[variant]["resource"] for history in histories]
        infos = [_resource(resources, name) for name in names]
        paths = [_path(record, info) for info in infos]
        data = [buffers.raw(path) for path in paths]
        layout = tuple(int(info.get("stride", 0)) for info in infos)
        _require(12 <= layout[0] <= 256 and layout[1] == 32 and 4 <= layout[2] <= 256,
                 "Import requires supported static Position/Blend/Texcoord strides.")
        _require(strides is None or strides == layout,
                 "Outfit variants use incompatible vertex-stream layouts.")
        strides = layout
        selected_uv = _detect_uv_best(paths[2], layout[2], data=data[2])
        _require(uv_layout is None or uv_layout == selected_uv,
                 "Outfit variants use incompatible UV encodings.")
        uv_layout = selected_uv
        counts = [len(raw) // stride for raw, stride in zip(data, strides)]
        _require(all(len(raw) % stride == 0 for raw, stride in zip(data, strides))
                 and counts[0] > 0 and len(set(counts)) == 1,
                 "A variant has malformed or mismatched vertex streams.")
        input_paths.update(paths)
        input_vertices += counts[0]
        _require(input_vertices <= 2_000_000,
                 "Import exceeds the two-million vertex-record safety limit.")
        mapping = []
        for index in range(counts[0]):
            parts = [raw[index * stride:(index + 1) * stride]
                     for raw, stride in zip(data, strides)]
            mapping.append(vertices.add(parts, strides[0]))
        mappings.append(mapping)
        for role, name in enumerate(names):
            filenames[name] = _resource(resources, histories[role][0]["resource"])["filename"]
    for role, stream in enumerate(shared):
        _add_buffer(output, _path(record, _resource(resources, histories[role][0]["resource"])), bytes(stream))
    _require(_detect_uv_best(None, strides[2], data=shared[2]) == uv_layout,
             "The shared stream would change the selected UV encoding.")

    patches = []
    for line_no in auto_lines:
        raw = doc.lines[line_no].raw
        comment = raw[raw.index(";"):] if ";" in raw else ""
        patches.append((line_no, line_no + 1, [comment] if comment else []))
    unique_draws = 0
    remaining_draws = sum(sum(str(binding["resource"]).casefold() != "null"
                              for binding in state["index_binding_history"])
                          for _component, state in components)
    processed_runs = 0
    for component, state in components:
        history = state["index_binding_history"]
        enabled = [binding for binding in history if str(binding["resource"]).casefold() != "null"]
        if not enabled:
            continue
        remaining_draws -= len(enabled)
        first_name = enabled[0]["resource"]
        first_info = _resource(resources, first_name)
        remapped_variants = []
        replacements = {}
        for variant, binding in enumerate(history):
            name = binding["resource"]
            source_line = owner["auto_sources"][auto_indices[variant]]["line_no"] - 1
            replacements.setdefault(source_line, [])
            if str(name).casefold() == "null":
                remapped_variants.append(None)
                continue
            info = _resource(resources, name)
            fmt = str(info.get("format", "")).upper().removeprefix("DXGI_FORMAT_")
            size = {"R16_UINT": 2, "R32_UINT": 4}.get(fmt)
            _require(size is not None, "Import requires explicit R16_UINT or R32_UINT indices.")
            path = _path(record, info)
            raw = buffers.raw(path)
            _require(raw and len(raw) % (3 * size) == 0,
                     "A component index buffer does not contain complete triangles.")
            input_paths.add(path)
            mapping = mappings[variant]
            indices = [item[0] for item in struct.iter_unpack("<H" if size == 2 else "<I", raw)]
            _require(max(indices) < len(mapping), "An index references a missing vertex.")
            remapped = b"".join(struct.pack("<I", mapping[index]) for index in indices)
            remapped_variants.append(remapped)
            filenames[name] = first_info["filename"]
            _rewrite_property(doc, name, "format", "DXGI_FORMAT_R32_UINT")
        can_split = _authored_normals_available(shared[0], strides[0], remapped_variants)
        run_budget = max(0, 9000 - processed_runs - remaining_draws)
        packed_indices = consolidate_index_ranges(remapped_variants, shared[0], strides[0],
                                                  split_parts=can_split, max_runs=run_budget)
        if packed_indices.partitioned:
            part_ranges = {(part.start, part.count) for variant in packed_indices.variants for part in variant}
            if not _authored_normals_available(shared[0], strides[0], (
                    packed_indices.data[start * 4:(start + count) * 4] for start, count in part_ranges)):
                packed_indices = consolidate_index_ranges(remapped_variants, shared[0], strides[0], split_parts=False)
        processed_runs += sum(map(len, packed_indices.variants))
        for variant, ranges in enumerate(packed_indices.variants):
            source_line = owner["auto_sources"][auto_indices[variant]]["line_no"] - 1
            draw_lines = []
            for part in ranges:
                draw_lines.extend([f"ib = {first_name}", f"drawindexed = {part.count}, {part.start}, 0"])
            if len(owner["auto_draws"]) == 1 and len(variant_conditions) > 1:
                draw_lines = [f"if {_guard_expression(variant_conditions[variant])}", *draw_lines, "endif"]
            replacements[source_line].extend(draw_lines)
        lines = [line for branch in prefix for line in branch]
        for line in template_lines:
            lines.extend(replacements.get(line.no, [line.raw]))
        lines.extend(["endif"] * len(prefix))
        target = doc.section(component)
        patches.append((target.end, target.end, ["", *lines, ""]))
        _add_buffer(output, _path(record, first_info), packed_indices.data)
        unique_draws += packed_indices.unique_ranges
    for name, filename in filenames.items():
        _rewrite_property(doc, name, "filename", filename)
    blend_draws = []
    for section in doc.sections:
        if section.name not in blend_sections:
            continue
        for line in section.lines:
            command = parse_command_line(line.raw)
            if re.match(r"draw\s*=", command.text, re.I):
                blend_draws.append((line, command))
    _require(bool(blend_draws) and all(command.phase != "post" and
                                     re.fullmatch(r"draw\s*=\s*\d+\s*,\s*0", command.text, re.I)
                                     for line, command in blend_draws),
             "Import requires literal Blend processing draws.")
    for line, _command in blend_draws:
        match = re.fullmatch(r"(\s*[^=]+=\s*)([^;]*?)(\s*(?:;.*)?)", line.raw)
        line.raw = match[1] + f"{vertices.count}, 0" + match[3]
    for start, end, lines in sorted(patches, reverse=True):
        doc.replace_lines(start, end, lines)
    if predicates:
        _normalize_selectors(doc, predicates)
    _require(not doc.structure_errors(), "The consolidated INI failed structural verification.")
    return doc, output, {
        "variants": len(guards), "input_vertices": input_vertices,
        "shared_vertices": vertices.count, "unique_draws": unique_draws,
        "canonicalized_vertices": vertices.canonicalized,
        "input_bytes": sum(len(buffers.raw(path)) for path in input_paths),
        "shared_bytes": sum(map(len, output.values())),
    }, input_paths


def plan_import(context):
    """Prepare detached documents and bytes; never mutate the source or session."""
    _require(not context.source.read_only, "Import currently requires a mod folder.")
    _require(bool(context.ini.records), "No active INI files were found for import.")
    buffers = BufferStore(source=context.source, overrides=context.buffer_overrides)
    documents, output, reports = {}, {}, []
    inputs = set()
    for record in context.ini.records:
        doc, candidate, report, input_paths = _plan_record(record, buffers)
        _require(not input_paths.intersection(output) and not set(candidate).intersection(inputs),
                 "Sibling INIs share geometry buffers; import is ambiguous.")
        for path, raw in candidate.items():
            _require(path not in output, "Sibling INIs share geometry buffers; import is ambiguous.")
            output[path] = raw
        documents[record.path] = doc
        inputs.update(input_paths)
        reports.append(report)
    summary = {key: sum(report[key] for report in reports) for key in reports[0]}
    summary["export_supported"] = False
    return ImportPlan(documents, output, summary)
