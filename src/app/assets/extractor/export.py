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


@dataclass
class ExportOptions:
    layout: str = "compact"
    debug_assets: bool = False


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


def _texture_plan(meshes, name, options):
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
                refs[(c.id, i, t["file"])] = (t, ext, selected)
    payload_hashes = {}
    for t, ext, selected in refs.values():
        if selected and len(hash_contents[(t["hash"], ext)]) == 1:
            payload_hashes.setdefault((t["sha256"], ext), set()).add(t["hash"])
    result = {}
    for mesh in meshes:
        c = mesh.component
        for i, part in enumerate(c.parts):
            label = _part_label(i)
            for t in part.textures:
                key = c.id, i, t["file"]
                if key not in refs:
                    continue
                _, ext, selected = refs[key]
                collision = len(hash_contents[(t["hash"], ext)]) > 1
                if options.layout == "compact" and selected and not collision:
                    hashes = sorted(payload_hashes[(t["sha256"], ext)])
                    # Bound each Windows filename component while retaining every hash lookup.
                    begin = hashes.index(t["hash"]) // 24 * 24
                    filename = "Texture-" + "-".join(hashes[begin:begin + 24]) + ext
                else:
                    filename = name + c.name + label + t["role"] + ext
                    if not selected:
                        filename = "assets/" + filename
                result[key] = filename
    return result, omitted


def _part_label(index):
    return chr(65 + index) if index < 26 else f"Part{index + 1:03d}"


def _component_folders(meshes):
    """Group proven facial materials with successfully reconstructed expressions."""
    folders = {}
    for mesh in meshes:
        if mesh.morph_status != "reversed_recognized" or not mesh.component.parts:
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
    selection = selection or Selection()
    name = safe_name(selection.name)
    inv = Inventory(analysis.dump, ctx)
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
    meshes, diagnostics = [], list(analysis.diagnostics)
    for c in analysis.components:
        if c.status == "included" and c.id not in {x.id for x in supported} and not c.default_included:
            diagnostics.extend(c.diagnostics)
    used_names = set()
    for i, c in enumerate(supported):
        ctx.tick("Reconstructing " + c.name, i, len(supported))
        # Do not mutate the review state when renaming a package.
        import copy
        c = copy.deepcopy(c)
        c.name = safe_name((selection.renames or {}).get(c.id, c.name))
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
        meshes.append(mesh)
        diagnostics.extend(c.diagnostics)
    unresolved = [c for c in analysis.components if c.status in ("uncertain", "unsupported")]
    partial = bool(unresolved)
    if partial:
        diagnostics.append(f"Partial capture coverage: {len(unresolved)} unresolved/unsupported geometry candidates are not exported. Static attachments may be missing.")
    return ModelPackage(analysis, name, meshes, diagnostics, partial, time.perf_counter() - started)


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
    options = options or ExportOptions()
    if options.layout not in ("compact", "compatible"):
        raise ValueError("Export layout must be compact or compatible")
    if safe_name(package.name) != package.name:
        raise ValueError("Model name must not have leading or trailing spaces")
    started = time.perf_counter()
    output = Path(output).resolve()
    dump = Path(package.analysis.dump).resolve()
    if output == dump or output.is_relative_to(dump) or dump.is_relative_to(output):
        raise ValueError("Output must be outside the input capture")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / package.name
    report_path = output / (package.name + ".extraction_report.json")
    if destination.exists() or report_path.exists():
        existing = destination if destination.exists() else report_path
        raise FileExistsError(f"Output already exists: {existing}. Choose a new name; existing results are preserved.")
    inv = Inventory(dump, ctx)
    # Restore only recorded, capture-relative dedup paths; no log rescan required.
    inv.targets.update(package.analysis.metrics.get("dedup_targets", {}))
    texture_plan, omitted = _texture_plan(package.meshes, package.name, options)
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
    texture_plan = {
        key: "Face/" + filename if texture_folders[texture_identities[key]] == {"Face"} else filename
        for key, filename in texture_plan.items()
    }
    for mesh in package.meshes:
        safe_name(mesh.component.name)
        for h in (mesh.component.ib, mesh.component.streams["position"]["hash"]):
            if not re.fullmatch(r"[0-9a-f]{8}", str(h)):
                raise Unsupported("Invalid geometry identity in analysis")
    stage = Path(tempfile.mkdtemp(prefix=".xxmi-stage-", dir=output))
    metadata, sources, part_count = {"": []}, [], 0
    payloads = PayloadFiles(stage)
    textures, texture_names, checked_sources = set(), set(), {}
    associations = 0
    committed_report_digest = None
    try:
        for mesh in package.meshes:
            c = mesh.component
            ctx.tick("Exporting " + c.name)
            folder = component_folders.get(c.id, "")
            prefix_folder = folder + "/" if folder else ""
            mesh_directory = stage / folder
            mesh_directory.mkdir(exist_ok=True)
            classes = [_part_label(i) for i in range(len(c.parts))]
            meta = dict(component_name=c.name, root_vs=c.provenance["root_vs"], draw_vb=c.provenance["draw_vb"],
                        position_vb=c.streams["position"]["hash"], blend_vb=c.streams["blend"]["hash"],
                        texcoord_vb=c.streams["texcoord"]["hash"], ib=c.ib,
                        object_indexes=[p.first for p in c.parts], object_index_counts=[p.count for p in c.parts],
                        object_classifications=classes, texture_hashes=[], ib_files=[])
            main_vb = None
            for part_index, (part, label, indices) in enumerate(zip(c.parts, classes, mesh.indices)):
                ctx.tick("Writing part " + c.name + label)
                prefix = package.name + c.name + label
                vbprefix = package.name + c.name if options.layout == "compact" else prefix
                vbname = f"{prefix_folder}{vbprefix}-vb0={meta['position_vb']}.txt"
                if main_vb and main_vb != stage / vbname:
                    payloads.alias(main_vb, stage / vbname)
                elif main_vb:
                    pass
                else:
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
                meta["texture_hashes"].append(texmeta)
                sources.append(dict(component=c.id, part=label, metadata=prefix_folder + "hash.json",
                                    vb=vbname, ib=ibname, original_range=asdict(part), textures=mappings))
            metadata.setdefault(folder, []).append(meta)
            if options.debug_assets and mesh.morph_status != "not_detected":
                (mesh_directory / "source_geometry").mkdir(exist_ok=True)
                (mesh_directory / "source_geometry" / (c.name + "-captured-position.buf")).write_bytes(mesh.source_position)
        for folder, components in metadata.items():
            (stage / folder / "hash.json").write_text(json.dumps(
                {"format": "mod-viewer-compact", "components": components}, indent=2), encoding="utf-8")
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
                              hardlink_aliases=payloads.hardlinks, copy_fallbacks=payloads.fallbacks,
                              logical_bytes=sum(p.stat().st_size for p in files), stored_bytes=sum(unique_files.values()),
                              omitted_texture_associations=len(omitted), report_path=str(report_path), layout=options.layout)
        details = dict(schema=1, app_version=__version__, report=asdict(report), analysis=package.analysis.to_dict(),
                       components=[asdict(m.component) for m in package.meshes], outputs=sources,
                       extraction_seconds=package.extraction_seconds,
                       omitted_assets=omitted, options=asdict(options),
                       geometry_notes="Component-local bone IDs and vertex order retained. No welding, original skeleton or animations recovered. Base vertex applied in exported IB text; original values recorded.",
                       storage_notes="Shared textures use native resource hashes. Hash collisions retain per-part aliases. Hardlinks only join output files; unsupported filesystems use copies. Editing a shared file changes its linked aliases.")
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
