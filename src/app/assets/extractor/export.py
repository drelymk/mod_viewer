from dataclasses import dataclass, asdict
from pathlib import Path
import json
import os
import re
import shutil
import tempfile
import time
from .core import Analysis, Context, Unsupported
from .capture import Inventory
from .geometry import decode_component, codec
from .textures import digest
from . import __version__
from ..package_format import FORMAT, WWMI_METADATA


def safe_name(name):
    name = name.strip()
    if not name or name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise ValueError("Use a model/component name without Windows reserved characters")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("This name is reserved by Windows")
    return name


@dataclass
class Selection:
    name: str = "Model_001"
    ids: list[str] | None = None
    renames: dict | None = None


@dataclass
class ModelPackage:
    analysis: Analysis
    name: str
    meshes: list
    diagnostics: list
    partial: bool
    extraction_seconds: float
    skipped: int = 0
    unproven_candidates: int = 0


@dataclass
class ExportReport:
    output: str
    partial: bool
    components: int
    parts: int
    textures: int
    seconds: float
    diagnostics: list
    texture_associations: int = 0
    texture_files: int = 0
    files: int = 0
    hardlink_aliases: int = 0
    copy_fallbacks: int = 0
    logical_bytes: int = 0
    stored_bytes: int = 0
    omitted_texture_associations: int = 0
    report_path: str = ""
    layout: str = "compact"
    skipped: int = 0
    unproven_candidates: int = 0


@dataclass
class ExportOptions:
    layout: str = "compact"
    debug_assets: bool = False
    asset_type: str | None = None


class PayloadFiles:
    """Share bytes only between newly created output files, never with inputs."""
    def __init__(self, stage):
        self.stage = stage
        self.by_content = {}
        self.hardlinks = self.fallbacks = 0

    def alias(self, source, destination):
        try:
            os.link(source, destination)
            self.hardlinks += 1
        except OSError:
            shutil.copyfile(source, destination)
            self.fallbacks += 1

    def texture(self, source, name, identity):
        destination = self.stage / name
        if destination.exists():
            if digest(destination) != identity:
                raise Unsupported(f"Conflicting output texture name: {name}")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        if identity in self.by_content:
            self.alias(self.by_content[identity], destination)
        else:
            if hasattr(source, "source"):
                with source.open("rb") as incoming, destination.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            else:
                shutil.copyfile(source, destination)
            self.by_content[identity] = destination

    def generated(self, path):
        identity = digest(path)
        previous = self.by_content.get(identity)
        if previous and previous != path:
            path.unlink()
            self.alias(previous, path)
        else:
            self.by_content[identity] = path


def _texture_plan(meshes, name, options, *, wwmi=False):
    """Keep importer role/hash schema intact while sharing unambiguous payloads."""
    refs, omitted, hash_contents = {}, [], {}
    for mesh in meshes:
        c = mesh.component
        for i, part in enumerate(c.parts):
            for t in part.textures:
                selected = not t.get("auxiliary")
                if not selected and not options.debug_assets:
                    omitted.append(dict(component=c.id, part=i, file=t["file"], role=t["role"],
                                        hash=t["hash"], sha256=t["sha256"], reason=t.get("role_note", "Not a supported viewer material binding")))
                    continue
                ext = Path(t["file"]).suffix.lower()
                if not re.fullmatch(r"[0-9a-f]{8}", str(t["hash"])) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", t["role"]):
                    raise Unsupported("Invalid texture identity or role in analysis")
                if ext not in (".dds", ".png", ".jpg", ".jpeg"):
                    raise Unsupported("Unsupported texture extension in analysis")
                hash_contents.setdefault((t["hash"], ext), set()).add(t["sha256"])
                refs[(c.id, i, t["file"])] = (ext, selected)
    if wwmi:
        from .wwmi_export import texture_plan
        return texture_plan(meshes, refs), omitted
    result, shared = {}, {}
    for mesh in meshes:
        c = mesh.component
        for i, part in enumerate(c.parts):
            label = _part_label(i)
            for t in part.textures:
                key = c.id, i, t["file"]
                if key not in refs:
                    continue
                ext, selected = refs[key]
                collision = len(hash_contents[(t["hash"], ext)]) > 1
                if options.layout == "compact":
                    suffix = "-" + t["sha256"][:12] if collision else ""
                    filename = name + "-" + c.name + "-" + t["role"] + "-" + t["hash"] + suffix + ext
                    if not selected:
                        filename = "assets/" + filename
                    else:
                        # Keep distinct native hashes and payload versions addressable.
                        identity = t["hash"], t["sha256"], ext
                        filename = shared.setdefault(identity, filename)
                else:
                    filename = name + c.name + label + t["role"] + ext
                    if not selected:
                        filename = "assets/" + filename
                result[key] = filename
    return result, omitted


