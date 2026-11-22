"""Build a detached, verified rig/animation package before touching an Asset."""

import collections
import hashlib
import json
from pathlib import Path
import re
import shutil

from app.assets.animation_conversion import ReferencePoseMismatch, convert_clip, merge_reference_layer, reference_layer_timing
from app.assets import animation_keys
from app.assets.animations import read_clip
from app.assets.rig import validate_rig
from . import montage_sequences, shared_layers
from .native import capture, make_rig, match_components, read_mesh, read_limited
from .runtime import ExtractionCancelled, check_cancelled, list_packages
from .morphs import export_morphs, sample_curves


# ActorX's 32-byte keys plus skeleton headers for the viewer's 128 MiB clip limit.
MAX_ANIMATION_BYTES = 160 * 1024 * 1024


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
# Notify states that spawn an effect (which may be an animated mesh) for their window.
EFFECT_NOTIFY = "AnimNotifyStateEffect_C"
# Effect notify settings prop extraction reproduces (see wuwa_effect_props): the
# effect, its socket and attachment, its stop, time sync, its replacement slot, its inner
# notifies and what happens when a skill ends (the viewer's clip has no skill). Any other
# one (an offset, a follow rule, a tag gate...) is reported, never approximated.
EFFECT_SETTINGS = frozenset(("EffectDataAssetRef", "SocketName", "Attached", "FasterStop", "SyncEventTimeToEffectTime",
                             "SyncEventTimeContinuous", "EffectSlotName", "EndInnerAnimNotifyStatesOnStop",
                             "DetachWhenSkillEnd", "WhenSkillEnd", "exportIndex", "CurrentTimeLength"))
MAX_EFFECT_NOTIFIES = 64


def effect_notifies(row, rows):
    """The effect notify states of one exported sequence ``row`` as plain facts:
    ``[{effect, socket, attached, faster_stop, start, duration, link, settings}]``.
    Both flags default to on: ``FasterStop`` in the notify class, and ``Attached`` in its
    native base, which cooked notifies only ever turn off (only values differing from the
    default are stored, and no notify stores it on). ``settings`` lists the notify
    object's other (or unreadable) property names. Nothing here is validated."""
    objects = {}
    for item in rows:
        if isinstance(item, dict) and item.get("Type") == EFFECT_NOTIFY and isinstance(item.get("Name"), str):
            # A name exported twice is never guessed between.
            objects[item["Name"]] = None if item["Name"] in objects else item
    notifies = (row.get("Properties") or {}).get("Notifies")
    found = []
    for notify in notifies if isinstance(notifies, list) else []:
        state = notify.get("NotifyStateClass") if isinstance(notify, dict) else None
        name = state.get("ObjectName") if isinstance(state, dict) else None
        if not isinstance(name, str) or not name.startswith(EFFECT_NOTIFY + "'"):
            continue
        target = objects.get(name[len(EFFECT_NOTIFY) + 1:].rstrip("'").rsplit(":", 1)[-1])
        properties = target.get("Properties") if isinstance(target, dict) else None
        properties = properties if isinstance(properties, dict) else {}
        reference, socket = properties.get("EffectDataAssetRef"), properties.get("SocketName")
        attached, faster = properties.get("Attached", True), properties.get("FasterStop", True)
        settings = sorted(set(properties) - EFFECT_SETTINGS) if target else ["(unreadable)"]
        settings += [key for key, value in (("Attached", attached), ("FasterStop", faster))
                     if value is not None and not isinstance(value, bool)]
        found.append(dict(
            effect=package_name(reference.get("AssetPathName")) if isinstance(reference, dict) else "",
            socket=socket if isinstance(socket, str) and socket not in ("", "None") else None,
            attached=attached is not False, faster_stop=faster is not False,
            start=notify.get("LinkValue"), duration=notify.get("Duration"), link=notify.get("LinkMethod"),
            settings=sorted(settings)))
        if len(found) == MAX_EFFECT_NOTIFIES:
            break
    return found


