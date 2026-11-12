"""Build a detached, verified rig/animation package before touching an Asset."""

import collections
import hashlib
import json
from pathlib import Path
import re
import shutil

from app.assets.animation_conversion import convert_clip, merge_reference_layer
from app.assets.animations import read_clip
from app.assets.rig import validate_rig
from .native import capture, make_rig, match_components, read_mesh
from .runtime import ExtractionCancelled, check_cancelled


def document(file, limit=64 * 1024 * 1024):
    file = Path(file)
    if file.stat().st_size > limit:
        raise ValueError("Game metadata exceeds its size limit.")
    return json.loads(file.read_text(encoding="utf-8-sig"))


def write_document(file, value, limit):
    data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    if len(data) > limit:
        raise ValueError("Generated rig/animation metadata exceeds the viewer's size limit.")
    Path(file).write_bytes(data)


def package_name(value):
    if not isinstance(value, str):
        return ""
    value = value.split("'", 1)[-1].rstrip("'").split(".", 1)[0]
    if value.startswith("/Game/"):
        value = "Client/Content/" + value[6:]
    return value if value.startswith("Client/Content/") and re.fullmatch(r"[A-Za-z0-9_/-]{1,1024}", value) else ""


def exported_file(item, directory, extension):
    if not item or item.get("state") != "completed" or item.get("failures"):
        return None
    files = [Path(file).resolve() for file in item.get("rootFiles", []) if Path(file).suffix.lower() == extension]
    if len(files) != 1 or not files[0].is_relative_to(Path(directory).resolve()) or not files[0].is_file():
        return None
    return files[0]


SEQUENCE_PROPERTIES = ("Skeleton", "AdditiveAnimType", "RefPoseType", "NumFrames", "SequenceLength")


def sequence(file):
    # Long clips carry very large per-track curve data; keep only the fields extraction uses.
    rows = document(file, 256 * 1024 * 1024)
    matches = [row for row in rows if row.get("Type") == "AnimSequence"]
    if len(matches) != 1:
        return None
    properties = matches[0].get("Properties") or {}
    slim = {key: properties[key] for key in SEQUENCE_PROPERTIES if key in properties}
    table = properties.get("TrackToSkeletonMapTable")
    if isinstance(table, list):
        slim["TrackToSkeletonMapTable"] = [dict(BoneTreeIndex=row["BoneTreeIndex"]) for row in table
                                           if isinstance(row, dict) and "BoneTreeIndex" in row]
    return dict(Type="AnimSequence", Name=matches[0].get("Name"), Properties=slim)


def clip_id(name, package, used):
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:48].strip("_") or "clip"
    result = stem
    if result in used:
        result = stem + "_" + hashlib.sha256(package.encode()).hexdigest()[:12]
    if result in used:
        raise ValueError("Game clip identities are ambiguous.")
    used.add(result)
    return result


def preserve_library(asset, output, rig, previous=None):
    original = asset / "Animation"
    if original.exists():
        if original.is_symlink() or not original.is_dir():
            raise ValueError("The existing Animation path must be an ordinary directory.")
        files = list(original.rglob("*"))
        if (len(files) > 4096 or any(file.is_symlink() for file in files)
                or sum(file.stat().st_size for file in files if file.is_file()) > 2 * 1024 ** 3):
            raise ValueError("The existing Animation directory contains linked or oversized resources.")
        # Preserve original exports and other user files as well as catalog clips.
        shutil.copytree(original, output, dirs_exist_ok=True)
    catalog = original / "manifest.json"
    if not catalog.is_file():
        return {}
    manifest = document(catalog)
    # The same skeleton can gain semantics (and so a new fingerprint); its clips stay valid.
    old_id = manifest.get("skeleton_id")
    same_skeleton = (previous is not None and old_id == previous["id"]
                     and previous["bones"] == rig["bones"])
    if (manifest.get("format") != "mod-viewer-animations" or manifest.get("version") != 1
            or (old_id != rig["id"] and not same_skeleton)):
        return {}
    check = rig if old_id == rig["id"] else {**rig, "id": old_id}
    entries = manifest.get("clips", [])
    if not isinstance(entries, list) or len(entries) > 1024:
        raise ValueError("Existing animation catalog is invalid.")
    result = {}
    total = 0
    for entry in entries:
        key = entry.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or key in result:
            raise ValueError("Existing animation IDs are invalid.")
        metadata, data = read_clip(str(catalog.parent), entry, check)
        total += len(data)
        if total > 2 * 1024 ** 3:
            raise ValueError("Animation library exceeds its size limit.")
        if check is not rig:
            write_document(output / entry["file"], {**metadata, "skeleton_id": rig["id"]}, 1024 * 1024)
        result[key] = entry
    return result


