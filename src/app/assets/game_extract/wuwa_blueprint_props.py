"""Animated Wuthering Waves blueprint props, shown while their paired clips play.

A character blueprint can declare extra skeletal mesh components on its construction
script (an ``SCS_Node`` whose parent is ``CharacterMesh0`` and whose ``AttachToName``
names a socket); the character's notifies show them while the game plays the prop's
own animation, such as a character's scissors during attacks. A derived blueprint may
override a component's template (its mesh). Weapon cases are excluded: they hold the
regular weapon, which is not exported. Only components without their own relative offset
(beyond float noise) are supported.

The prop's animation for a clip is the prop sequence in the prop mesh's folder, on the
prop's own skeleton, named after the clip exactly or with a ``_<suffix>`` (the longest
matching clip name wins), sampled at the same frame interval and no longer than the
clip, since the prop plays from the clip's start. A clip paired with two sequences is
never guessed between, except by the component's own animation blueprint: the
template's ``AnimClass`` lists the sequences it plays in keyed maps (clip name ->
sequence). When exactly one of the clip's candidates is among them it is the pair,
provided the clip's own submesh control notify shows that component (``MeshName``),
since sibling components of one mesh (left and right hand) each play their own side. A
sequence only a sibling component's blueprint lists is never this component's, and a
clip no sequence pairs with by name pairs through the exact key it is listed under
(``SpDodge_2`` -> ``SpDodge2``) unless a sibling lists the same key and the notifies do
not name this component. Components without a readable map pair by name alone. A
sequence one of the clip's own effects plays belongs to that effect (see
wuwa_effect_props), never to the component. Intentional exception to name-free proof,
like ribbon layers' ``Rib_`` names: the game pairs them in its animation blueprints.
Each pair is baked
into per-frame prop bone transforms relative to the socket (``prop-track-v1``, or
``prop-track-v2`` affine rows when the prop scales: the game hides props by scaling a bone
to zero), and a prop shows only while its paired animation runs.
"""

import numpy as np

from app.assets.animation_conversion import ReferencePoseMismatch, convert_clip
from app.assets.animations import MAX_PROP_BONES, PROP_AFFINE_TRACK_LAYOUT
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
# Offsets this small (cm, degrees, scale) are editor float noise and count as none.
_NEGLIGIBLE = 1e-3


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
    """Whether a component template keeps its socket transform (no relative offset beyond float noise)."""
    for key, neutral in _OFFSETS.items():
        value = template.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            return False
        numbers = [value.get(axis) for axis in (("Pitch", "Yaw", "Roll") if key == "RelativeRotation" else ("X", "Y", "Z"))]
        if any(type(number) not in (int, float) for number in numbers) or \
                any(abs(number - expected) > _NEGLIGIBLE for number, expected in zip(numbers, neutral)):
            return False
    return True


