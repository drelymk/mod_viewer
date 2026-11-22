"""Animated Wuthering Waves blueprint props, shown while their paired clips play.

A character blueprint can declare extra skeletal mesh components on its construction
script (an ``SCS_Node`` whose parent is ``CharacterMesh0`` and whose ``AttachToName``
names a socket); the character's notifies show them while the game plays the prop's
own animation, such as a character's scissors during attacks. A derived blueprint may
override a component's template (its mesh). Weapon cases are excluded: they hold the
regular weapon, which is not exported. Only components without their own relative
offset are supported.

The prop's animation for a clip is the prop sequence in the prop mesh's folder, on the
prop's own skeleton, named after the clip exactly or with a ``_<suffix>`` (the longest
matching clip name wins), sampled at the same frame interval and no longer than the
clip, since the prop plays from the clip's start. A clip paired with two sequences is
never guessed between, and a sequence one of the clip's own effects plays belongs to
that effect (see wuwa_effect_props), never to the component. Intentional exception to name-free proof, like ribbon layers'
``Rib_`` names: the game pairs them in its animation blueprints. Each pair is baked
into per-frame prop bone transforms relative to the socket (``prop-track-v1``), and a
prop shows only while its paired animation runs.
"""

import numpy as np

from app.assets.animation_conversion import ReferencePoseMismatch, convert_clip
from app.assets.animations import MAX_PROP_BONES
from .mecanim import qfrom_basis
from .native import read_limited
from .pipeline import MAX_ANIMATION_BYTES, document, exported_file, package_name, rest_worlds, sequence, source_skeleton
from .runtime import ExtractionCancelled, check_cancelled, list_packages
from .wuwa_props import CHARACTER_ROOT, mesh_parts

MAX_BLUEPRINT_DEPTH = 4
MAX_COMPONENTS = 8
MAX_SEQUENCES = 512
_TEMPLATE = "_GEN_VARIABLE"
_OFFSETS = {"RelativeLocation": (0, 0, 0), "RelativeRotation": (0, 0, 0), "RelativeScale3D": (1, 1, 1)}


def _chain(client, blueprint, cache, cancel):
    """(package, exports) of a blueprint and its character blueprint ancestors, most derived first."""
    chain, package = [], blueprint
    while package and len(chain) <= MAX_BLUEPRINT_DEPTH and all(package != row[0] for row in chain):
        check_cancelled(cancel)
        if package not in cache:
            try:
                cache[package] = document(client.export("json", package, client.work / "blueprints")[0], 16 * 1024 * 1024)
            except ExtractionCancelled:
                raise
            except (ValueError, KeyError, TypeError, IndexError, OSError):
                cache[package] = None
        exports = cache[package]
        if not isinstance(exports, list):
            break
        chain.append((package, exports))
        parents = {package_name(item["SuperStruct"].get("ObjectPath")) for item in exports
                   if isinstance(item, dict) and item.get("Type") == "BlueprintGeneratedClass"
                   and isinstance(item.get("SuperStruct"), dict)}
        package = next(iter(parents)) if len(parents) == 1 else ""
        if not package.startswith(CHARACTER_ROOT):
            package = ""
    return chain


def _identity(template):
    """Whether a component template keeps its socket transform (no relative offset)."""
    for key, neutral in _OFFSETS.items():
        value = template.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            return False
        numbers = [value.get(axis) for axis in (("Pitch", "Yaw", "Roll") if key == "RelativeRotation" else ("X", "Y", "Z"))]
        if any(type(number) not in (int, float) for number in numbers) or \
                any(abs(number - expected) > 1e-6 for number, expected in zip(numbers, neutral)):
            return False
    return True


def components(client, blueprints, cancel):
    """Socket-attached prop components of the blueprints: variable -> {(mesh, socket)}.

    Components with a relative offset are returned with mesh None so they are reported.
    """
    found, cache = {}, {}
    for blueprint in sorted(blueprints):
        nodes, templates = {}, {}
        # Base first, so derived templates override the meshes they inherit.
        for _package, exports in reversed(_chain(client, blueprint, cache, cancel)):
            for item in exports:
                if not isinstance(item, dict):
                    continue
                properties = item.get("Properties") if isinstance(item.get("Properties"), dict) else {}
                name = item.get("Name")
                if item.get("Type") == "SCS_Node" and isinstance(properties.get("InternalVariableName"), str):
                    nodes[properties["InternalVariableName"]] = properties
                elif item.get("Type") == "SkeletalMeshComponent" and isinstance(name, str) and name.endswith(_TEMPLATE):
                    templates.setdefault(name[:-len(_TEMPLATE)], {}).update(properties)
        for variable, node in nodes.items():
            socket = node.get("AttachToName")
            template = templates.get(variable)
            if (variable.startswith("WeaponCase") or template is None or node.get("ParentComponentOrVariableName") != "CharacterMesh0"
                    or not isinstance(socket, str) or socket in ("", "None")):
                continue
            mesh = template.get("SkeletalMesh")
            mesh = package_name(mesh.get("ObjectPath")) if isinstance(mesh, dict) else ""
            if mesh.startswith(CHARACTER_ROOT):
                found.setdefault(variable, set()).add((mesh if _identity(template) else None, socket))
    return found