def reusable_clip_key(entries, existing, package, name, hashes):
    preferred = clip_id(name, package, set())
    references = {}
    for key, entry in entries.items():
        for file in (entry["file"], existing[key]["data_file"]):
            references.setdefault(file.replace("\\", "/").casefold(), []).append(key)
    for key, metadata in existing.items():
        owned = metadata.get("source") == package
        legacy_match = (key == preferred and not metadata.get("source")
                        and entries[key]["name"] == name and metadata["sha256"] in hashes)
        if not owned and not legacy_match:
            continue
        if (entries[key]["file"] == key + ".json" and metadata["data_file"] == key + ".bin"
                and references.get((key + ".json").casefold()) == [key]
                and references.get((key + ".bin").casefold()) == [key]):
            return key
    return None


ROLE_ROOT = "Client/Content/Aki/Character/Role"
BODY_FOLDERS = ("FemaleM", "FemaleMS", "FemaleS", "FemaleXL", "MaleM", "MaleMS", "MaleS", "MaleXL")


def section_shapes(file):
    rows = [row for row in document(file) if isinstance(row, dict) and row.get("Type") == "SkeletalMesh"]
    lods = rows[0].get("LODModels") if len(rows) == 1 else None
    if not isinstance(lods, list) or not lods or not isinstance(lods[0].get("Sections"), list):
        return None
    return {(section.get("NumVertices"), section.get("NumTriangles", 0) * 3) for section in lods[0]["Sections"]}