def components(client, blueprints, cancel, anims=None):
    """Socket-attached prop components of the blueprints: variable -> {(mesh, socket)}.

    Components with a relative offset are returned with mesh None so they are reported.
    When ``anims`` is a dict it receives variable -> {animation blueprint package} (the
    template's most derived ``AnimClass``, absent when none), kept apart from the choices
    so a differing animation blueprint never makes a component ambiguous.
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
                anim = template.get("AnimClass")
                anim = package_name(anim.get("ObjectPath")) if isinstance(anim, dict) else ""
                if anims is not None and anim.startswith(CHARACTER_ROOT):
                    anims.setdefault(variable, set()).add(anim)
    return found


def animation_map(client, package, cache, cancel):
    """{key: sequence packages} an animation blueprint lists in its keyed maps, or None.

    The game's animation blueprints map a clip name (or a state name) to the
    ``AnimSequence`` the prop plays for it (``{"Key": name, "Value": AnimSequence}``).
    Nothing here is validated against the Asset's clips. An unreadable blueprint or one
    without such a map gives None.
    """
    check_cancelled(cancel)
    if package not in cache:
        try:
            exports = document(client.export("json", package, client.work / "blueprints")[0], 16 * 1024 * 1024)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError):
            exports = None
        found = {}

        def walk(node, depth=0):
            if depth > 64:
                return
            if isinstance(node, dict):
                value = node.get("Value")
                if (isinstance(node.get("Key"), str) and isinstance(value, dict)
                        and str(value.get("ObjectName", "")).startswith("AnimSequence'")):
                    path = package_name(value.get("ObjectPath"))
                    if path:
                        found.setdefault(node["Key"], set()).add(path)
                for child in node.values():
                    walk(child, depth + 1)
            elif isinstance(node, list):
                for child in node:
                    walk(child, depth + 1)

        walk(exports)
        cache[package] = {key: frozenset(packages) for key, packages in found.items()} or None
    return cache[package]


def _fits(frames, length, clip_frames, clip_length):
    """Whether a prop sequence plays from a clip's start on its frame interval: no more
    frames and no longer than the clip."""
    return (1 < frames <= clip_frames and 0 < length <= clip_length + 1e-4 and clip_frames > 1
            and abs(length / (frames - 1) - clip_length / (clip_frames - 1)) <= 1e-4)


def pairs(sequences, clips, preferred=None):
    """Clip id -> prop sequence package for exactly named sequences on the clip's timing.

    ``sequences`` maps packages to (name, frame count, length); ``clips`` maps the
    Asset's clip names to every converted (id, frame count, length) of that name. A
    sequence pairs with each same-named clip it fits: the same frame interval, no
    more frames and no longer. A clip several sequences fit pairs only when exactly one
    of them is in ``preferred`` (the sequences the component's own animation blueprint
    plays, so the name rule still decides everything it decided without it).
    """
    found = {}
    for package, (name, frames, length) in sorted(sequences.items()):
        matches = [clip for clip in clips if name == clip or name.startswith(clip + "_")]
        if not matches:
            continue
        for clip_id, clip_frames, clip_length in clips[max(matches, key=len)]:
            if _fits(frames, length, clip_frames, clip_length):
                found.setdefault(clip_id, []).append(package)
    if preferred:
        found = {clip_id: [package for package in packages if package in preferred] if len(packages) > 1 else packages
                 for clip_id, packages in found.items()}
    return {clip_id: packages[0] for clip_id, packages in found.items() if len(packages) == 1}


def poses(locals_, bones):
    """(frame, bone) component-space matrices of frame-major local transforms
    (xyz translation, xyzw quaternion, optionally xyz scale; otherwise each bone's rest
    scale) on ``bones``, whose parents precede them."""
    q = locals_[..., 3:7] / np.linalg.norm(locals_[..., 3:7], axis=-1, keepdims=True)
    x, y, z, w = np.moveaxis(q, -1, 0)
    local = np.zeros(q.shape[:-1] + (4, 4))
    local[..., 0, :3] = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1)
    local[..., 1, :3] = np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1)
    local[..., 2, :3] = np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)
    local[..., :3, :3] *= (locals_[..., None, 7:10] if locals_.shape[-1] == 10
                           else np.asarray([bone["scale"] for bone in bones], dtype=float)[:, None, :])
    local[..., :3, 3] = locals_[..., :3]
    local[..., 3, 3] = 1
    world = np.zeros_like(local)
    for index, bone in enumerate(bones):
        if not bone["parent"] < index:
            raise ValueError("The prop skeleton lists a bone before its parent.")
        world[:, index] = local[:, index] if bone["parent"] < 0 else world[:, bone["parent"]] @ local[:, index]
    return world


def convert(client, package, item, bones, cache):
    """(frames, frame-major local transforms with xyz scale, 10 floats per bone) of one prop
    sequence on the prop mesh's bones.

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
        frames, binary = convert_clip(data, dict(bones=bones), properties["SequenceLength"], scales=True)
    except ReferencePoseMismatch:
        skeleton = package_name(properties.get("Skeleton", {}).get("ObjectPath"))
        if skeleton not in cache:
            cache[skeleton] = dict(bones=source_skeleton(client, skeleton, strict_tree=True))
        frames, binary = convert_clip(data, dict(bones=bones), properties["SequenceLength"], reference=cache[skeleton],
                                      authored_bones=[row["BoneTreeIndex"] for row in properties["TrackToSkeletonMapTable"]],
                                      scales=True)
    if frames != properties["NumFrames"]:
        raise ValueError("Prop animation frame count differs from its sequence metadata.")
    return frames, binary


def _deltas(binary, frames, bones, anchor, offsets=None):
    """(frames, bones, 4, 4) asset-space transforms that move each prop bone's rest skin to its
    pose, relative to the prop's placement ``anchor``; ``offsets`` ((frames, 4, 4) matrices in
    the placement's frame) move the whole pose first. A socket's own clip motion is applied
    on top during playback."""
    data = np.frombuffer(binary, "<f4", count=frames * len(bones) * 10).reshape(frames, len(bones), 10).astype(float)
    if not np.isfinite(data).all():
        raise ValueError("The prop animation is invalid.")
    rest = np.asarray(rest_worlds(bones, affine=True))
    pose = poses(data, bones)
    if offsets is not None:
        pose = np.asarray(offsets, dtype=float)[:, None] @ pose
    delta = anchor @ pose @ np.linalg.inv(rest) @ np.linalg.inv(anchor)
    if not (np.isfinite(delta).all() and np.abs(delta).max() <= 1e6):
        raise ValueError("The prop animation is invalid.")
    return delta