def sequence(file, morph_names=(), *, skeleton=None):
    # Long clips carry very large per-track curve data; keep only the fields extraction uses.
    rows = document(file, 256 * 1024 * 1024)
    matches = [row for row in rows if row.get("Type") == "AnimSequence"]
    if len(matches) != 1:
        return None
    properties = matches[0].get("Properties") or {}
    if (properties.get("AdditiveAnimType") not in (None, "AAT_None")
            or (skeleton is not None and package_name(properties.get("Skeleton", {}).get("ObjectPath")) != skeleton)):
        morph_names = ()
    slim = {key: properties[key] for key in SEQUENCE_PROPERTIES if key in properties}
    table = properties.get("TrackToSkeletonMapTable")
    if isinstance(table, list):
        slim["TrackToSkeletonMapTable"] = [dict(BoneTreeIndex=row["BoneTreeIndex"]) for row in table
                                           if isinstance(row, dict) and "BoneTreeIndex" in row]
    return dict(Type="AnimSequence", Name=matches[0].get("Name"), Properties=slim,
                MorphWeights=sample_curves(matches[0], morph_names), Effects=effect_notifies(matches[0], rows))


def animation_reference(client, skeleton, rig):
    """Read the mesh's declared skeleton for conservative absolute-local playback.

    This fallback is only for Animation mode with an identical ordered hierarchy.
    The native converter does no retargeting beyond a short BoneTree table, so
    absent trailing entries have the same absolute-local behavior as Animation.
    Explicit other modes require native mesh-targeted conversion first.
    """
    bones = source_skeleton(client, skeleton, strict_tree=True)
    if len(bones) != len(rig["bones"]):
        raise ValueError("The animation source skeleton requires unsupported retargeting.")
    return validate_rig({**rig, "bones": bones})


def source_skeleton(client, skeleton, *, strict_tree=False):
    """Reference bones of a game Skeleton whose BoneTree uses only Animation retargeting.

    ``strict_tree`` also refuses a BoneTree longer than the reference skeleton, as
    mesh-reference playback requires; extra entries are otherwise inert because the
    converter verifies every exported bone against this reference.
    """
    rows = document(client.export("json", skeleton, client.work / "source-skeleton")[0])
    sources = [row for row in rows if row.get("Type") == "Skeleton"]
    if len(sources) != 1:
        raise ValueError("The animation source skeleton is unavailable.")
    source = sources[0]
    reference = source.get("ReferenceSkeleton", {})
    infos, poses = reference.get("FinalRefBoneInfo"), reference.get("FinalRefBonePose")
    modes = source.get("Properties", {}).get("BoneTree")
    if (not isinstance(infos, list) or not isinstance(poses, list) or not isinstance(modes, list)
            or len(poses) != len(infos) or (strict_tree and len(modes) > len(infos))
            or any(not isinstance(mode, dict) or mode.get("TranslationRetargetingMode") != "EBoneTranslationRetargetingMode::Animation"
                   for mode in modes)):
        raise ValueError("The animation source skeleton requires unsupported retargeting.")
    return [dict(name=info["Name"], parent=info["ParentIndex"],
                 translation=[pose["Translation"][axis] for axis in ("X", "Y", "Z")],
                 rotation=[pose["Rotation"][axis] for axis in ("X", "Y", "Z", "W")],
                 scale=[pose["Scale3D"][axis] for axis in ("X", "Y", "Z")])
            for info, pose in zip(infos, poses)]


def is_base_clip(item):
    """A complete, non-additive clip within the viewer's frame and duration limits."""
    properties = item["Properties"]
    return (properties.get("AdditiveAnimType") in (None, "AAT_None")
            and bool(properties.get("TrackToSkeletonMapTable"))
            and type(properties.get("NumFrames")) is int
            and 2 <= properties["NumFrames"] <= 60000
            and type(properties.get("SequenceLength")) in (int, float)
            and 0 < properties["SequenceLength"] <= 3600)