def structural_candidates(client, captured, cancel, index=None):
    """Find the meshes whose section vertex/index counts cover the most captured components.

    An Asset may also hold prop geometry that lives in another mesh, so full coverage is not required.
    """
    stride = [part["resources"]["fmt"].stride for part in captured]
    if not all(stride):
        return []
    wanted = {(len(part["resources"]["vb"]) // size, part["index_count"]) for part, size in zip(captured, stride)}
    if index is not None:
        scores = {package: len(wanted & index.shapes(package)) for package in index.search("", ROLE_ROOT)}
        scores = {package: score for package, score in scores.items() if score}
        best = max(scores.values(), default=0)
        return [package for package, score in scores.items() if score == best]
    packages = []
    for body in BODY_FOLDERS:
        check_cancelled(cancel)
        packages += client.search("", "SkeletalMesh", ROLE_ROOT + "/" + body)
    packages = list(dict.fromkeys(packages))
    directory = client.work / "mesh-shapes"
    files = client.batch("json", packages, directory)
    scores = {}
    for package in packages:
        check_cancelled(cancel)
        file = exported_file(files.get(package), directory, ".json")
        try:
            shapes = section_shapes(file) if file else None
        except (ValueError, TypeError, AttributeError):
            shapes = None
        if shapes and wanted & shapes:
            scores[package] = len(wanted & shapes)
    best = max(scores.values(), default=0)
    return [package for package, score in scores.items() if score == best]


def verify_candidates(client, captured, candidates, cancel):
    """Return (package, mesh, palettes, matched_components) for meshes proven against captured geometry.

    Only the meshes proving the most components compete, so props in other meshes do not block the body.
    """
    matches = []
    for number, candidate in enumerate(candidates):
        check_cancelled(cancel)
        native_file = client.export("mesh", candidate, client.work / "meshes", "ueformat")[0]
        try:
            mesh = read_mesh(native_file)
        except (ValueError, KeyError, IndexError):
            continue
        proven = []
        for index, component in enumerate(captured):
            try:
                proven.append((index, match_components([component], mesh)[0]))
            except (ValueError, KeyError, IndexError):
                continue
        if proven:
            matches.append((candidate, mesh, [row[1] for row in proven], [row[0] for row in proven]))
    best = max((len(row[3]) for row in matches), default=0)
    return [row for row in matches if len(row[3]) == best]


def sub_skeleton_map(primary, mesh):
    """Map a companion mesh's bones to the primary skeleton only when names, parents and rest transforms are identical."""
    index = {bone["name"]: number for number, bone in enumerate(primary["bones"])}
    if len(index) != len(primary["bones"]) or len({bone["name"] for bone in mesh["bones"]}) != len(mesh["bones"]):
        return None
    result = []
    for bone in mesh["bones"]:
        target = index.get(bone["name"])
        if target is None:
            return None
        other = primary["bones"][target]
        parent = mesh["bones"][bone["parent"]]["name"] if bone["parent"] >= 0 else None
        expected = primary["bones"][other["parent"]]["name"] if other["parent"] >= 0 else None
        if parent != expected or any(abs(a - b) > 1e-4 for key in ("translation", "rotation", "scale")
                                    for a, b in zip(bone[key], other[key])):
            return None
        result.append(target)
    return result


def adopt_companions(client, captured, primary, primary_package, proven, character_root, cancel, index=None):
    """Prove leftover components (props) against meshes sharing an exact sub-skeleton of the primary rig.

    A component is adopted only when exactly one companion mesh proves it; its palette is remapped to primary bones.
    """
    remaining = [index for index in range(len(captured)) if index not in proven]
    if not remaining:
        return {}
    character = character_root.rsplit("/", 1)[-1]
    try:
        if index is not None:
            names = index.search(character, "Client/Content/Aki/Character")
        else:
            names = client.search(character, "SkeletalMesh", "Client/Content/Aki/Character")
    except ExtractionCancelled:
        raise
    except ValueError:
        return {}
    found = {}
    for package in names[:64]:
        check_cancelled(cancel)
        if package == primary_package or package.startswith(character_root + "/"):
            continue
        try:
            mesh = read_mesh(client.export("mesh", package, client.work / "companions", "ueformat")[0])
        except (ValueError, KeyError, IndexError):
            continue
        mapping = sub_skeleton_map(primary, mesh)
        if mapping is None:
            continue
        for index in remaining:
            try:
                palette = match_components([captured[index]], mesh)[0]
            except (ValueError, KeyError, IndexError):
                continue
            palette["bone_map"] = [mapping[bone] for bone in palette["bone_map"]]
            found.setdefault(index, []).append((package, palette))
    return {index: rows[0] for index, rows in found.items() if len(rows) == 1}


def build(client, root, record, output, query, progress, cancel, index=None):
    asset = Path(root) / record["path"]
    captured, fingerprints = capture(root, record)
    progress("search", 0, 0)
    # The character name is tried first; captured geometry is the fallback.
    named = []
    if query:
        try:
            named = index.search(query, ROLE_ROOT) if index is not None else client.search(query, "SkeletalMesh", ROLE_ROOT)
        except ExtractionCancelled:
            raise
        except ValueError:
            named = []
    matches = verify_candidates(client, captured, named, cancel) if 0 < len(named) <= 32 else []
    tried = bool(matches) or 0 < len(named) <= 32
    if not matches:
        candidates = [row for row in structural_candidates(client, captured, cancel, index) if row not in named]
        if len(candidates) > 32:
            candidates = []
        tried = tried or bool(candidates)
        matches = verify_candidates(client, captured, candidates, cancel)
    if not tried:
        raise ValueError("No specific game mesh candidates were found. Enter the character's game name and retry.")
    previous = None
    if (asset / "Rig.json").is_file():
        try:
            previous = validate_rig(document(asset / "Rig.json"))
        except (ValueError, KeyError, TypeError):
            pass
    progress("rig", 0, 1)
    if len(matches) != 1:
        raise ValueError("A unique matching game mesh could not be verified against this Asset's positions, UVs and weights.")
    mesh_package, mesh, palettes, proven = matches[0]
    parts = mesh_package.split("/")
    role = parts.index("Role")
    character_root = "/".join(parts[:role + 3])
    skin_root = mesh_package.rsplit("/", 2)[0]
    palette_by_component = dict(zip(proven, palettes))
    companions = adopt_companions(client, captured, mesh, mesh_package, set(proven), character_root, cancel, index)
    for index, (_package, palette) in companions.items():
        palette_by_component[index] = palette
    proven = sorted(palette_by_component)
    rig = make_rig(mesh, [palette_by_component[index] for index in proven], previous)
    mesh_document = document(client.export("json", mesh_package, client.work / "metadata")[0])
    meshes = [row for row in mesh_document if row.get("Type") == "SkeletalMesh"]
    skeleton = package_name(meshes[0].get("Properties", {}).get("Skeleton", {}).get("ObjectPath")) if len(meshes) == 1 else ""
    if not skeleton:
        raise ValueError("The matching mesh does not declare a usable source skeleton.")
    progress("animations", 0, 0)
    packages = client.search("", "AnimSequence", character_root)
    if not packages:
        raise ValueError("No character animation sequences were found.")
    metadata_dir = client.work / "animation-metadata"
    metadata_files = client.batch("json", packages, metadata_dir)
    sequences, skipped, reasons = {}, 0, collections.Counter()
    for package in packages:
        check_cancelled(cancel)
        file = exported_file(metadata_files.get(package), metadata_dir, ".json")
        if file is None:
            skipped += 1
            continue
        item = sequence(file)
        if not item:
            skipped += 1
            continue
        properties = item.get("Properties", {})
        if package_name(properties.get("Skeleton", {}).get("ObjectPath")) != skeleton:
            continue
        sequences[package] = item
    bases = {path: item for path, item in sequences.items()
             if item["Properties"].get("AdditiveAnimType") in (None, "AAT_None")
             and item["Properties"].get("TrackToSkeletonMapTable")
             and type(item["Properties"].get("NumFrames")) is int
             and 2 <= item["Properties"]["NumFrames"] <= 60000
             and type(item["Properties"].get("SequenceLength")) in (int, float)
             and 0 < item["Properties"]["SequenceLength"] <= 3600}
    if not bases:
        raise ValueError("No compatible skeletal animation clips were found for the verified rig.")
    pairs = {}
    for package, item in bases.items():
        # Supported game layout: exact companion under this skin's RibbonAnim.
        # Skeleton, source timing, additive metadata and omitted base tracks also
        # have to agree; a similar filename alone never authorizes a layer.
        layer_path = skin_root + "/RibbonAnim/Rib_" + item["Name"]
        layer = sequences.get(layer_path)
        if not package.startswith(character_root + "/CommonAnim/") or not layer:
            continue
        base, extra = item["Properties"], layer["Properties"]
        if (extra.get("AdditiveAnimType") == "AAT_LocalSpaceBase" and extra.get("RefPoseType") == "ABPT_RefPose"
                and extra.get("NumFrames") == base["NumFrames"]
                and extra.get("SequenceLength") == base["SequenceLength"]):
            body_ids = {row["BoneTreeIndex"] for row in base["TrackToSkeletonMapTable"]}
            mask = sorted({row["BoneTreeIndex"] for row in extra["TrackToSkeletonMapTable"]} - body_ids)
            if mask:
                pairs[package] = layer_path, mask
    animation_dir = client.work / "animations"
    tracks = client.batch("anim", sorted(set(bases) | {row[0] for row in pairs.values()}), animation_dir, "psa")
    output.mkdir(parents=True, exist_ok=True)
    library = output / "Animation"
    library.mkdir()
    entries = preserve_library(asset, library, rig, previous)
    existing = {key: document(library / entry["file"]) for key, entry in entries.items()}
    used, imported, secondary = set(entries), 0, 0
    provenance = []
    ordered = sorted(bases, key=lambda package: (not package.startswith(character_root + "/CommonAnim/"), package))
    for number, package in enumerate(ordered):
        check_cancelled(cancel)
        progress("convert", number, len(ordered))
        item = bases[package]
        file = exported_file(tracks.get(package), animation_dir, ".psa")
        if file is None:
            skipped += 1
            reasons["The native tool could not export the animation tracks."] += 1
            continue
        duration = item["Properties"]["SequenceLength"]
        try:
            frames, binary = convert_clip(file.read_bytes(), rig, duration)
        except (ValueError, KeyError) as error:
            skipped += 1
            reasons[str(error) if isinstance(error, ValueError) else "Animation data is missing a required chunk."] += 1
            continue
        base_hash = hashlib.sha256(binary).hexdigest()
        layer_info = None
        if package in pairs:
            layer_path, mask = pairs[package]
            layer_file = exported_file(tracks.get(layer_path), animation_dir, ".psa")
            try:
                if layer_file is None:
                    raise ValueError("Secondary clip is unavailable.")
                layer_frames, layer_binary = convert_clip(layer_file.read_bytes(), rig, duration)
                if layer_frames != frames:
                    raise ValueError("Secondary clip timing differs.")
                binary = merge_reference_layer(binary, layer_binary, rig, mask)
                layer_info = dict(source=layer_path, sha256=hashlib.sha256(layer_file.read_bytes()).hexdigest(), bone_ids=mask)
                secondary += 1
            except (ValueError, KeyError):
                skipped += 1
        binary_hash = hashlib.sha256(binary).hexdigest()
        key = reusable_clip_key(entries, existing, package, item["Name"], {base_hash, binary_hash})
        if key is None:
            key = clip_id(item["Name"], package, used)
            if (library / (key + ".json")).exists() or (library / (key + ".bin")).exists():
                key = clip_id(item["Name"], package, used)
            if (library / (key + ".json")).exists() or (library / (key + ".bin")).exists():
                raise ValueError("Extracted clip filenames conflict with preserved user resources.")
        metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
                        skeleton_id=rig["id"], frame_count=frames, bone_count=len(rig["bones"]), duration=duration,
                        data_file=key + ".bin", sha256=binary_hash, source=package)
        if layer_info:
            metadata["reference_layer"] = layer_info
        (library / (key + ".bin")).write_bytes(binary)
        write_document(library / (key + ".json"), metadata, 1024 * 1024)
        label = item["Name"]
        if key != clip_id(item["Name"], package, set()):
            label += " (" + package.removeprefix(character_root + "/").rsplit("/", 1)[0] + ")"
        entry = dict(id=key, name=label[:128], file=key + ".json", duration=duration)
        read_clip(str(library), entry, rig)
        entries[key] = entry
        existing[key] = metadata
        provenance.append(dict(id=key, source=package, secondary=layer_info))
        imported += 1
    if not imported or len(entries) > 1024:
        detail = f" Most common skip reason ({sum(reasons.values())} of {len(ordered)} clips): {reasons.most_common(1)[0][0]}" if reasons else ""
        raise ValueError("No usable animation library could be produced within the viewer limits." + detail)
    for entry in entries.values():
        read_clip(str(library), entry, rig)
    if sum(file.stat().st_size for file in library.rglob("*") if file.is_file()) > 2 * 1024 ** 3:
        raise ValueError("The extracted animation library exceeds its size limit.")
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=list(entries.values()))
    write_document(library / "manifest.json", manifest, 1024 * 1024)
    write_document(output / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
    report = dict(format="mod-viewer-rig-extraction", version=1, game="wuwa", mesh=mesh_package,
                  skeleton=skeleton, skeleton_id=rig["id"], proven_components=len(proven), unmatched_components=len(captured) - len(proven),
                  companion_meshes=sorted({package for package, _ in companions.values()}), animations=provenance,
                  bones=len(rig["bones"]), clips=len(entries), imported=imported, secondary=secondary, skipped=skipped)
    (output / "RigExtraction.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report, fingerprints