def pairs(sequences, clips):
    """Clip id -> prop sequence package for exactly named sequences on the clip's timing.

    ``sequences`` maps packages to (name, frame count, length); ``clips`` maps the
    Asset's clip names to every converted (id, frame count, length) of that name. A
    sequence pairs with each same-named clip it fits: the same frame interval, no
    more frames and no longer.
    """
    found = {}
    for package, (name, frames, length) in sorted(sequences.items()):
        matches = [clip for clip in clips if name == clip or name.startswith(clip + "_")]
        if not matches:
            continue
        for clip_id, clip_frames, clip_length in clips[max(matches, key=len)]:
            if (1 < frames <= clip_frames and 0 < length <= clip_length + 1e-4 and clip_frames > 1
                    and abs(length / (frames - 1) - clip_length / (clip_frames - 1)) <= 1e-4):
                found.setdefault(clip_id, []).append(package)
    return {clip_id: packages[0] for clip_id, packages in found.items() if len(packages) == 1}


def poses(locals_, bones):
    """(frame, bone) component-space matrices of frame-major local transforms
    (xyz translation, xyzw quaternion) on ``bones``, whose parents precede them."""
    q = locals_[..., 3:7] / np.linalg.norm(locals_[..., 3:7], axis=-1, keepdims=True)
    x, y, z, w = np.moveaxis(q, -1, 0)
    local = np.zeros(q.shape[:-1] + (4, 4))
    local[..., 0, :3] = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1)
    local[..., 1, :3] = np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1)
    local[..., 2, :3] = np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)
    local[..., :3, :3] *= np.asarray([bone["scale"] for bone in bones], dtype=float)[:, None, :]
    local[..., :3, 3] = locals_[..., :3]
    local[..., 3, 3] = 1
    world = np.zeros_like(local)
    for index, bone in enumerate(bones):
        if not bone["parent"] < index:
            raise ValueError("The prop skeleton lists a bone before its parent.")
        world[:, index] = local[:, index] if bone["parent"] < 0 else world[:, bone["parent"]] @ local[:, index]
    return world


def convert(client, package, item, bones, cache):
    """(frames, frame-major local transforms) of one prop sequence on the prop mesh's bones.

    A sequence whose skeleton keeps another reference pose plays its authored tracks as
    absolute locals over that skeleton's reference (the pipeline's Animation mode).
    """
    directory = client.work / "prop-animations"
    file = exported_file(client.batch("anim", [package], directory, "psa").get(package), directory, ".psa")
    if file is None:
        raise ValueError("The native tool could not export the prop animation.")
    properties = item["Properties"]
    data = read_limited(file, MAX_ANIMATION_BYTES, "Native animation")
    try:
        frames, binary = convert_clip(data, dict(bones=bones), properties["SequenceLength"])
    except ReferencePoseMismatch:
        skeleton = package_name(properties.get("Skeleton", {}).get("ObjectPath"))
        if skeleton not in cache:
            cache[skeleton] = dict(bones=source_skeleton(client, skeleton, strict_tree=True))
        frames, binary = convert_clip(data, dict(bones=bones), properties["SequenceLength"], reference=cache[skeleton],
                                      authored_bones=[row["BoneTreeIndex"] for row in properties["TrackToSkeletonMapTable"]])
    if frames != properties["NumFrames"]:
        raise ValueError("Prop animation frame count differs from its sequence metadata.")
    return frames, binary


def bake(binary, frames, bones, anchor):
    """``PROP_TRACK_LAYOUT`` bytes: per frame and prop bone, the asset-space transform
    (xyz translation, xyzw quaternion) that moves the bone's rest skin to its pose,
    expressed relative to the prop's placement ``anchor``; a socket's own clip motion
    is applied on top during playback."""
    data = np.frombuffer(binary, "<f4", count=frames * len(bones) * 7).reshape(frames, len(bones), 7).astype(float)
    if not np.isfinite(data).all():
        raise ValueError("The prop animation is invalid.")
    rest = np.asarray(rest_worlds(bones, affine=True))
    delta = anchor @ poses(data, bones) @ np.linalg.inv(rest) @ np.linalg.inv(anchor)
    rotation = delta[..., :3, :3]
    error = np.abs(np.swapaxes(rotation, -1, -2) @ rotation - np.eye(3)).max() if delta.size else 0
    if not error <= 1e-3 or not (np.linalg.det(rotation) > 0).all():
        raise ValueError("The prop animation is not rigid in the viewer's space.")
    track = np.concatenate([delta[..., :3, 3], qfrom_basis(rotation[..., 0], rotation[..., 1], rotation[..., 2])], -1)
    if not np.isfinite(track).all():
        raise ValueError("The prop animation is invalid.")
    return track.astype("<f4").tobytes()