def _part_label(index):
    return chr(65 + index) if index < 26 else f"Part{index + 1:03d}"


def _component_folders(meshes):
    """Group facial families proven by game-specific material/topology evidence."""
    folders = {}
    for mesh in meshes:
        if mesh.component.provenance.get("gimi_face_family"):
            folders[mesh.component.id] = "Face"
            continue
        if mesh.morph_status not in ("not_detected", "reversed_recognized") or not mesh.component.parts:
            continue
        if all(
            any(t.get("role_confidence") == "unsupported_face_normal_encoding"
                for t in part.textures)
            and any(t.get("role") == "Diffuse" and not t.get("auxiliary")
                    for t in part.textures)
            for part in mesh.component.parts
        ):
            folders[mesh.component.id] = "Face"
    # A standalone facial selection must keep a usable root metadata document.
    return folders if len(folders) < len(meshes) else {}


def extract(analysis, selection=None, context=None):
    ctx = context or Context()
    ctx.budget = getattr(analysis, "_budget", ctx.budget)
    selection = selection or Selection()
    name = safe_name(selection.name)
    inv = Inventory(getattr(analysis, "_capture_source", None) or analysis.dump, ctx)
    inv.targets.update(analysis.metrics.get("dedup_targets", {}))
    started = time.perf_counter()
    supported = [c for c in analysis.components if c.status == "included"]
    if selection.ids is not None:
        available = {c.id for c in supported}
        if not set(selection.ids) <= available:
            raise Unsupported("Selection contains unsupported or unknown components")
        supported = [c for c in supported if c.id in selection.ids]
    else:
        supported = [c for c in supported if c.default_included]
    if not supported:
        raise Unsupported("No supported components selected; no empty package was written")
    selected_ids = {c.id for c in supported}
    meshes, diagnostics = [], list(analysis.diagnostics)
    for c in analysis.components:
        if c.status == "included" and c.id not in selected_ids and not c.default_included:
            diagnostics.extend(c.diagnostics)
    used_names = set()
    for i, c in enumerate(supported):
        ctx.tick("Reconstructing " + c.name, i, len(supported))
        # Do not mutate the review state when renaming a package.
        import copy
        c = copy.deepcopy(c)
        attribute_skinning = c.provenance.get("geometry_kind") == "attribute_skinning"
        default_name = "Object_" + c.streams["position"]["hash"] if attribute_skinning else c.name
        c.name = safe_name((selection.renames or {}).get(c.id, c.provenance.get("suggested_name", default_name)))
        if c.name.casefold() in used_names:
            raise ValueError("Component names must be unique")
        used_names.add(c.name.casefold())
        # Detect a changed capture rather than silently exporting stale analysis.
        for source in c.streams.values():
            expected = source.get("sha256")
            if expected and digest(inv.path(source["file"])) != expected:
                raise Unsupported("Capture changed since analysis; analyze it again")
        for part in c.parts:
            if part.sha256 and digest(inv.path(part.ib_file.replace(".txt", ".buf"))) != part.sha256:
                raise Unsupported("Index buffer changed since analysis; analyze it again")
        mesh = decode_component(inv, c, ctx)
        if attribute_skinning:
            from .profiles.wwmi import split_components
            for child in split_components(mesh):
                if child.component.name.casefold() in used_names:
                    raise ValueError("Component names must be unique")
                used_names.add(child.component.name.casefold())
                meshes.append(child)
        else:
            meshes.append(mesh)
        diagnostics.extend(c.diagnostics)
    unresolved = [c for c in analysis.components if c.status in ("uncertain", "unsupported")]
    unproven = sum(c.unproven_direct and c.id not in selected_ids for c in analysis.components)
    partial = bool(unresolved)
    if partial:
        diagnostics.append(f"Partial capture coverage: {len(unresolved)} unresolved or unsupported geometry candidates are not exported. Static attachments may be missing.")
    return ModelPackage(analysis, name, meshes, list(dict.fromkeys(diagnostics)), partial, time.perf_counter() - started,
                        skipped=len(unresolved), unproven_candidates=unproven)