def _rigid(delta):
    """Whether every transform only rotates and moves, so a ``prop-track-v1`` track keeps it."""
    rotation = delta[..., :3, :3]
    error = np.abs(np.swapaxes(rotation, -1, -2) @ rotation - np.eye(3)).max() if delta.size else 0
    return bool(error <= 1e-3 and (np.linalg.det(rotation) > 0).all())


def _pack(delta, affine):
    """Track bytes of :func:`_deltas` output: rigid ``PROP_TRACK_LAYOUT`` records (xyz translation,
    xyzw quaternion), or with ``affine`` ``PROP_AFFINE_TRACK_LAYOUT`` 3x4 rows, which may scale."""
    if affine:
        return delta[..., :3, :].astype("<f4").tobytes()
    if not _rigid(delta):
        raise ValueError("The prop animation is not rigid in the viewer's space.")
    rotation = delta[..., :3, :3]
    track = np.concatenate([delta[..., :3, 3], qfrom_basis(rotation[..., 0], rotation[..., 1], rotation[..., 2])], -1)
    if not np.isfinite(track).all():
        raise ValueError("The prop animation is invalid.")
    return track.astype("<f4").tobytes()


def bake(binary, frames, bones, anchor, affine=False, offsets=None):
    """Track bytes of one converted prop sequence (see :func:`_deltas` and :func:`_pack`)."""
    return _pack(_deltas(binary, frames, bones, anchor, offsets), affine)


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


def prepare(client, package, item, bones, references):
    """(frames, local transforms with scale, duration) of one prop sequence."""
    frames, binary = convert(client, package, item, bones, references)
    return frames, binary, float(item["Properties"]["SequenceLength"])


def write_tracks(writer, prepared, bones, anchor):
    """(package -> track entry, package -> reason it failed, extra prop manifest fields) of one
    prop's prepared sequences, each (frames, local transforms, duration, offsets or None). A prop any of whose tracks scales (bone scale keys or offsets)
    bakes every track as ``prop-track-v2`` affine rows (the viewer reads one layout per prop);
    others stay rigid."""
    deltas, failures = {}, {}
    for package, (frames, binary, _duration, offsets) in sorted(prepared.items()):
        try:
            deltas[package] = _deltas(binary, frames, bones, anchor, offsets)
        except ValueError as error:
            failures[package] = str(error)
    affine = not all(_rigid(delta) for delta in deltas.values())
    entries = {}
    for package, delta in deltas.items():
        frames, _binary, duration, _offsets = prepared[package]
        try:
            path, digest = writer.write(_pack(delta, affine), track=True)
        except ValueError as error:
            failures[package] = str(error)
            continue
        entries[package] = dict(file=path, sha256=digest, frame_count=frames, duration=duration)
    return entries, failures, (dict(track_layout=PROP_AFFINE_TRACK_LAYOUT) if affine else {})


def skeleton_of(exports):
    """The skeleton package an exported skeletal mesh document declares."""
    meshes = [item for item in exports if isinstance(item, dict) and item.get("Type") == "SkeletalMesh"] \
        if isinstance(exports, list) else []
    properties = meshes[0].get("Properties") if len(meshes) == 1 and isinstance(meshes[0].get("Properties"), dict) else {}
    skeleton = properties.get("Skeleton")
    return package_name(skeleton.get("ObjectPath")) if isinstance(skeleton, dict) else ""


def prop_folder(mesh):
    """The folder holding a prop mesh's animation sequences."""
    return mesh.split("/Model/", 1)[0] if "/Model/" in mesh else mesh.rsplit("/", 1)[0]


def ownership(variable, folders, maps):
    """What one component's animation blueprint says about its prop folder's sequences.

    ``folders`` maps components to their prop folder and ``maps`` to their
    ``animation_map`` (None without one). Returns None when the component has no map,
    else ``plays`` (its own key map), ``foreign`` (sequences a sibling component of the
    same folder lists and this one does not: they belong to the sibling) and
    ``rivals`` (the keys siblings list).
    """
    plays = maps.get(variable)
    if not plays:
        return None
    siblings = [maps[other] for other in maps if other != variable and maps[other] and folders.get(other) == folders.get(variable)]
    own = frozenset().union(*plays.values())
    return dict(plays=plays, foreign=frozenset().union(*(frozenset().union(*sibling.values()) for sibling in siblings)) - own,
                rivals=frozenset().union(*(frozenset(sibling) for sibling in siblings)))