def sequence_items(client, packages, cancel):
    """Package -> slim metadata of each readable, timed prop sequence."""
    directory = client.work / "prop-animation-metadata"
    files = client.batch("json", sorted(packages), directory) if packages else {}
    items = {}
    for package in sorted(packages):
        check_cancelled(cancel)
        file = exported_file(files.get(package), directory, ".json")
        item = sequence(file) if file else None
        properties = item.get("Properties", {}) if item else {}
        if item and type(properties.get("NumFrames")) is int and type(properties.get("SequenceLength")) in (int, float):
            items[package] = item
    return items


def track(client, writer, package, item, bones, anchor, references):
    """Manifest fields of one prop sequence baked at ``anchor`` into a written track."""
    frames, binary = convert(client, package, item, bones, references)
    path, digest = writer.write(bake(binary, frames, bones, anchor), track=True)
    return dict(file=path, sha256=digest, frame_count=frames, duration=float(item["Properties"]["SequenceLength"]))


def skeleton_of(exports):
    """The skeleton package an exported skeletal mesh document declares."""
    meshes = [item for item in exports if isinstance(item, dict) and item.get("Type") == "SkeletalMesh"] \
        if isinstance(exports, list) else []
    properties = meshes[0].get("Properties") if len(meshes) == 1 and isinstance(meshes[0].get("Properties"), dict) else {}
    skeleton = properties.get("Skeleton")
    return package_name(skeleton.get("ObjectPath")) if isinstance(skeleton, dict) else ""


def _tracks(client, mesh, skeleton, folder, clips, anchor, writer, cancel, claimed=frozenset()):
    """Clip id -> written track entry for every paired prop sequence of one prop mesh.

    A (clip id, sequence) pair in ``claimed`` is played by one of the clip's effects instead.
    """
    items = {package: item for package, item in sequence_items(
        client, list_packages(client, "AnimSequence", folder, cancel)[:MAX_SEQUENCES], cancel).items()
        if package_name(item["Properties"].get("Skeleton", {}).get("ObjectPath")) == skeleton}
    sequences = {package: (package.rsplit("/", 1)[-1], item["Properties"]["NumFrames"],
                           float(item["Properties"]["SequenceLength"])) for package, item in items.items()}
    paired = {clip_id: package for clip_id, package in pairs(sequences, clips).items()
              if (clip_id, package) not in claimed}
    tracks, references, baked = {}, {}, {}
    for clip_id, package in sorted(paired.items()):
        check_cancelled(cancel)
        try:
            # A sequence paired with several same-named clips is converted once.
            if package not in baked:
                baked[package] = track(client, writer, package, items[package], mesh["bones"], anchor, references)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            writer.skipped.append(dict(source=package, reason=str(error) if isinstance(error, ValueError)
                                       else "The prop animation is incomplete."))
            continue
        # The prop shows while its animation plays from the clip's start.
        tracks[clip_id] = dict(baked[package], start=0.0, end=baked[package]["duration"])
    return tracks


def extract(client, own, names, worlds, clips, writer, cancel, claimed=frozenset()):
    """Add the blueprint props of the verified mesh's model rows (``own``) to ``writer``.

    ``claimed`` (clip id, sequence) pairs belong to the clips' effects and never pair.
    """
    found = components(client, {row["blueprint"] for row in own.values() if row["blueprint"]}, cancel)
    for variable, choices in sorted(found.items())[:MAX_COMPONENTS]:
        check_cancelled(cancel)
        if len(choices) != 1:
            writer.skipped.append(dict(kind="blueprint", source=variable, reason="Different blueprints declare the component differently."))
            continue
        package, socket = next(iter(choices))
        try:
            if package is None:
                raise ValueError("Components with their own offset from the socket are not supported.")
            if names.count(socket) != 1:
                raise ValueError("The prop socket is not a unique bone of the verified rig.")
            anchor = worlds[names.index(socket)]
            mesh, exports, parts = mesh_parts(client, package, anchor)
            if not 0 < len(mesh["bones"]) <= MAX_PROP_BONES:
                raise ValueError("The prop skeleton is unsupported.")
            folder = package.split("/Model/", 1)[0] if "/Model/" in package else package.rsplit("/", 1)[0]
            tracks = _tracks(client, mesh, skeleton_of(exports), folder, clips, anchor, writer, cancel, claimed)
            if not tracks:
                raise ValueError("No clip has a paired animation for the prop.")
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            writer.skipped.append(dict(kind="blueprint", source=package or variable,
                                       reason=str(error) if isinstance(error, ValueError) else "The prop could not be read."))
            continue
        writer.add(package, parts, socket=names.index(socket), bone_count=len(mesh["bones"]), tracks=tracks)
