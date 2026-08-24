"""Offline WuWa texture-corpus scanner.

The scanner is intentionally read-only with respect to a mod library.  It
parses active INIs with the application's pre-enrichment parser, records the
parser's authoritative role evidence, and keeps filename/classifier evidence
as diagnostics.  It does not call the runtime enrichment or mesh-building
pipeline.

Usage::

    python -m tools.wuwa_texture_corpus scan <mods-root> --output <corpus>
    python -m tools.wuwa_texture_corpus summary <corpus>
    python -m tools.wuwa_texture_corpus review <corpus> --mods-root <mods-root>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from app import mod_loader  # noqa: E402
from core import dds_classifier as _dds_classifier  # noqa: E402
from core.dds import inspect_dds  # noqa: E402
from core.dds_classifier import classify_dds  # noqa: E402
from core.resource_paths import safe_resource_path  # noqa: E402
from core.texture_features import (  # noqa: E402
    FEATURE_SCHEMA_VERSION,
    extract_texture_features,
    model_feature_columns,
)
from core.textures import load_texture_image  # noqa: E402


SCHEMA_VERSION = "wuwa-texture-corpus-v1"
SCANNER_VERSION = "1"
ARCHIVE_SUFFIXES = frozenset({".7z", ".rar", ".zip"})
_COMPONENT_RE = re.compile(r"^Component(?P<ordinal>\d+)(?:_\d+)?$", re.I)
_FILENAME_RE = re.compile(
    r"^Components-(?P<components>\d+(?:-\d+)*)\s+t=(?P<tag>.+?)\.dds$",
    re.I,
)
_SEMANTIC_RESOURCE_RE = re.compile(
    r"^Resource[\\/](?:GIMI|ZZMI|RabbitFX|WWMI)[\\/]"
    r"(?P<role>Diffuse|NormalMap|LightMap|MaterialMap)$",
    re.I,
)
_ROLE_LABELS = {
    "diffuse": "diffuse",
    "normal_map": "normal_map",
    "light_map": "light_map",
    "material_map": "material_map",
}
_PRIMARY_SOURCES = frozenset({
    "explicit_semantic_binding",
    "mod_slot_mapping",
})
_SECONDARY_SOURCES = frozenset({"legacy_slot_mapping"})

MOD_FIELDS = [
    "mod_id", "relative_path", "game", "parse_status", "ini_count",
    "component_count", "texture_count", "character_signature", "source",
    "warnings",
]
OCCURRENCE_FIELDS = [
    "mod_id", "game", "character_signature", "component",
    "texture_sha256", "relative_file", "context", "source",
    "association_tier", "association_count", "associated_components",
    "ini", "resource", "slot", "role", "original_texture_hash",
    "filename_tag",
]
LABEL_FIELDS = [
    "texture_sha256", "label", "label_source", "label_tier", "mod_id",
    "component", "relative_file", "role", "ini", "resource", "slot",
]
UNKNOWN_FIELDS = [
    "texture_sha256", "label_status", "example_mod_id", "example_file",
    "occurrence_count", "dds_format", "dds_width", "dds_height",
    "baseline_texture_class", "baseline_role", "baseline_confidence",
]
MANUAL_FIELDS = [
    "texture_sha256", "label", "notes", "reviewer", "source",
]
MANUAL_LABELS = frozenset({
    "", "unknown", "diffuse", "normal_map", "light_map", "material_map",
})
COMPONENT_LABEL_FIELDS = [
    "texture_sha256", "component", "label", "label_source", "label_tier",
    "mod_id", "relative_file", "role",
]


def _json(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else value


def _write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = sorted({key for row in rows for key in row if key not in fields})
    columns = list(fields) + extra
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json(row.get(key)) for key in columns})


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def _relative(path, root):
    return Path(os.path.relpath(path, root)).as_posix()


def _active_ini_files(directory):
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [
        os.path.join(directory, name)
        for name in sorted(names, key=str.casefold)
        if name.lower().endswith(".ini")
        and not name.upper().startswith("DISABLED")
        and os.path.isfile(os.path.join(directory, name))
    ]


def _walk_files(root, suffixes=None):
    suffixes = {suffix.lower() for suffix in suffixes} if suffixes else None
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.upper().startswith("DISABLED"))
        for name in sorted(filenames, key=str.casefold):
            path = os.path.join(directory, name)
            if suffixes and Path(name).suffix.lower() not in suffixes:
                continue
            yield path


def discover_mod_directories(mods_root):
    """Return every active-INI directory below ``mods_root``.

    The directory itself is the dataset's mod boundary.  Child directories
    with their own active INIs are also scanned independently, which avoids
    sharing parser/resource state between sibling extracted mods.
    """
    result = []
    for directory, dirnames, _filenames in os.walk(mods_root):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.upper().startswith("DISABLED"))
        if _active_ini_files(directory):
            result.append(os.path.normpath(directory))
    return result


def discover_archives(mods_root):
    return list(_walk_files(mods_root, ARCHIVE_SUFFIXES))


def _walk_mod_textures(root, mod_boundaries):
    """Yield textures owned by one mod without entering child mod roots."""
    boundary_set = {os.path.normcase(os.path.normpath(path))
                    for path in mod_boundaries}
    root = os.path.normpath(root)
    for directory, dirnames, filenames in os.walk(root):
        normalized_directory = os.path.normcase(os.path.normpath(directory))
        if normalized_directory != os.path.normcase(root):
            if normalized_directory in boundary_set:
                dirnames[:] = []
                continue
        kept_dirs = []
        for name in sorted(dirnames):
            if name.upper().startswith("DISABLED"):
                continue
            child = os.path.normcase(os.path.normpath(
                os.path.join(directory, name)))
            if child in boundary_set:
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames, key=str.casefold):
            if Path(name).suffix.lower() == ".dds":
                yield os.path.join(directory, name)


def _basename(value):
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def parse_component_ordinal(value):
    match = _COMPONENT_RE.fullmatch(str(value or ""))
    return int(match.group("ordinal")) if match else None


def filename_association(components, ordinal):
    """Return the diagnostic association tier used by the existing fallback."""
    components = tuple(int(item) for item in components)
    if ordinal not in components:
        return None
    if components == (ordinal,):
        return "exact"
    if components[0] == ordinal:
        return "leading"
    return "contains"


def parse_component_filename(filename):
    match = _FILENAME_RE.fullmatch(_basename(filename))
    if not match:
        return None
    components = tuple(int(value) for value in match.group("components").split("-"))
    return {
        "components": components,
        "tag": match.group("tag"),
    }


def _geometry_hash(value):
    match = getattr(value, "hash", None)
    if match:
        return str(match).lower()
    if isinstance(value, dict):
        return value.get("hash")
    return None


def character_signature(groups, fallback):
    hashes = set()
    for group in groups or ():
        hashes.add(_geometry_hash(group.get("geometry_match")))
        for draw in group.get("draws", ()):
            hashes.add(_geometry_hash(getattr(draw, "geometry_match", None)))
    hashes.discard(None)
    if not hashes:
        return fallback
    digest = hashlib.sha256("|".join(sorted(hashes)).encode("ascii")).hexdigest()
    return digest[:16]


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _role_default(draw, role):
    method = getattr(draw, "texture_default", None)
    if method:
        return method(role)
    prefix = "texture" if role == "diffuse" else role
    return _get(draw, f"{prefix}_default_file")


def _role_variants(draw, role):
    prefix = "texture" if role == "diffuse" else role
    return list(_get(draw, f"{prefix}_variants", ()) or ())


def _role_assignments(draw, role):
    if role == "diffuse":
        return list(_get(draw, "texture_assignments", ()) or ())
    return []


def _source_for_role(draw, role):
    provenance = _get(draw, "texture_provenance", {}) or {}
    value = provenance.get(role)
    if value == "mod_slot_semantic":
        return "mod_slot_mapping", "primary"
    if value == "mod_slot_legacy":
        return "legacy_slot_mapping", "secondary"
    return "explicit_semantic_binding", "primary"


def _semantic_resource_role(resource):
    match = _SEMANTIC_RESOURCE_RE.fullmatch(str(resource or ""))
    if not match:
        return None
    role = match.group("role").casefold()
    return {
        "diffuse": "diffuse",
        "normalmap": "normal_map",
        "lightmap": "light_map",
        "materialmap": "material_map",
    }[role]


def _diagnostic_baseline(path, info, image):
    """Evaluate the existing classifier without decoding the image twice."""
    if (info and image and info.format not in {"bc5_unorm", "bc5_snorm",
                                               "bc4_unorm", "bc4_snorm"}
            and max(info.width, info.height) > 4
            and info.format not in {"bc6h_ufloat", "bc6h_float"}):
        return _dds_classifier._decoded_classification(info, image)
    if (info and (info.format in {"bc5_unorm", "bc5_snorm", "bc4_unorm",
                                  "bc4_snorm", "bc6h_ufloat", "bc6h_float"}
                  or max(info.width, info.height) <= 4)):
        return classify_dds(path)
    return None


class CorpusBuilder:
    def __init__(self, mods_root, output_dir, *, wuwa_only=True,
                 progress=False, pixel_limit=None, max_mods=None):
        self.mods_root = os.path.normpath(os.path.abspath(mods_root))
        self.output_dir = Path(output_dir)
        self.wuwa_only = wuwa_only
        self.progress = progress
        self.pixel_limit = (None if pixel_limit is None
                            else max(0, int(pixel_limit)))
        self.max_mods = (None if max_mods is None
                         else max(0, int(max_mods)))
        self.textures = {}
        self.texture_examples = {}
        self.texture_occurrence_counts = Counter()
        self.occurrences = []
        self.trusted_labels = []
        self._label_keys = set()
        self.component_labels = []
        self._component_label_keys = set()
        self.mods = []
        self.errors = []
        self.feature_cache_hits = 0
        self.feature_extractions = 0
        self.pixel_decoded = 0
        self.pixel_skipped_budget = 0
        self.manual_labels = []
        self.manual_label_errors = []
        self._load_feature_cache()
        self._load_manual_labels()

    def _load_feature_cache(self):
        path = self.output_dir / "feature_cache.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if payload.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            return
        for sha, row in (payload.get("entries") or {}).items():
            if isinstance(sha, str) and isinstance(row, dict):
                self.textures[sha] = dict(row)

    def _flush_feature_cache(self):
        _write_json(self.output_dir / "feature_cache.json", {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "entries": {
                sha: {key: value for key, value in row.items()
                      if key not in {"example_mod_id", "example_relative_file",
                                     "occurrence_count"}}
                for sha, row in sorted(self.textures.items())
            },
        })

    def _load_manual_labels(self):
        path = self.output_dir / "manual_labels.csv"
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                rows = csv.DictReader(stream)
                for row in rows:
                    normalized = {field: (row.get(field) or "")
                                  for field in MANUAL_FIELDS}
                    if normalized["label"] not in MANUAL_LABELS:
                        self.manual_label_errors.append({
                            "kind": "manual_label",
                            "sha256": normalized["texture_sha256"],
                            "error": f"unsupported label: {normalized['label']}",
                        })
                        continue
                    if not normalized["texture_sha256"]:
                        self.manual_label_errors.append({
                            "kind": "manual_label",
                            "error": "missing texture_sha256",
                        })
                        continue
                    self.manual_labels.append(normalized)
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, csv.Error) as exc:
            self.manual_label_errors.append({
                "kind": "manual_label", "error": str(exc),
            })

    def _ensure_texture(self, path, mod_id, relative_file):
        try:
            data = Path(path).read_bytes()
        except (OSError, ValueError) as exc:
            self.errors.append({"kind": "texture_read", "file": relative_file,
                                "mod_id": mod_id, "error": str(exc)})
            return None
        sha = hashlib.sha256(data).hexdigest()
        self.texture_occurrence_counts[sha] += 1
        if sha in self.textures:
            self.feature_cache_hits += 1
            row = self.textures[sha]
            row.setdefault("sha256", sha)
            row["example_mod_id"] = row.get("example_mod_id") or mod_id
            row["example_relative_file"] = (
                row.get("example_relative_file") or relative_file)
            self.texture_examples.setdefault(sha, (mod_id, relative_file, path))
            return sha

        info = inspect_dds(path)
        decode_allowed = (
            bool(info)
            and (self.pixel_limit is None or self.pixel_decoded < self.pixel_limit))
        image = load_texture_image(path, max_size=128, preserve_alpha=True) \
            if decode_allowed else None
        if image is not None:
            self.pixel_decoded += 1
        elif info and not decode_allowed:
            self.pixel_skipped_budget += 1
        try:
            baseline = _diagnostic_baseline(path, info, image)
        except Exception as exc:  # diagnostics must never stop corpus scanning
            baseline = None
            self.errors.append({"kind": "baseline", "file": relative_file,
                                "mod_id": mod_id, "error": str(exc)})
        try:
            features = extract_texture_features(
                path, dds_info=info, image=image, baseline=baseline,
                decode=decode_allowed)
            if info and not decode_allowed and features.get("decode_status") == "unavailable":
                features["decode_status"] = "skipped_budget"
        except Exception as exc:
            features = {
                "feature_version": FEATURE_SCHEMA_VERSION,
                "dds_valid": bool(info),
                "decode_status": "feature_error",
            }
            self.errors.append({"kind": "feature", "file": relative_file,
                                "mod_id": mod_id, "error": str(exc)})
        self.feature_extractions += 1
        row = {
            "sha256": sha,
            "file_size": len(data),
            "example_mod_id": mod_id,
            "example_relative_file": relative_file,
            **features,
        }
        self.textures[sha] = row
        self.texture_examples.setdefault(sha, (mod_id, relative_file, path))
        if self.progress and self.feature_extractions % 250 == 0:
            print(f"features: {self.feature_extractions} unique DDS",
                  file=sys.stderr, flush=True)
            self._flush_feature_cache()
        return sha

    def _resolve_texture(self, mod_dir, filename):
        if not isinstance(filename, str) or not filename.lower().endswith(".dds"):
            return None
        path = safe_resource_path(mod_dir, filename)
        if path is None or not os.path.isfile(path):
            return None
        return path

    def _add_occurrence(self, *, mod, path, context, source, component=None,
                        association_tier=None, associated_components=(), ini=None,
                        resource=None, slot=None, role=None,
                        original_texture_hash=None, filename_tag=None):
        relative_file = _relative(path, mod["path"])
        sha = self._ensure_texture(path, mod["mod_id"], relative_file)
        if sha is None:
            return None
        associated_components = tuple(associated_components or ())
        row = {
            "mod_id": mod["mod_id"],
            "game": mod["game"],
            "character_signature": mod["character_signature"],
            "component": component,
            "texture_sha256": sha,
            "relative_file": relative_file,
            "context": context,
            "source": source,
            "association_tier": association_tier,
            "association_count": len(associated_components)
            if associated_components else None,
            "associated_components": associated_components,
            "ini": ini,
            "resource": resource,
            "slot": slot,
            "role": role,
            "original_texture_hash": original_texture_hash,
            "filename_tag": filename_tag,
        }
        self.occurrences.append(row)
        return sha

    def _add_label(self, sha, *, label, source, tier, mod, component, path,
                   role, ini=None, resource=None, slot=None):
        if source not in _PRIMARY_SOURCES | _SECONDARY_SOURCES:
            return
        row = {
            "texture_sha256": sha,
            "label": label,
            "label_source": source,
            "label_tier": tier,
            "mod_id": mod["mod_id"],
            "component": component,
            "relative_file": _relative(path, mod["path"]),
            "role": role,
            "ini": ini,
            "resource": resource,
            "slot": slot,
        }
        key = tuple(row.get(field) for field in LABEL_FIELDS)
        if key in self._label_keys:
            return
        self._label_keys.add(key)
        self.trusted_labels.append(row)
        if component is not None:
            component_row = {
                "texture_sha256": sha,
                "component": component,
                "label": label,
                "label_source": source,
                "label_tier": tier,
                "mod_id": mod["mod_id"],
                "relative_file": row["relative_file"],
                "role": role,
            }
            component_key = tuple(component_row.get(field)
                                  for field in COMPONENT_LABEL_FIELDS)
            if component_key not in self._component_label_keys:
                self._component_label_keys.add(component_key)
                self.component_labels.append(component_row)

    def _record_parser_evidence(self, mod, group):
        component = parse_component_ordinal(
            group.get("display_name") or group.get("name"))
        component_name = (f"Component{component}"
                          if component is not None else None)
        for draw in group.get("draws", ()):
            source_info = _get(draw, "sources", ()) or ()
            source_info = source_info[0] if source_info else {}
            ini = (_relative(source_info["ini_path"], mod["path"])
                   if source_info.get("ini_path") else None)
            for role in ("diffuse", "normal_map", "light_map", "material_map"):
                files = []
                default_file = _role_default(draw, role)
                if default_file:
                    files.append((default_file, None))
                for item in _role_variants(draw, role):
                    if isinstance(item, dict) and item.get("file"):
                        files.append((item["file"], item.get("texture_hashes")))
                for item in _role_assignments(draw, role):
                    if isinstance(item, dict) and item.get("file"):
                        files.append((item["file"], item.get("texture_hashes")))
                seen = set()
                label_source, label_tier = _source_for_role(draw, role)
                for filename, hashes in files:
                    path = self._resolve_texture(mod["path"], filename)
                    if path is None:
                        continue
                    key = (os.path.normcase(path), role)
                    if key in seen:
                        continue
                    seen.add(key)
                    sha = self._add_occurrence(
                        mod=mod, path=path, context="parser_binding",
                        source=label_source, component=component_name,
                        ini=ini, resource=None, role=role,
                        original_texture_hash="|".join(hashes or ()) or None)
                    if sha:
                        self._add_label(
                            sha, label=_ROLE_LABELS[role], source=label_source,
                            tier=label_tier, mod=mod, component=component_name,
                            path=path, role=role, ini=ini)

            for binding in _get(draw, "slot_textures", ()) or ():
                path = self._resolve_texture(
                    mod["path"], _get(binding, "file"))
                if path is None:
                    continue
                role = _get(binding, "role_hint")
                source = _get(binding, "role_hint_source")
                label_source = {
                    "mod_slot_mapping": "mod_slot_mapping",
                    "legacy_slot_mapping": "legacy_slot_mapping",
                }.get(source)
                tier = "primary" if label_source == "mod_slot_mapping" \
                    else "secondary" if label_source else None
                hashes = _get(binding, "texture_hashes", ()) or ()
                sha = self._add_occurrence(
                    mod=mod, path=path, context="parser_slot_binding",
                    source=label_source or "slot_binding", component=component_name,
                    ini=ini, resource=_get(binding, "resource"),
                    slot=_get(binding, "slot"), role=role,
                    original_texture_hash="|".join(hashes) or None)
                if sha and label_source and role in _ROLE_LABELS:
                    self._add_label(
                        sha, label=_ROLE_LABELS[role], source=label_source,
                        tier=tier, mod=mod, component=component_name, path=path,
                        role=role, ini=ini, resource=_get(binding, "resource"),
                        slot=_get(binding, "slot"))

        for entry in group.get("diffuse_pool_files", ()) or ():
            resource = entry.get("res")
            role = _semantic_resource_role(resource)
            path = self._resolve_texture(mod["path"], entry.get("file"))
            if path is None:
                continue
            sha = self._add_occurrence(
                mod=mod, path=path, context="parser_resource_pool",
                source="explicit_semantic_binding" if role else "resource_pool",
                component=component_name, resource=resource, role=role)
            if sha and role == "diffuse":
                self._add_label(
                    sha, label="diffuse", source="explicit_semantic_binding",
                    tier="primary", mod=mod, component=component_name, path=path,
                    role=role, resource=resource)

    def _record_filename_evidence(self, mod, dds_files, components):
        for path in dds_files:
            parsed = parse_component_filename(os.path.basename(path))
            if not parsed:
                continue
            for component in components:
                tier = filename_association(parsed["components"], component)
                if tier is None:
                    continue
                self._add_occurrence(
                    mod=mod, path=path, context="filename_association",
                    source="filename_analysis", component=f"Component{component}",
                    association_tier=tier,
                    associated_components=parsed["components"],
                    original_texture_hash=parsed["tag"],
                    filename_tag=parsed["tag"])

    def scan(self):
        root = self.mods_root
        all_mod_dirs = discover_mod_directories(root)
        mod_dirs = (all_mod_dirs if self.max_mods is None
                    else all_mod_dirs[:self.max_mods])
        self._mods_total_discovered = len(all_mod_dirs)
        mod_boundaries = set(mod_dirs)
        archive_paths = discover_archives(root)
        for mod_path in mod_dirs:
            mod_id = _relative(mod_path, root)
            ini_paths = _active_ini_files(mod_path)
            mod = {
                "mod_id": mod_id,
                "relative_path": mod_id,
                "path": mod_path,
                "game": "unknown",
                "character_signature": mod_id,
                "parse_status": "error",
                "ini_count": len(ini_paths),
                "component_count": 0,
                "texture_count": 0,
                "source": "pre_enrichment_parser",
                "warnings": [],
            }
            parsed = None
            try:
                parsed = mod_loader._parse_inis(ini_paths, mod_path)
                mod["game"] = parsed.game.game
                mod["parse_status"] = "ok" if parsed.groups else "no_geometry"
                mod["character_signature"] = character_signature(
                    parsed.groups, mod_id)
                mod["component_count"] = len(parsed.groups)
                if not parsed.groups:
                    mod["warnings"].append("no_draw_groups")
            except Exception as exc:
                mod["warnings"].append(f"parse_error:{exc}")
                self.errors.append({"kind": "parse", "mod_id": mod_id,
                                    "error": str(exc)})
            self.mods.append(mod)
            if self.wuwa_only and mod["game"] != "wuwa":
                mod["warnings"].append("excluded_non_wuwa")
                continue

            dds_files = list(_walk_mod_textures(mod_path, mod_boundaries))
            mod_shas = set()
            for path in dds_files:
                relative_file = _relative(path, mod_path)
                sha = self._ensure_texture(path, mod_id, relative_file)
                if sha:
                    mod_shas.add(sha)
                    self.occurrences.append({
                        "mod_id": mod_id,
                        "game": mod["game"],
                        "character_signature": mod["character_signature"],
                        "component": None,
                        "texture_sha256": sha,
                        "relative_file": relative_file,
                        "context": "file_inventory",
                        "source": "file_inventory",
                        "association_tier": "inventory",
                        "association_count": None,
                        "associated_components": (),
                        "ini": None,
                        "resource": None,
                        "slot": None,
                        "role": None,
                        "original_texture_hash": None,
                        "filename_tag": None,
                    })
            mod["texture_count"] = len(mod_shas)
            if parsed:
                components = set()
                for group in parsed.groups:
                    ordinal = parse_component_ordinal(
                        group.get("display_name") or group.get("name"))
                    if ordinal is not None:
                        components.add(ordinal)
                    self._record_parser_evidence(mod, group)
                self._record_filename_evidence(mod, dds_files, sorted(components))

        return self._write(archive_paths)

    def _write(self, archive_paths):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for row in self.mods:
            row["warnings"] = ";".join(row["warnings"])
            row.pop("path", None)
        for row in self.textures.values():
            row["occurrence_count"] = self.texture_occurrence_counts[row["sha256"]]
        _write_csv(self.output_dir / "mods.csv", self.mods, MOD_FIELDS)
        _write_csv(self.output_dir / "textures.csv",
                   sorted(self.textures.values(), key=lambda row: row["sha256"]),
                   ["sha256", "file_size", "example_mod_id",
                    "example_relative_file", "occurrence_count"])
        _write_csv(self.output_dir / "occurrences.csv", self.occurrences,
                   OCCURRENCE_FIELDS)
        _write_csv(self.output_dir / "trusted_labels.csv",
                   sorted(self.trusted_labels,
                          key=lambda row: tuple(row.get(key) or ""
                                                for key in LABEL_FIELDS)),
                   LABEL_FIELDS)
        trusted_shas = {row["texture_sha256"] for row in self.trusted_labels}
        unknown = []
        for sha, row in sorted(self.textures.items()):
            if sha in trusted_shas:
                continue
            example = self.texture_examples.get(sha, (None, None, None))
            unknown.append({
                "texture_sha256": sha,
                "label_status": "unknown",
                "example_mod_id": example[0],
                "example_file": example[1],
                "occurrence_count": self.texture_occurrence_counts[sha],
                "dds_format": row.get("dds_format"),
                "dds_width": row.get("dds_width"),
                "dds_height": row.get("dds_height"),
                "baseline_texture_class": row.get("baseline_texture_class"),
                "baseline_role": row.get("baseline_role"),
                "baseline_confidence": row.get("baseline_confidence"),
            })
        _write_csv(self.output_dir / "unknown_candidates.csv", unknown,
                   UNKNOWN_FIELDS)
        _write_csv(self.output_dir / "manual_labels.csv", self.manual_labels,
                   MANUAL_FIELDS)
        _write_csv(self.output_dir / "component_labels.csv",
                   sorted(self.component_labels,
                          key=lambda row: tuple(row.get(key) or ""
                                                for key in COMPONENT_LABEL_FIELDS)),
                   COMPONENT_LABEL_FIELDS)

        label_counts = Counter(row["label"] for row in self.trusted_labels)
        source_counts = Counter(row["label_source"] for row in self.trusted_labels)
        format_counts = Counter(
            str(row.get("dds_format") or "invalid")
            for row in self.textures.values())
        parse_counts = Counter(row["parse_status"] for row in self.mods)
        game_counts = Counter(row["game"] for row in self.mods)
        pixel_decoded_total = sum(
            row.get("decode_status") == "decoded"
            for row in self.textures.values())
        pixel_skipped_total = sum(
            row.get("decode_status") == "skipped_budget"
            for row in self.textures.values())
        summary = {
            "schema_version": SCHEMA_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "mods_discovered": len(self.mods),
            "mods_total_discovered": self._mods_total_discovered,
            "mods_scan_limit": self.max_mods,
            "scan_truncated": (self.max_mods is not None
                                and self.max_mods < self._mods_total_discovered),
            "mods_in_dataset": sum(
                1 for row in self.mods
                if not self.wuwa_only or row["game"] == "wuwa"),
            "mods_by_game": dict(sorted(game_counts.items())),
            "parse_status": dict(sorted(parse_counts.items())),
            "textures_unique": len(self.textures),
            "texture_occurrences": len(self.occurrences),
            "trusted_label_rows": len(self.trusted_labels),
            "trusted_labels_by_label": dict(sorted(label_counts.items())),
            "trusted_labels_by_source": dict(sorted(source_counts.items())),
            "unknown_candidates": len(unknown),
            "manual_label_rows": len(self.manual_labels),
            "manual_label_errors": len(self.manual_label_errors),
            "component_label_rows": len(self.component_labels),
            "format_distribution": dict(sorted(format_counts.items())),
            "archives_found": len(archive_paths),
            "archives_skipped": len(archive_paths),
            "feature_extractions": self.feature_extractions,
            "feature_cache_hits": self.feature_cache_hits,
            "pixel_decoded": pixel_decoded_total,
            "pixel_decoded_this_run": self.pixel_decoded,
            "pixel_skipped_budget": pixel_skipped_total,
            "pixel_skipped_budget_this_run": self.pixel_skipped_budget,
            "pixel_decode_limit": self.pixel_limit,
            "errors": len(self.errors),
            "error_details": self.errors[:100],
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "scanner_version": SCANNER_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "input_root_name": Path(self.mods_root).name,
            "wuwa_only": self.wuwa_only,
            "pixel_decode_limit": self.pixel_limit,
            "mods_scan_limit": self.max_mods,
            "files": [
                "manifest.json", "summary.json", "mods.csv", "textures.csv",
                "occurrences.csv", "trusted_labels.csv",
                "unknown_candidates.csv", "manual_labels.csv",
                "component_labels.csv", "feature_cache.json",
            ],
            "label_policy": {
                "primary_sources": sorted(_PRIMARY_SOURCES),
                "secondary_sources": sorted(_SECONDARY_SOURCES),
                "unknown_is_not_negative": True,
                "excluded_as_labels": [
                    "current_dds_classifier", "Components_filename",
                    "filename_t_tag", "original_texture_hash",
                    "WWMI_TextureUsage", "Asset_JSON", "material_guess",
                    "runtime_fallback",
                ],
            },
            "model_feature_columns": model_feature_columns(
                {key for row in self.textures.values() for key in row}),
            "diagnostic_feature_columns": sorted({
                key for row in self.textures.values() for key in row
                if key.startswith("baseline_")
            }),
        }
        _write_json(self.output_dir / "manifest.json", manifest)
        _write_json(self.output_dir / "summary.json", summary)
        _write_json(self.output_dir / "feature_cache.json", {
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "entries": {
                sha: {key: value for key, value in row.items()
                      if key not in {"example_mod_id", "example_relative_file"}}
                for sha, row in sorted(self.textures.items())
            },
        })
        self._write_report(summary, unknown)
        return summary

    def _write_report(self, summary, unknown):
        reports = self.output_dir / "reports"
        _write_json(reports / "scan_report.json", summary)
        rows = "".join(
            f"<tr><td>{html.escape(str(row.get('label') or ''))}</td>"
            f"<td>{html.escape(str(row.get('count') or ''))}</td></tr>"
            for row in [
                {"label": key, "count": value}
                for key, value in summary["trusted_labels_by_label"].items()
            ])
        page = """<!doctype html><meta charset='utf-8'>
