"""One conservative, source-ordered scan of INI program facts.

This is not a 3DMigoto interpreter.  It records the assignments, conditions,
run edges, and input sections that the control graph needs, retaining unknown
expressions and their dependencies instead of guessing their values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

from .dnf import (DNF_TRUE, build_bool_alias_map, canonicalize_dnf, dnf_and,
                  dnf_not, dnf_or, parse_condition_dnf)
from .sections import line_source
from .variables import (IniSource, VariableId, VariableResolver,
                        extract_variable_references, variable_name)


_VAR = r'(?:\\[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.${}-]+)+|\{[^}]+\}|[A-Za-z0-9_.${}-]+)'
_ASSIGN_RE = re.compile(rf'^(?P<prefix>(?:(?:(?:global|local)(?:\s+persist)?|post)\s+)?)'
                        rf'(?P<target>\${_VAR})\s*=\s*(?P<expression>.+)$', re.I)
_DECL_RE = re.compile(rf'^global\s+(?P<persist>persist\s+)?'
                      rf'(?P<target>\${_VAR})(?:\s*=\s*(?P<value>.*))?$', re.I)
_LOCAL_RE = re.compile(rf'^local\s+(?P<target>\${_VAR})(?:\s*=\s*(?P<value>.*))?$', re.I)
_RUN_RE = re.compile(r'^run\s*=\s*(\S+)', re.I)
_ELIF_RE = re.compile(r'(?:else\s+if|elif)\s+(.*)$', re.I)
_NUMERIC_RE = re.compile(r'^-?\d+(?:\.\d+)?$')
_POST_RE = re.compile(r'^post\s+(.+)$', re.I)


def _clean(raw) -> str:
    text = str(raw).strip()
    # Semicolon is a valid 3DMigoto key binding.  The section parser already
    # preserves it on key/back assignments; keep the scanner consistent so
    # ``key = ;`` reaches KeyInput.key as ``;`` instead of an empty binding.
    if re.fullmatch(r"(?i)(?:key|back)\s*=\s*;", text):
        return text
    return text.split(";", 1)[0].strip()


def _literal(value: str):
    value = value.strip()
    return value if _NUMERIC_RE.fullmatch(value) else None


def _cycle_values(value: str) -> tuple[str, ...]:
    if "," not in value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


_COMPARISON_RE = re.compile(
    rf'\$({_VAR})\s*(==|!=|<=|>=|<|>)\s*(-?[\w.]+)', re.I)


def _comparison_ops(text):
    return {
        (token.casefold(), value.casefold()): op
        for token, op, value in _COMPARISON_RE.findall(str(text or ""))
    }


def _resolve_dnf(dnf, resolver, source, section, raw_hints=()):
    operations = {}
    for hint in raw_hints or ():
        operations.update(_comparison_ops(hint))
    result = []
    for group in canonicalize_dnf(dnf):
        resolved_group = []
        for clause in group:
            authored = str(clause["var"])
            resolved = resolver.resolve(authored, source, section)
            item = {**clause, "var": resolved.key}
            op = operations.get((authored.casefold(),
                                 str(clause.get("value", "")).casefold()))
            if op:
                item["op"] = op
            resolved_group.append(item)
        result.append(resolved_group)
    return result


@dataclass(frozen=True, slots=True)
class VariableDeclaration:
    var: VariableId
    default: str | None = None
    persist: bool = False
    source: dict | None = None
    authored_name: str = ""


@dataclass(frozen=True, slots=True)
class VariableWrite:
    target: VariableId
    expression: str
    dependencies: tuple[VariableId, ...] = ()
    literal: str | None = None
    cycle_values: tuple[str, ...] = ()
    conditions: tuple = ()
    section: str = ""
    phase: str = "immediate"
    source: dict | None = None
    authored_target: str = ""
    exact_copy: bool = False


@dataclass(frozen=True, slots=True)
class RunEdge:
    source_section: str
    target_section: str
    conditions: tuple = ()
    source: dict | None = None


@dataclass(frozen=True, slots=True)
class KeyInput:
    section: str
    type: str = ""
    key: str = ""
    back: str = ""
    conditions: tuple = ()
    writes: tuple[VariableId, ...] = ()
    runs: tuple[str, ...] = ()
    source: dict | None = None


@dataclass(slots=True)
class ProgramFacts:
    source: IniSource
    declarations: list[VariableDeclaration] = field(default_factory=list)
    writes: list[VariableWrite] = field(default_factory=list)
    run_edges: list[RunEdge] = field(default_factory=list)
    key_inputs: list[KeyInput] = field(default_factory=list)

    @property
    def variables(self) -> set[VariableId]:
        result = {item.var for item in self.declarations}
        result.update(item.target for item in self.writes)
        result.update(dep for item in self.writes for dep in item.dependencies)
        return result


def _section_lookup(source: IniSource):
    return {str(name).casefold(): name for name in source.sections}


def _condition_for_stack(stack) -> list:
    result = DNF_TRUE
    for frame in stack:
        result = dnf_and(result, frame["current"])
    return result


def _resolve_condition(condition, aliases, resolver, source, section):
    raw = parse_condition_dnf(condition, aliases)
    return _resolve_dnf(raw, resolver, source, section, (condition,))


def scan_program(source: IniSource, resolver: VariableResolver | None = None) -> ProgramFacts:
    """Scan one source while resolving its variables through a mod resolver."""
    resolver = resolver or VariableResolver([source])
    sections = source.sections
    lookup = _section_lookup(source)
    aliases = build_bool_alias_map(sections)
    facts = ProgramFacts(source)

    for section, lines in sections.items():
        # Local declarations are also collected by IniSource, but recording
        # them here gives the graph provenance and preserves source order.
        for raw in lines:
            line = _clean(raw)
            decl = _DECL_RE.match(line)
            if decl:
                authored = decl.group("target")
                facts.declarations.append(VariableDeclaration(
                    resolver.resolve(authored, source, section),
                    default=(_literal(decl.group("value")) if decl.group("value") else None),
                    persist=bool(decl.group("persist")),
                    source=line_source(raw),
                    authored_name=variable_name(authored),
                ))

    for section, lines in sections.items():
        stack = []
        key_type = ""
        key_combo = ""
        back_combo = ""
        key_conditions = DNF_TRUE
        key_condition_text = ""
        key_writes = []
        key_runs = []
        for raw in lines:
            line = _clean(raw)
            if not line:
                continue
            low = line.casefold()
            match = _ELIF_RE.fullmatch(line)
            if match:
                if stack:
                    frame = stack[-1]
                    branch = parse_condition_dnf(match.group(1), aliases)
                    frame["current"] = dnf_and(dnf_not(frame["seen"]), branch)
                    frame["seen"] = dnf_or(frame["seen"], branch)
                    frame["hint"] = match.group(1)
                continue
            if low.startswith("if "):
                branch = parse_condition_dnf(line[3:].strip(), aliases)
                stack.append({"current": branch, "seen": branch,
                              "hint": line[3:].strip()})
                continue
            if low == "else":
                if stack:
                    stack[-1]["current"] = dnf_not(stack[-1]["seen"])
                    stack[-1]["hint"] = None
                continue
            if low == "endif":
                if stack:
                    stack.pop()
                continue

            if low.startswith("key") and "=" in line:
                lhs, _, value = line.partition("=")
                if lhs.strip().casefold() == "key":
                    key_combo = value.strip()
            elif low.startswith("back") and "=" in line:
                lhs, _, value = line.partition("=")
                if lhs.strip().casefold() == "back":
                    back_combo = value.strip()
            elif low.startswith("type") and "=" in line:
                lhs, _, value = line.partition("=")
                if lhs.strip().casefold() == "type":
                    key_type = value.strip().casefold()
            elif low.startswith("condition") and "=" in line:
                lhs, _, value = line.partition("=")
                if lhs.strip().casefold() == "condition":
                    key_condition_text = value.strip()
                    key_conditions = parse_condition_dnf(
                        key_condition_text, aliases)

            run = _RUN_RE.match(line)
            if run:
                target = lookup.get(run.group(1).casefold())
                if target is not None:
                    current = _condition_for_stack(stack)
                    conditions = _resolve_dnf(
                        current, resolver, source, section,
                        [frame.get("hint") for frame in stack])
                    facts.run_edges.append(RunEdge(
                        section, target,
                        tuple(tuple(group) for group in conditions),
                        line_source(raw)))
                    if section.casefold().startswith("key"):
                        key_runs.append(target)

            assignment = _ASSIGN_RE.match(line)
            if not assignment:
                continue
            authored_target = assignment.group("target")
            expression = assignment.group("expression").strip()
            phase = ("post" if assignment.group("prefix").strip().casefold()
                     == "post" else "immediate")
            post = _POST_RE.match(expression)
            if post:
                phase = "post"
                expression = post.group(1).strip()
            current = _condition_for_stack(stack)
            conditions = _resolve_dnf(
                current, resolver, source, section,
                [frame.get("hint") for frame in stack])
            dependencies = tuple(
                resolver.resolve(ref, source, section)
                for ref in extract_variable_references(expression)
            )
            exact = (len(dependencies) == 1 and
                     expression.strip().casefold() ==
                     f"${variable_name(dependencies[0].name)}".casefold())
            # The comparison above intentionally uses the authored-token form
            # below as well; qualified copies must remain exact copies.
            refs = extract_variable_references(expression)
            exact = len(refs) == 1 and expression.strip().casefold() == refs[0].casefold()
            write = VariableWrite(
                target=resolver.resolve(authored_target, source, section),
                expression=expression,
                dependencies=dependencies,
                literal=_literal(expression),
                cycle_values=(_cycle_values(expression)
                              if section.casefold().startswith("key") else ()),
                conditions=tuple(tuple(group) for group in conditions),
                section=section, phase=phase, source=line_source(raw),
                authored_target=variable_name(authored_target),
                exact_copy=exact,
            )
            facts.writes.append(write)
            if (section.casefold().startswith("key")
                    and phase != "post"):
                key_writes.append(write.target)

        if section.casefold().startswith("key"):
            src = next((line_source(raw) for raw in lines if line_source(raw)), None)
            normalized_conditions = _resolve_dnf(
                key_conditions, resolver, source, section,
                (key_condition_text,))
            facts.key_inputs.append(KeyInput(
                section=section, type=key_type, key=key_combo,
                back=back_combo,
                conditions=tuple(tuple(group) for group in normalized_conditions),
                writes=tuple(dict.fromkeys(key_writes)),
                runs=tuple(dict.fromkeys(key_runs)), source=src,
            ))
    return facts


def _format_key_combo(combo: str) -> str:
    """Compact a 3DMigoto key binding for projections."""
    mods, main = [], None
    for token in str(combo or "").split():
        low = token.casefold()
        if low.startswith("no_"):
            continue
        if low in {"ctrl", "shift", "alt"}:
            mods.append(low.capitalize())
        else:
            main = token
    if main is None:
        return str(combo or "")
    return "+".join(mods + [main.upper() if len(main) == 1 else main])


__all__ = [
    "KeyInput", "ProgramFacts", "RunEdge", "VariableDeclaration",
    "VariableWrite", "_format_key_combo", "scan_program",
]