def write_vb(path, mesh, context):
    stride = sum(codec(e.format)[0] for e in mesh.elements)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"stride: {stride}\nfirst vertex: 0\nvertex count: {len(mesh.vertices)}\ntopology: trianglelist\n")
        for i, e in enumerate(mesh.elements):
            f.write(f"element[{i}]:\n  SemanticName: {e.semantic}\n  SemanticIndex: {e.index}\n  Format: {e.format}\n  InputSlot: 0\n  AlignedByteOffset: {e.offset}\n  InputSlotClass: per-vertex\n  InstanceDataStepRate: 0\n")
        f.write("\nvertex-data:\n\n")
        for i, row in enumerate(mesh.vertices):
            if i % 512 == 0:
                context.tick("Writing " + mesh.component.name, i, len(mesh.vertices))
            for e, values in zip(mesh.elements, row):
                value = ", ".join(str(v) if isinstance(v, int) else format(v, ".9g") for v in values)
                f.write(f"vb0[{i}]+{e.offset:03d} {e.label}: {value}\n")
            f.write("\n")


def write_ib(path, part, indices):
    # The importer consumes absolute indices. Keep originals in the report and apply base once.
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"byte offset: 0\nfirst index: {part.first}\nindex count: {part.count}\ntopology: trianglelist\nformat: DXGI_FORMAT_R32_UINT\n\n")
        for i in range(0, len(indices), 3):
            f.write(" ".join(str(v + part.base_vertex) for v in indices[i:i + 3]) + "\n")