<title>WuWa texture corpus report</title>
<h1>WuWa texture corpus</h1>
<p>Scanner-only checkpoint. Unknown candidates are not negative labels.</p>
<table><tr><th>Metric</th><th>Value</th></tr>
{metrics}</table>
<h2>Trusted labels</h2><table><tr><th>Label</th><th>Rows</th></tr>
{labels}</table>
""".format(
            metrics="".join(
                f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
                for key, value in summary.items()
                if key not in {"error_details", "trusted_labels_by_label",
                               "trusted_labels_by_source"}),
            labels=rows,
        )
        (reports / "summary.html").write_text(page, encoding="utf-8")


def scan_corpus(mods_root, output_dir, *, wuwa_only=True, progress=False,
                pixel_limit=None, max_mods=None):
    return CorpusBuilder(
        mods_root, output_dir, wuwa_only=wuwa_only, progress=progress,
        pixel_limit=pixel_limit, max_mods=max_mods).scan()


def read_summary(corpus_dir):
    path = Path(corpus_dir) / "summary.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_review_path(mods_root, mod_id, relative_file):
    root = Path(mods_root).resolve()
    candidate = (root / Path(mod_id) / Path(relative_file)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def review_corpus(corpus_dir, mods_root, *, limit=12, offset=0):
    """Create deterministic contact sheets for a small unknown sample."""
    corpus_dir = Path(corpus_dir)
    offset = max(0, int(offset))
    review_dir = corpus_dir / "review"
    if offset:
        review_dir = review_dir / f"batch-{offset:04d}"
    review_dir.mkdir(parents=True, exist_ok=True)
    with (corpus_dir / "unknown_candidates.csv").open(
            encoding="utf-8", newline="") as stream:
        unknown = list(csv.DictReader(stream))
    selected = unknown[offset:offset + max(0, int(limit))]
    items = []
    for row in selected:
        path = _safe_review_path(mods_root, row.get("example_mod_id", ""),
                                 row.get("example_file", ""))
        image = load_texture_image(path, max_size=160, preserve_alpha=True) \
            if path and path.is_file() else None
        if image is None:
            continue
        items.append((row, image.convert("RGB")))

    sheets = []
    if items:
        from PIL import Image, ImageDraw

        columns = 4
        cell_width, cell_height = 220, 200
        for start in range(0, len(items), columns * 3):
            chunk = items[start:start + columns * 3]
            sheet = Image.new("RGB", (columns * cell_width, 3 * cell_height),
                              "white")
            draw = ImageDraw.Draw(sheet)
            for index, (row, image) in enumerate(chunk):
                x = (index % columns) * cell_width
                y = (index // columns) * cell_height
                image.thumbnail((200, 160))
                sheet.paste(image, (x + 10, y + 8))
                draw.text((x + 10, y + 172), row["texture_sha256"][:12],
                          fill="black")
            name = f"contact-sheet-{len(sheets) + 1:02d}.png"
            sheet.save(review_dir / name)
            sheets.append(name)

    _write_csv(review_dir / "items.csv", [
        {"texture_sha256": row["texture_sha256"],
         "example_mod_id": row.get("example_mod_id"),
         "example_file": row.get("example_file"),
         "image_available": bool(_safe_review_path(
             mods_root, row.get("example_mod_id", ""), row.get("example_file", ""))) }
        for row in selected
    ], ["texture_sha256", "example_mod_id", "example_file", "image_available"])
    html_rows = "".join(
        f"<li>{html.escape(name)}</li>" for name in sheets)
    (review_dir / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Unknown review</title>"
        "<h1>Unknown candidates</h1><ul>" + html_rows + "</ul>",
        encoding="utf-8")
    return {
        "offset": offset,
        "selected": len(selected),
        "decoded": len(items),
        "sheets": sheets,
        "review_dir": str(review_dir),
    }


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="scan an extracted mod root")
    scan.add_argument("mods_root", type=Path)
    scan.add_argument("--output", required=True, type=Path)
    scan.add_argument("--include-non-wuwa", action="store_true")
    scan.add_argument(
        "--pixel-limit", type=int, default=None,
        help="decode at most this many unique DDS previews; headers still scan fully")
    scan.add_argument(
        "--max-mods", type=int, default=None,
        help="scan only the first N discovered mod directories")
    summary = commands.add_parser("summary", help="print corpus summary")
    summary.add_argument("corpus", type=Path)
    review = commands.add_parser("review", help="make unknown contact sheets")
    review.add_argument("corpus", type=Path)
    review.add_argument("--mods-root", required=True, type=Path)
    review.add_argument("--limit", default=12, type=int)
    review.add_argument("--offset", default=0, type=int)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    if args.command == "scan":
        result = scan_corpus(
            args.mods_root, args.output, wuwa_only=not args.include_non_wuwa,
            progress=True, pixel_limit=args.pixel_limit,
            max_mods=args.max_mods)
    elif args.command == "summary":
        result = read_summary(args.corpus)
    else:
        result = review_corpus(
            args.corpus, args.mods_root, limit=args.limit, offset=args.offset)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