def _tracks(client, mesh, skeleton, folder, clips, anchor, writer, cancel, claimed=frozenset(), owner=None,
            component=None, shown=None):
    """(clip id -> written track entry, manifest fields) for every paired prop sequence of one
    prop mesh. Sequences are prepared once each, then written together with one layout.

    A (clip id, sequence) pair in ``claimed`` is played by one of the clip's effects instead.
    ``owner`` (see ``ownership``) and ``shown`` (clip id -> component names its notifies
    show) apply the animation blueprint rules of the module docstring to ``component``.
    """
    items = {package: item for package, item in sequence_items(
        client, list_packages(client, "AnimSequence", folder, cancel)[:MAX_SEQUENCES], cancel).items()
        if package_name(item["Properties"].get("Skeleton", {}).get("ObjectPath")) == skeleton}
    sequences = {package: (package.rsplit("/", 1)[-1], item["Properties"]["NumFrames"],
                           float(item["Properties"]["SequenceLength"])) for package, item in items.items()}
    owner = owner or {}
    plays, shown = owner.get("plays") or {}, shown or {}
    preferred = frozenset().union(*plays.values()) if plays else None
    by_name = pairs(sequences, clips)
    chosen = pairs(sequences, clips, preferred) if preferred else by_name
    paired = {clip_id: package for clip_id, package in chosen.items()
              if (clip_id, package) not in claimed
              and (clip_id in by_name or component in shown.get(clip_id, ()))}
    for name, entries in sorted(clips.items()):
        keyed = plays.get(name, ())
        package = next(iter(keyed)) if len(keyed) == 1 else None
        if package not in sequences:
            continue
        _name, frames, length = sequences[package]
        for clip_id, clip_frames, clip_length in entries:
            visible = shown.get(clip_id, ())
            if (clip_id not in chosen and (clip_id, package) not in claimed
                    and _fits(frames, length, clip_frames, clip_length)
                    and (component in visible or (not visible and name not in owner.get("rivals", ())))):
                paired[clip_id] = package
    paired = {clip_id: package for clip_id, package in paired.items() if package not in owner.get("foreign", ())}
    references, prepared, attempted = {}, {}, set()
    for clip_id, package in sorted(paired.items()):
        check_cancelled(cancel)
        # A sequence paired with several same-named clips is converted once, and a failure is reported once.
        if package in attempted:
            continue
        attempted.add(package)
        try:
            prepared[package] = prepare(client, package, items[package], mesh["bones"], references) + (None,)
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError) as error:
            writer.skipped.append(dict(source=package, reason=str(error) if isinstance(error, ValueError)
                                       else "The prop animation is incomplete."))
    entries, failures, extra = write_tracks(writer, prepared, mesh["bones"], anchor)
    for package, reason in sorted(failures.items()):
        writer.skipped.append(dict(source=package, reason=reason))
    # The prop shows while its animation plays from the clip's start.
    tracks = {clip_id: dict(entries[package], start=0.0, end=entries[package]["duration"])
              for clip_id, package in sorted(paired.items()) if package in entries}
    return tracks, extra


def extract(client, own, names, worlds, clips, writer, cancel, claimed=frozenset(), shown=None):
    """Add the blueprint props of the verified mesh's model rows (``own``) to ``writer``.

    ``claimed`` (clip id, sequence) pairs belong to the clips' effects and never pair.
    ``shown`` maps clip ids to the component names their submesh notifies show.
    """
    anims, abp_cache = {}, {}
    found = components(client, {row["blueprint"] for row in own.values() if row["blueprint"]}, cancel, anims)
    # What each component's animation blueprint plays (only a component with exactly one
    # can say), and its prop folder, so siblings on one mesh can tell their sides apart.
    folders, maps = {}, {}
    for variable, choices in sorted(found.items())[:MAX_COMPONENTS]:
        package = next(iter(choices))[0] if len(choices) == 1 else None
        blueprints = anims.get(variable, ())
        if package and len(blueprints) == 1:
            folders[variable] = prop_folder(package)
            maps[variable] = animation_map(client, next(iter(blueprints)), abp_cache, cancel)
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
            tracks, extra = _tracks(client, mesh, skeleton_of(exports), prop_folder(package), clips, anchor, writer, cancel, claimed,
                                    ownership(variable, folders, maps), variable, shown)
            if not tracks:
                raise ValueError("No clip has a paired animation for the prop.")
        except ExtractionCancelled:
            raise
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            writer.skipped.append(dict(kind="blueprint", source=package or variable,
                                       reason=str(error) if isinstance(error, ValueError) else "The prop could not be read."))
            continue
        writer.add(package, parts, socket=names.index(socket), bone_count=len(mesh["bones"]), tracks=tracks, **extra)