def export(package, output, context=None, *, options=None):
    ctx = context or Context()
    ctx.budget = getattr(package.analysis, "_budget", ctx.budget)
    options = options or ExportOptions()
    if options.layout not in ("compact", "compatible"):
        raise ValueError("Export layout must be compact or compatible")
    if safe_name(package.name) != package.name:
        raise ValueError("Model name must not have leading or trailing spaces")
    started = time.perf_counter()
    output = Path(output).resolve()
    dump = Path(package.analysis.dump).resolve()
    source = getattr(package.analysis, "_capture_source", None)
    if source is None and (output == dump or output.is_relative_to(dump) or dump.is_relative_to(output)):
        raise ValueError("Output must be outside the input capture")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / package.name
    report_path = output / (package.name + ".extraction_report.json")
    if destination.exists() or report_path.exists():
        existing = destination if destination.exists() else report_path
        raise FileExistsError(f"Output already exists: {existing}. Choose a new name; existing results are preserved.")
    inv = Inventory(source or dump, ctx)
    # Restore only recorded, capture-relative dedup paths; no log rescan required.
    inv.targets.update(package.analysis.metrics.get("dedup_targets", {}))
    wwmi = options.asset_type == "WWMI" or (
        options.asset_type is None and any(
            m.component.provenance.get("geometry_kind") == "attribute_skinning"
            for m in package.meshes))
    metadata_name = WWMI_METADATA if wwmi else "hash.json"
    texture_plan, omitted = _texture_plan(package.meshes, package.name, options, wwmi=wwmi)
    if wwmi:
        component_folders = {m.component.id: m.component.streams["position"]["hash"] for m in package.meshes}
    else:
        component_folders = _component_folders(package.meshes)
        texture_folders = {}
        texture_identities = {}
        for mesh in package.meshes:
            c = mesh.component
            for part_index, part in enumerate(c.parts):
                for texture in part.textures:
                    key = c.id, part_index, texture["file"]
                    if key not in texture_plan:
                        continue
                    identity = texture["sha256"]
                    texture_identities[key] = identity
                    texture_folders.setdefault(identity, set()).add(component_folders.get(c.id, ""))
        for key, filename in list(texture_plan.items()):
            folders = texture_folders[texture_identities[key]]
            folder = next(iter(folders)) if len(folders) == 1 else ""
            if folder:
                texture_plan[key] = folder + "/" + filename
    for mesh in package.meshes:
        safe_name(mesh.component.name)
        for h in (mesh.component.ib, mesh.component.streams["position"]["hash"]):
            if not re.fullmatch(r"[0-9a-f]{8}", str(h)):
                raise Unsupported("Invalid geometry identity in analysis")
    stage = Path(tempfile.mkdtemp(prefix=".xxmi-stage-", dir=output))
    metadata, sources, part_count = {"": []}, [], 0
    wwmi_usage = {}
    wwmi_objects = {}
    payload_groups = {}
    textures, texture_names, checked_sources = set(), set(), {}
    associations = 0
    committed_report_digest = None
    try:
        for mesh in package.meshes:
            c = mesh.component
            ctx.tick("Exporting " + c.name)
            folder = component_folders.get(c.id, "")
            group = folder if wwmi else ""
            if group not in payload_groups:
                payload_groups[group] = PayloadFiles(stage)
            payloads = payload_groups[group]
            prefix_folder = folder + "/" if folder else ""
            mesh_directory = stage / folder
            mesh_directory.mkdir(exist_ok=True)
            classes = [_part_label(i) for i in range(len(c.parts))]
            if not wwmi:
                meta = dict(component_name=c.name, root_vs=c.provenance["root_vs"], draw_vb=c.provenance["draw_vb"],
                            position_vb=c.streams["position"]["hash"], blend_vb=c.streams.get("blend", {}).get("hash", ""),
                            texcoord_vb=c.streams["texcoord"]["hash"], ib=c.ib,
                            object_indexes=[p.first for p in c.parts], object_index_counts=[p.count for p in c.parts],
                            object_classifications=classes, texture_hashes=[], ib_files=[])
            main_vb = None
            for part_index, (part, label, indices) in enumerate(zip(c.parts, classes, mesh.indices)):
                ctx.tick("Writing part " + c.name + label)
                if wwmi:
                    from .wwmi_export import write_component, texture_usage
                    ordinal = len(metadata.setdefault(folder, []))
                    native_files, vertex_count = write_component(mesh_directory, ordinal, mesh, part, indices, ctx)
                    vbname, ibname = (prefix_folder + native_files[kind] for kind in ("vb", "ib"))
                    native = dict(component_name=c.name, classification=label, index_offset=part.first,
                                  index_count=part.count, vertex_count=vertex_count, ib_hash=c.ib)
                    metadata[folder].append(native)
                    wwmi_objects.setdefault(folder, dict(vb0_hash=c.streams["position"]["hash"],
                                                        vertex_count=c.vertex_count, index_count=0))["index_count"] += part.count
                    wwmi_usage.setdefault(folder, {})[f"Component {ordinal}"] = texture_usage(c, part)
                else:
                    prefix = package.name + c.name + label
                    vbprefix = package.name + c.name if options.layout == "compact" else prefix
                    vbname = f"{prefix_folder}{vbprefix}-vb0={meta['position_vb']}.txt"
                    if main_vb and main_vb != stage / vbname:
                        payloads.alias(main_vb, stage / vbname)
                    elif not main_vb:
                        write_vb(stage / vbname, mesh, ctx)
                        payloads.generated(stage / vbname)
                        main_vb = stage / vbname
                    meta.setdefault("vb_file", os.path.basename(vbname))
                    ibname = f"{prefix_folder}{prefix}-ib={c.ib}.txt"
                    write_ib(stage / ibname, part, indices)
                    payloads.generated(stage / ibname)
                    meta["ib_files"].append(os.path.basename(ibname))
                part_count += 1
                texmeta, mappings = [], []
                for texture in part.textures:
                    key = c.id, part_index, texture["file"]
                    if key not in texture_plan:
                        continue
                    ctx.tick("Copying available textures")
                    try:
                        source = inv.path(texture["file"])
                    except FileNotFoundError:
                        package.diagnostics.append(f"Texture missing since analysis: {texture['file']}")
                        continue
                    identity = checked_sources.get(texture["file"])
                    if identity is None:
                        identity = checked_sources[texture["file"]] = digest(source)
                    if identity != texture["sha256"]:
                        raise Unsupported("Texture changed since analysis; analyze again")
                    texname = texture_plan[key]
                    payloads.texture(source, texname, identity)
                    if not texture.get("auxiliary"):
                        texmeta.append([texture["role"], source.suffix, texture["hash"],
                                        os.path.relpath(stage / texname, mesh_directory).replace(os.sep, "/")])
                    mappings.append(dict(output=texname, **texture))
                    associations += 1
                    textures.add(identity)
                    texture_names.add(texname)
                if wwmi:
                    native["texture_hashes"] = [texmeta]
                else:
                    meta["texture_hashes"].append(texmeta)
                sources.append(dict(component=c.id, part=label, metadata=prefix_folder + metadata_name,
                                    vb=vbname, ib=ibname, original_range=asdict(part), textures=mappings))
            if not wwmi:
                metadata.setdefault(folder, []).append(meta)
            if options.debug_assets and mesh.morph_status != "not_detected":
                (mesh_directory / "source_geometry").mkdir(exist_ok=True)
                (mesh_directory / "source_geometry" / (c.name + "-captured-position.buf")).write_bytes(mesh.source_position)
        for folder, components in metadata.items():
            if not components:
                continue
            document = {"format": FORMAT, "components": components}
            if wwmi:
                document.update(wwmi_objects[folder])
                (stage / folder / "TextureUsage.json").write_text(json.dumps(
                    wwmi_usage[folder], indent=2), encoding="utf-8")
            (stage / folder / metadata_name).write_text(json.dumps(
                document, indent=2), encoding="utf-8")
        if options.debug_assets:
            (stage / "diagnostic.log").write_text("\n".join(package.diagnostics), encoding="utf-8")
        files = [p for p in stage.rglob("*") if p.is_file()]
        unique_files = {}
        for p in files:
            stat = p.stat()
            key = (stat.st_dev, stat.st_ino) if stat.st_ino else str(p)
            unique_files[key] = stat.st_size
        report = ExportReport(str(destination), package.partial, len(package.meshes), part_count, len(textures),
                              time.perf_counter() - started, package.diagnostics,
                              texture_associations=associations, texture_files=len(texture_names), files=len(files),
                              hardlink_aliases=sum(p.hardlinks for p in payload_groups.values()),
                              copy_fallbacks=sum(p.fallbacks for p in payload_groups.values()),
                              logical_bytes=sum(p.stat().st_size for p in files), stored_bytes=sum(unique_files.values()),
                              omitted_texture_associations=len(omitted), report_path=str(report_path), layout=options.layout,
                              skipped=package.skipped, unproven_candidates=package.unproven_candidates)
        details = dict(schema=1, app_version=__version__, report=asdict(report), analysis=package.analysis.to_dict(),
                       components=[asdict(m.component) for m in package.meshes], outputs=sources,
                       extraction_seconds=package.extraction_seconds,
                       omitted_assets=omitted, options=asdict(options),
                       geometry_notes="Component-local bone IDs and vertex order retained. No welding, original skeleton or animations recovered. Base vertex applied in exported IB text; original values recorded. Split ATTRIBUTE captures use the viewer's WWMI winding convention without changing source buffers.",
                       storage_notes="Shared textures use native resource hashes. Hash collisions retain per-part aliases. Hardlinks only join output files; unsupported filesystems use copies. Editing a shared file changes its linked aliases.")
        details["report"].update(output=package.name, report_path=report_path.name)
        details["analysis"].pop("dump")
        # Diagnostics may include OS errors containing source/output paths.
        def portable(value):
            if isinstance(value, dict):
                return {k: portable(v) for k, v in value.items()}
            if isinstance(value, list):
                return [portable(v) for v in value]
            if isinstance(value, str):
                for root, label in ((package.analysis.dump, "<capture>"), (str(output), "<output>")):
                    for spelling in {root, root.replace("\\", "/"), root.replace("/", "\\"), root.replace("\\", "\\\\")}:
                        value = value.replace(spelling, label)
            return value
        details = portable(details)
        staged_report = stage / ".extraction-report.json"
        staged_report.write_text(json.dumps(details, indent=2), encoding="utf-8")
        ctx.tick("Committing export")
        # On Windows rename refuses an existing directory; no user content is replaced.
        staged_report.rename(report_path)
        committed_report_digest = digest(report_path)
        stage.rename(destination)
        return report
    except BaseException:
        # Delete only our verified, newly created staging directory.
        if stage.parent == output and stage.name.startswith(".xxmi-stage-"):
            shutil.rmtree(stage)
        if committed_report_digest and report_path.exists() and digest(report_path) == committed_report_digest:
            report_path.unlink()
        raise