def clip_id(name, package, used):
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:48].strip("_") or "clip"
    result = stem
    occupied = {key.casefold() for key in used}
    if result.casefold() in occupied:
        result = stem + "_" + hashlib.sha256(package.encode()).hexdigest()[:12]
    if result.casefold() in occupied:
        raise ValueError("Game clip identities are ambiguous.")
    used.add(result)
    return result


def preserve_library(asset, output, rig, previous=None, *, validated_morphs=None):
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
        metadata, data = read_clip(str(catalog.parent), entry, check, validated_morphs=validated_morphs)
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


def rest_worlds(bones, *, affine=False):
    """Compose validated reference transforms, rejecting malformed or cyclic hierarchies."""
    import numpy as np

    local, parents = [], []
    for bone in bones:
        parent = bone["parent"]
        if type(parent) is not int or not -1 <= parent < len(bones):
            raise ValueError("Invalid companion skeleton parent.")
        translation, rotation, scale = [np.asarray(bone[key], dtype=float) for key in ("translation", "rotation", "scale")]
        if (translation.shape != (3,) or rotation.shape != (4,) or scale.shape != (3,)
                or any(not np.isfinite(value).all() or (np.abs(value) > 1e6).any() for value in (translation, rotation, scale))):
            raise ValueError("Invalid companion reference transform.")
        length = np.linalg.norm(rotation)
        if (abs(length - 1) > .001 or np.min(np.abs(scale)) < 1e-6
                or (not affine and np.any(np.abs(scale - scale[0]) > .002 * abs(scale[0])))):
            raise ValueError("Invalid companion reference rotation or scale.")
        x, y, z, w = rotation / length
        matrix = np.eye(4)
        matrix[:3, :3] = np.array([
            [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
            [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
            [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
        ]) * scale
        matrix[:3, 3] = translation
        local.append(matrix)
        parents.append(parent)
    worlds = [None] * len(bones)
    for index in range(len(bones)):
        chain, visiting = [], set()
        current = index
        while current >= 0 and worlds[current] is None:
            if current in visiting:
                raise ValueError("Cyclic companion skeleton.")
            visiting.add(current)
            chain.append(current)
            current = parents[current]
        for current in reversed(chain):
            parent = parents[current]
            worlds[current] = local[current] if parent < 0 else worlds[parent] @ local[current]
            if not np.isfinite(worlds[current]).all():
                raise ValueError("Invalid companion world reference transform.")
    return worlds


def sub_skeleton_map(primary, mesh, *, affine=False, worlds_for=None):
    """Prove named bones, the induced parent hierarchy and identical world reference transforms.

    ``worlds_for`` limits the reference-world comparison to those ``mesh`` bone ordinals;
    names and hierarchy are always proven for every bone.
    """
    import numpy as np

    index = {bone["name"]: number for number, bone in enumerate(primary["bones"])}
    names = {bone["name"] for bone in mesh["bones"]}
    if not names or len(index) != len(primary["bones"]) or len(names) != len(mesh["bones"]):
        return None
    try:
        primary_worlds, mesh_worlds = rest_worlds(primary["bones"], affine=affine), rest_worlds(mesh["bones"], affine=affine)
    except (ValueError, TypeError, KeyError):
        return None
    result = []
    for ordinal, bone in enumerate(mesh["bones"]):
        target = index.get(bone["name"])
        if target is None:
            return None
        parent = mesh["bones"][bone["parent"]]["name"] if bone["parent"] >= 0 else None
        ancestor = primary["bones"][target]["parent"]
        while ancestor >= 0 and primary["bones"][ancestor]["name"] not in names:
            ancestor = primary["bones"][ancestor]["parent"]
        expected = primary["bones"][ancestor]["name"] if ancestor >= 0 else None
        if parent != expected or ((worlds_for is None or ordinal in worlds_for)
                                  and not np.allclose(primary_worlds[target], mesh_worlds[ordinal], rtol=0, atol=1e-4)):
            return None
        result.append(target)
    return result


def adopt_companions(client, captured, primary, primary_package, proven, character_root, cancel):
    """Prove leftover components (props) against compatible sub-skeletons of the primary rig.

    A component is adopted only when exactly one companion mesh proves it; its palette is remapped to primary bones.
    """
    remaining = [index for index in range(len(captured)) if index not in proven]
    if not remaining:
        return {}
    character = character_root.rsplit("/", 1)[-1]
    try:
        # The shape index covers only some character folders. It must never hide
        # accessories elsewhere or a second matching mesh that makes proof ambiguous.
        names = list(dict.fromkeys(client.search(character, "SkeletalMesh", "Client/Content/Aki/Character")))
    except ExtractionCancelled:
        raise
    except ValueError:
        return {}
    if len(names) > 64:
        return {}
    found = {}
    for package in names:
        check_cancelled(cancel)
        if package == primary_package:
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
    validated_morphs = set()
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
    companions = adopt_companions(client, captured, mesh, mesh_package, set(proven), character_root, cancel)
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
    packages = list_packages(client, "AnimSequence", character_root, cancel)
    if not packages:
        raise ValueError("No character animation sequences were found.")
    metadata_dir = client.work / "animation-metadata"
    metadata_files = client.batch("json", packages, metadata_dir)
    sequences, skipped_items, reasons = {}, {}, collections.Counter()
    other_skeletons = set()

    def reject(kind, source, reason, base=None):
        item = dict(kind=kind, source=source, reason=reason)
        if base is not None:
            item["base"] = base
        skipped_items[source] = item

    for package in packages:
        check_cancelled(cancel)
        file = exported_file(metadata_files.get(package), metadata_dir, ".json")
        if file is None:
            reject("metadata", package, "The native tool could not export sequence metadata.")
            continue
        item = sequence(file, mesh.get("morphs", {}), skeleton=skeleton)
        if not item:
            reject("metadata", package, "The exported package does not contain one animation sequence.")
            continue
        properties = item.get("Properties", {})
        if package_name(properties.get("Skeleton", {}).get("ObjectPath")) != skeleton:
            other_skeletons.add(package)
            continue
        sequences[package] = item
    bases = {path: item for path, item in sequences.items() if is_base_clip(item)}
    if not bases:
        raise ValueError("No compatible skeletal animation clips were found for the verified rig.")
    pairs = {}
    for package, item in bases.items():
        # Supported game layout: exact companion under this skin's RibbonAnim.
        # Skeleton, sample spacing, additive metadata and omitted base tracks also
        # have to agree; a similar filename alone never authorizes a layer.
        layer_path = skin_root + "/RibbonAnim/Rib_" + item["Name"]
        layer = sequences.get(layer_path)
        if not package.startswith(character_root + "/CommonAnim/"):
            continue
        if layer is None:
            if layer_path in packages:
                reason = ("Secondary clip skeleton differs from the verified body skeleton." if layer_path in other_skeletons
                          else skipped_items.get(layer_path, {}).get("reason", "Secondary clip metadata is unavailable."))
                reject("layer", layer_path, reason, package)
            continue
        base, extra = item["Properties"], layer["Properties"]
        try:
            if extra.get("AdditiveAnimType") != "AAT_LocalSpaceBase" or extra.get("RefPoseType") != "ABPT_RefPose":
                raise ValueError("Secondary clip is not a reference-pose local additive layer.")
            timing = reference_layer_timing(base["NumFrames"], base["SequenceLength"],
                                            extra.get("NumFrames"), extra.get("SequenceLength"))
            for table in (base["TrackToSkeletonMapTable"], extra["TrackToSkeletonMapTable"]):
                ids = [row["BoneTreeIndex"] for row in table]
                if not ids or any(type(i) is not int or not 0 <= i < len(rig["bones"]) for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Secondary clip authored bone mask is invalid.")
            body_ids = {row["BoneTreeIndex"] for row in base["TrackToSkeletonMapTable"]}
            mask = sorted({row["BoneTreeIndex"] for row in extra["TrackToSkeletonMapTable"]} - body_ids)
            if not mask:
                raise ValueError("Secondary clip has no body-omitted authored tracks.")
            pairs[package] = layer_path, mask, timing
        except (ValueError, KeyError, TypeError) as error:
            reject("layer", layer_path, str(error) if isinstance(error, ValueError) else "Secondary clip metadata is incomplete.", package)
    animation_dir = client.work / "animations"
    tracks = client.batch("anim", sorted(set(bases) | {row[0] for row in pairs.values()}), animation_dir, "psa")
    output.mkdir(parents=True, exist_ok=True)
    library = output / "Animation"
    library.mkdir()
    entries = preserve_library(asset, library, rig, previous, validated_morphs=validated_morphs)
    morph_targets = export_morphs(mesh, [{**captured[index],
                                       "first_index": palette_by_component[index]["native_first_index"]} for index in proven
                                       if index not in companions], library)
    existing = {key: document(library / entry["file"]) for key, entry in entries.items()}
    used, imported, secondary = set(entries), 0, 0
    reference = None
    reference_failure = None

    def convert(data, item, duration):
        nonlocal reference, reference_failure
        if reference is None:
            try:
                return convert_clip(data, rig, duration)
            except ReferencePoseMismatch:
                if reference_failure is not None:
                    raise ValueError(reference_failure)
                try:
                    reference = animation_reference(client, skeleton, rig)
                except (ValueError, KeyError, TypeError) as error:
                    reference_failure = str(error) if isinstance(error, ValueError) else "Animation source skeleton metadata is incomplete."
                    raise ValueError(reference_failure) from error
        tracks = item["Properties"]["TrackToSkeletonMapTable"]
        return convert_clip(data, rig, duration, reference=reference, authored_bones=[row["BoneTreeIndex"] for row in tracks])

    provenance = []
    ordered = sorted(bases, key=lambda package: (not package.startswith(character_root + "/CommonAnim/"), package))
    for number, package in enumerate(ordered):
        check_cancelled(cancel)
        progress("convert", number, len(ordered))
        item = bases[package]
        file = exported_file(tracks.get(package), animation_dir, ".psa")
        if file is None:
            reason = "The native tool could not export the animation tracks."
            reject("clip", package, reason)
            reasons[reason] += 1
            continue
        duration = item["Properties"]["SequenceLength"]
        try:
            frames, binary = convert(read_limited(file, MAX_ANIMATION_BYTES, "Native animation"), item, duration)
            if frames != item["Properties"]["NumFrames"]:
                raise ValueError("Animation frame count differs from its sequence metadata.")
        except (ValueError, KeyError) as error:
            reason = str(error) if isinstance(error, ValueError) else "Animation data is missing a required chunk."
            reject("clip", package, reason)
            reasons[reason] += 1
            continue
        base_hash = hashlib.sha256(binary).hexdigest()
        # Bones the authored body track table keys; unknown tables record no evidence.
        table = item["Properties"].get("TrackToSkeletonMapTable")
        keyed = None
        if isinstance(table, list) and all(isinstance(row, dict) for row in table):
            ids = [row.get("BoneTreeIndex") for row in table]
            if all(type(i) is int and 0 <= i < len(rig["bones"]) for i in ids):
                keyed = set(ids)
        layer_info = None
        if package in pairs:
            layer_path, mask, timing = pairs[package]
            layer_file = exported_file(tracks.get(layer_path), animation_dir, ".psa")
            try:
                if layer_file is None:
                    raise ValueError("Secondary clip is unavailable.")
                layer_duration = timing["layer_duration"]
                layer_data = read_limited(layer_file, MAX_ANIMATION_BYTES, "Native animation")
                layer_frames, layer_binary = convert(layer_data, sequences[layer_path], layer_duration)
                if layer_frames != timing["layer_frames"]:
                    raise ValueError("Secondary clip frame count differs from its sequence metadata.")
                binary = merge_reference_layer(binary, layer_binary, rig, mask, duration=duration, layer_duration=layer_duration)
                layer_info = dict(source=layer_path, sha256=hashlib.sha256(layer_data).hexdigest(), bone_ids=mask, timing=timing)
                if keyed is not None:
                    keyed.update(mask)
                secondary += 1
            except (ValueError, KeyError) as error:
                reject("layer", layer_path, str(error) if isinstance(error, ValueError) else "Secondary clip data is incomplete.", package)
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
        if morph_targets and item.get("MorphWeights"):
            morph_weights = item["MorphWeights"]
            binary += morph_weights["data"]
            metadata["sha256"] = hashlib.sha256(binary).hexdigest()
            metadata["morphs"] = {**morph_targets, "names": morph_weights["names"]}
        if layer_info:
            metadata["reference_layer"] = layer_info
        if keyed is not None:
            metadata["keyed_bones"] = sorted(keyed)
        (library / (key + ".bin")).write_bytes(binary)
        write_document(library / (key + ".json"), metadata, 1024 * 1024)
        label = item["Name"]
        if key != clip_id(item["Name"], package, set()):
            label += " (" + package.removeprefix(character_root + "/").rsplit("/", 1)[0] + ")"
        entry = dict(id=key, name=label[:128], file=key + ".json", duration=duration)
        entries[key] = entry
        existing[key] = metadata
        provenance.append(dict(id=key, source=package, secondary=layer_info))
        imported += 1
    if not imported or len(entries) > 1024:
        detail = f" Most common skip reason ({reasons.most_common(1)[0][1]} of {len(ordered)} clips): {reasons.most_common(1)[0][0]}" if reasons else ""
        raise ValueError("No usable animation library could be produced within the viewer limits." + detail)
    for entry in entries.values():
        read_clip(str(library), entry, rig, validated_morphs=validated_morphs)
    # The character's own ribbon layers for the shared body-type clips it can play.
    layers, layer_masks, shared_layer_bones = shared_layers.extract(client, Path(root), rig, sequences, skin_root, convert,
                                                       library, reject, cancel)
    layer_bones = set(shared_layer_bones)
    for item in provenance:
        if item["secondary"]:
            layer_bones.update(item["secondary"]["bone_ids"])
    if sum(file.stat().st_size for file in library.rglob("*") if file.is_file()) > 2 * 1024 ** 3:
        raise ValueError("The extracted animation library exceeds its size limit.")
    converted = {item["source"]: (item["id"], entries[item["id"]]["duration"]) for item in provenance}
    # Each clip's own effect notify windows, for props the game spawns while they run.
    clip_effects = {item["id"]: (entries[item["id"]]["duration"], bases[item["source"]].get("Effects") or [])
                    for item in provenance}
    sequences, skipped_sequences, sequence_error = [], [], None
    try:
        montages = sorted(list_packages(client, "AnimMontage", character_root, cancel))[:montage_sequences.MAX_MONTAGES]
        montage_dir = client.work / "montage-metadata"
        files = client.batch("json", montages, montage_dir) if montages else {}
        loaded = []
        for package in montages:
            check_cancelled(cancel)
            file = exported_file(files.get(package), montage_dir, ".json")
            exports = document(file, 4 * 1024 * 1024) if file else None
            found = [item for item in exports if isinstance(item, dict) and item.get("Type") == "AnimMontage"] if isinstance(exports, list) else []
            properties = found[0].get("Properties") if len(found) == 1 else None
            if not isinstance(properties, dict) or package_name((properties.get("Skeleton") or {}).get("ObjectPath")) != skeleton:
                skipped_sequences.append(dict(source=package, reason="The montage metadata is unavailable or uses another skeleton."))
                continue
            loaded.append((package, properties))
        sequences, rejected = montage_sequences.sequences(loaded, converted, character_root)
        skipped_sequences += rejected
    except ExtractionCancelled:
        raise
    except (ValueError, KeyError, TypeError) as error:
        # Sequences are optional: discovery failures never block the clips.
        sequences, sequence_error = [], str(error) if isinstance(error, ValueError) else "Montage metadata is incomplete."
    # Start/Loop/End clip sets chained by gameplay code get labeled name-ordered previews
    # after the authored montage timelines, within the same sequence bounds.
    named = montage_sequences.name_sequences(converted, {item["id"] for item in sequences})
    total, name_sequences = sum(len(item["steps"]) for item in sequences), 0
    for item in named:
        if len(sequences) == montage_sequences.MAX_SEQUENCES or total + len(item["steps"]) > montage_sequences.MAX_SEQUENCE_TOTAL_STEPS:
            break
        sequences.append(item)
        total += len(item["steps"])
        name_sequences += 1
    # Props the capture never drew, shown only while the clips that attach and time them play.
    from . import wuwa_props
    props, skipped_props, prop_error = [], [], None
    # The Asset's own clips by source name (a skin folder may convert a name again), for
    # props whose animations pair with them.
    own_clips = collections.defaultdict(list)
    for item in provenance:
        own_clips[item["source"].rsplit("/", 1)[-1]].append(
            (item["id"], existing[item["id"]]["frame_count"], entries[item["id"]]["duration"]))
    try:
        props, skipped_props = wuwa_props.extract(
            client, Path(root), record, captured, {index: palette_by_component[index] for index in proven if index not in companions},
            mesh_package, mesh_document, rig, own_clips, clip_effects, library, cancel)
    except ExtractionCancelled:
        raise
    except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
        prop_error = str(error) if isinstance(error, ValueError) else "The props could not be extracted."
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=list(entries.values()), sequences=sequences,
                    runtime_bones=animation_keys.runtime_bones(rig, existing.values()),
                    shared_layers=layers, shared_layer_masks=layer_masks, layer_bones=sorted(layer_bones))
    if props:
        manifest["props"] = [{key: value for key, value in prop.items() if key not in ("source", "texture_error")}
                             for prop in props]
    write_document(library / "manifest.json", manifest, 1024 * 1024)
    write_document(output / "Rig.json", {key: value for key, value in rig.items() if key != "id"}, 2 * 1024 * 1024)
    report = dict(format="mod-viewer-rig-extraction", version=1, game="wuwa", mesh=mesh_package,
                  skeleton=skeleton, skeleton_id=rig["id"], proven_components=len(proven), unmatched_components=len(captured) - len(proven),
                  companion_meshes=sorted({package for package, _ in companions.values()}), animations=provenance,
                  bones=len(rig["bones"]), clips=len(entries), imported=imported, secondary=secondary,
                  skipped=len(skipped_items), skipped_layers=sum(item["kind"] == "layer" for item in skipped_items.values()),
                  skipped_details=list(skipped_items.values()),
                  sequences=len(sequences), name_sequences=name_sequences, skipped_sequences=len(skipped_sequences),
                  skipped_sequence_details=skipped_sequences, sequence_error=sequence_error,
                  shared_layers=len(layers),
                  props=[dict(id=prop["id"], name=prop["name"], source=prop["source"], textures=sorted(prop.get("textures", {})),
                              **({"texture_error": prop["texture_error"]} if "texture_error" in prop else {})) for prop in props],
                  skipped_props=skipped_props, **({"prop_error": prop_error} if prop_error else {}))
    (output / "RigExtraction.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report, fingerprints
