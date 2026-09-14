"""Canonical variable identities and INI source metadata.

The read path used to make a variable unique by prepending an INI-specific
string in each detector.  That made the same authored namespace impossible to
follow across files and made every detector implement its own spelling rules.
This module is the small identity layer shared by program facts, render
effects, and the control graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Iterable, Mapping


# 3DMigoto variable references are either plain names (including ${0}) or a
# backslash-qualified namespace path.  Keep this regex deliberately lexical;
# deciding whether a plain name is local to an INI or belongs to its authored
# namespace is the resolver's job.
VARIABLE_REF_RE = re.compile(
    r"\$(?:\\[A-Za-z0-9_.-]+(?:\\[A-Za-z0-9_.${}-]+)+|\{[^}]+\}|[A-Za-z0-9_.${}-]+)",
    re.I,
)
NAMESPACE_RE = re.compile(r"^\s*namespace\s*=\s*([^;\s]+)", re.I)
LOCAL_DECL_RE = re.compile(
    r"^\s*local\s+\$(?P<name>\\[^=\s]+|\{[^}]+\}|[^=\s]+)", re.I)


def _clean_name(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("$"):
        value = value[1:]
    return value


@dataclass(frozen=True, slots=True)
class VariableId:
    """A case-insensitive, immutable semantic variable identity.

    ``owner`` is an authored namespace for ``kind == "namespace"`` and an
    INI-relative owner for ``kind == "ini"``.  Authored spellings belong on
    facts/projections, not in the identity itself.
    """

    kind: str
    owner: str
    name: str

    def __post_init__(self):
        kind = str(self.kind).casefold()
        if kind not in {"ini", "namespace"}:
            raise ValueError(f"unsupported variable identity kind: {kind}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "owner", str(self.owner or "").strip().casefold())
        object.__setattr__(self, "name", _clean_name(self.name).casefold())

    @property
    def key(self) -> str:
        """Stable transport/control-state key, independent of authored case."""
        return f"{self.kind}:{self.owner}/{self.name}"

    @classmethod
    def from_key(cls, value: str) -> "VariableId":
        text = str(value or "")
        kind, separator, rest = text.partition(":")
        if not separator or kind.casefold() not in {"ini", "namespace"}:
            raise ValueError(f"not a variable identity key: {value!r}")
        owner, separator, name = rest.rpartition("/")
        if not separator or not owner or not name:
            raise ValueError(f"not a variable identity key: {value!r}")
        return cls(kind, owner, name)

    @property
    def is_external(self) -> bool:
        return self.kind == "namespace" and self.owner.split("/", 1)[0] in {
            "wwmi", "wwmiv1", "zzmi", "zzmiv1", "gimi", "gimiv1",
            "srmi", "srmi1", "srmiiv1", "rabbitfx",
        }

    def __str__(self):
        return self.key


def variable_name(reference: str) -> str:
    """Return the authored variable name without ``$`` or namespace path."""
    text = _clean_name(reference)
    if text.startswith("\\"):
        parts = [part for part in text.split("\\") if part]
        return parts[-1] if parts else ""
    return text


def qualified_parts(reference: str) -> tuple[str, ...]:
    """Return non-empty parts of a qualified reference without ``$``."""
    text = _clean_name(reference)
    if not text.startswith("\\"):
        return ()
    return tuple(part for part in text.split("\\") if part)


def extract_variable_references(text: str) -> list[str]:
    """Extract variable tokens in source order, including qualified names."""
    return [match.group(0) for match in VARIABLE_REF_RE.finditer(str(text or ""))]


@dataclass(slots=True)
class IniSource:
    """One independently parsed INI plus its authored namespace metadata."""

    path: str
    relative_path: str
    namespace: str | None
    sections: dict
    resources: dict
    # ``name -> section names`` for variables explicitly declared ``local``.
    local_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def namespace_key(self) -> str | None:
        return self.namespace.casefold() if self.namespace else None


def _document_text(document) -> str:
    return "".join(line.raw + line.eol for line in document.lines)


def top_level_namespace(text: str) -> str | None:
    """Read ``namespace = ...`` only from the file-level preamble."""
    for raw in str(text or "").splitlines():
        # ``utf-8`` decoding intentionally preserves a BOM for lossless
        # editing, so remove it only from the metadata view.
        line = raw.strip().lstrip("\ufeff").strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("["):
            break
        line = line.split(";", 1)[0].strip()
        match = NAMESPACE_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def _local_scopes(sections: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for section, lines in sections.items():
        for raw in lines:
            match = LOCAL_DECL_RE.match(str(raw).split(";", 1)[0])
            if match:
                name = variable_name(match.group("name"))
                if name:
                    found.setdefault(name.casefold(), []).append(str(section))
    return {name: tuple(sections) for name, sections in found.items()}


def source_from_path(path, folder_path=None, *, text=None, document=None) -> IniSource:
    """Build an :class:`IniSource` from disk, text, or an ``IniDocument``.

    ``document`` is preferred by staged reloads.  It is intentionally projected
    without serializing so pending edits remain authoritative and lossless.
    """
    from .sections import extract_resources, parse_sections, sections_from_document

    path = os.fspath(path)
    if document is not None:
        sections = sections_from_document(document)
        source_text = _document_text(document)
    else:
        sections = parse_sections(path, text=text)
        if text is None:
            with open(path, encoding="utf-8", errors="ignore") as stream:
                source_text = stream.read()
        else:
            source_text = text
    folder = os.fspath(folder_path) if folder_path is not None else os.path.dirname(path)
    relative = os.path.relpath(path, folder).replace(os.sep, "/")
    return IniSource(
        path=path,
        relative_path=relative,
        namespace=top_level_namespace(source_text),
        sections=sections,
        resources=extract_resources(sections),
        local_scopes=_local_scopes(sections),
    )


class VariableResolver:
    """Resolve authored references against the complete selected mod."""

    def __init__(self, sources: Iterable[IniSource]):
        self.sources = tuple(sources)
        self._by_path = {
            os.path.normcase(os.path.abspath(source.path)): source
            for source in self.sources
        }
        self._by_relative = {
            source.relative_path.casefold(): source for source in self.sources
        }

    def source_for(self, source) -> IniSource:
        if isinstance(source, IniSource):
            return source
        path = os.path.normcase(os.path.abspath(os.fspath(source)))
        return self._by_path[path]

    def resolve(self, reference: str, source, section: str | None = None) -> VariableId:
        """Resolve a source token without merging declarations or resources."""
        source = self.source_for(source)
        text = _clean_name(reference)
        parts = qualified_parts(reference)
        if parts:
            return VariableId("namespace", "/".join(parts[:-1]), parts[-1])

        name = variable_name(reference)
        local_sections = source.local_scopes.get(name.casefold(), ())
        if local_sections and (section is None or section in local_sections):
            owner = f"{source.relative_path}::{section or local_sections[0]}"
            return VariableId("ini", owner, name)
        if source.namespace:
            return VariableId("namespace", source.namespace, name)
        return VariableId("ini", source.relative_path, name)

    def authored_name(self, reference: str) -> str:
        return variable_name(reference)

    def resolve_many(self, references: Iterable[str], source, section=None) -> tuple[VariableId, ...]:
        return tuple(self.resolve(ref, source, section) for ref in references)


def variable_id_key(value) -> str:
    """Normalize a ``VariableId``/authored token for public mappings."""
    if isinstance(value, VariableId):
        return value.key
    return str(value)


__all__ = [
    "IniSource", "VariableId", "VariableResolver", "VARIABLE_REF_RE",
    "extract_variable_references", "qualified_parts", "source_from_path",
    "top_level_namespace", "variable_id_key", "variable_name",
]
