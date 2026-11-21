"""Index-stage extraction of WuWa body-type animation sets into shared libraries.

Locomotion lives once per body type (``Role/<BodyType>/BaseAnim*``), authored on
a body-type skeleton rather than each character's. Each set and source skeleton
becomes one library under ``<Asset Folder>/SharedAnimation``, converted once in
that skeleton's space and reused until its game metadata changes. Playback maps
it onto every compatible rig (see ``app.assets.shared_animations``).
"""

import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

from app.assets import shared_animations
from app.assets.animation_conversion import convert_clip
from app.assets.animations import read_clip
from . import pipeline
from .native import read_limited
from .runtime import check_cancelled, list_packages


SET_FOLDER = re.compile(r"BaseAnim[0-9]{0,2}")
# Bump when conversion output changes so existing libraries are rebuilt.
CONVERTER = 1
BATCH_SIZE = 40
METADATA_BATCH = 250
MAX_LIBRARY_BYTES = 2 * 1024 ** 3


def _sets(client, cancel):
    for body in client.folders(pipeline.ROLE_ROOT):
        check_cancelled(cancel)
        for name in client.folders(pipeline.ROLE_ROOT + "/" + body):
            if SET_FOLDER.fullmatch(name):
                yield body, name


def _metadata(client, packages, directory, cancel):
    """Slim base-clip metadata grouped by source skeleton, with a digest of each package's metadata."""
    groups, digests = {}, {}
    for start in range(0, len(packages), METADATA_BATCH):
        chunk = packages[start:start + METADATA_BATCH]
        files = client.batch("json", chunk, directory)
        for package in chunk:
            check_cancelled(cancel)
            file = pipeline.exported_file(files.get(package), directory, ".json")
            item = pipeline.sequence(file) if file else None
            # Additive and incomplete sequences are not playable clips.
            skeleton = pipeline.package_name(item["Properties"].get("Skeleton", {}).get("ObjectPath")) if item else ""
            if not skeleton or not pipeline.is_base_clip(item):
                continue
            with open(file, "rb") as stream:
                digests[package] = hashlib.file_digest(stream, "sha256").hexdigest()
            groups.setdefault(skeleton, {})[package] = item
    return groups, digests


def _owner(skeleton, body):
    prefix = pipeline.ROLE_ROOT + "/" + body + "/"
    return skeleton[len(prefix):].split("/", 1)[0] if skeleton.startswith(prefix) else skeleton.rsplit("/", 1)[-1]


def _current(directory, fingerprint):
    """Whether a valid library from this exact game metadata is already installed."""
    try:
        return shared_animations.read_library(str(directory))["fingerprint"] == fingerprint
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _convert(client, plan, output, progress, done, total, cancel):
    """Convert one set/skeleton group into ``output``; returns whether any clip converted."""
    bones, sid = plan["bones"], plan["skeleton_id"]
    source = dict(id=sid, bones=bones)
    used, clips, skipped = set(), [], []
    packages = sorted(plan["items"])
    tracks_dir = client.work / "shared-tracks"
    for start in range(0, len(packages), BATCH_SIZE):
        chunk = packages[start:start + BATCH_SIZE]
        tracks = client.batch("anim", chunk, tracks_dir, "psa")
        for package in chunk:
            check_cancelled(cancel)
            progress("shared", done[0], total)
            done[0] += 1
            item = plan["items"][package]
            properties = item["Properties"]
            file = pipeline.exported_file(tracks.get(package), tracks_dir, ".psa")
            try:
                if file is None:
                    raise ValueError("The native tool could not export the animation tracks.")
                ids = [row["BoneTreeIndex"] for row in properties["TrackToSkeletonMapTable"]]
                if any(type(bone) is not int or not 0 <= bone < len(bones) for bone in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Animation authored bone map is invalid.")
                duration = properties["SequenceLength"]
                frames, binary = convert_clip(read_limited(file, pipeline.MAX_ANIMATION_BYTES, "Native animation"), source, duration)
                if frames != properties["NumFrames"]:
                    raise ValueError("Animation frame count differs from its sequence metadata.")
            except (ValueError, KeyError) as error:
                skipped.append(dict(source=package, reason=str(error) if isinstance(error, ValueError) else "Animation data is incomplete."))
                continue
            finally:
                if file is not None:
                    file.unlink(missing_ok=True)
            key = pipeline.clip_id(item["Name"], package, used)
            digest = hashlib.sha256(binary).hexdigest()
            metadata = dict(format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
                            skeleton_id=sid, frame_count=frames, bone_count=len(bones), duration=duration,
                            data_file=key + ".bin", sha256=digest, source=package, keyed_bones=sorted(ids))
            (output / (key + ".bin")).write_bytes(binary)
            pipeline.write_document(output / (key + ".json"), metadata, 1024 * 1024)
            label = item["Name"] if package.count("/") == plan["folder"].count("/") + 1 else (
                package[len(plan["folder"]) + 1:].rsplit("/", 1)[0] + " / " + item["Name"])
            entry = dict(id=key, name=label[:128], file=key + ".json", duration=duration, keyed_bones=sorted(ids))
            read_clip(str(output), entry, source)
            clips.append(entry)
    if not clips:
        return False
    if sum(file.stat().st_size for file in output.iterdir() if file.is_file()) > MAX_LIBRARY_BYTES:
        raise ValueError("A shared animation library exceeds its size limit.")
    manifest = dict(format=shared_animations.FORMAT, version=shared_animations.VERSION, game="wuwa",
                    body=plan["body"], set=plan["set"], label=plan["label"], fingerprint=plan["fingerprint"],
                    skeleton_package=plan["skeleton"], skeleton_id=sid, skeleton=bones, clips=clips, skipped=skipped)
    pipeline.write_document(output / "manifest.json", manifest, shared_animations.MAX_MANIFEST_BYTES)
    shared_animations.read_library(str(output))
    return True


def _shared_root(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("A WWMI Asset Folder is unavailable.")
    shared = root / shared_animations.DIRECTORY
    if shared.is_symlink() or (shared.exists() and not shared.is_dir()):
        raise ValueError("The shared animation path must be an ordinary directory.")
    shared.mkdir(exist_ok=True)
    if not shared.resolve().is_relative_to(root.resolve()):
        raise ValueError("The shared animation directory escapes its Asset Folder.")
    return shared


def _install(shared, name, source):
    """Atomically place a converted library, replacing an outdated one of the same name."""
    stage = shared / (".staging-" + uuid.uuid4().hex)
    retired = None
    try:
        shutil.copytree(source, stage)
        target = shared / name
        if target.exists():
            if target.is_symlink() or not target.is_dir():
                raise ValueError("A shared animation library path is not an ordinary directory.")
            retired = shared / (".retired-" + uuid.uuid4().hex)
            target.rename(retired)
        try:
            stage.rename(target)
        except OSError:
            if retired is not None:
                retired.rename(target)
                retired = None
            raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)


def _retire_outdated(shared, current):
    """Remove generated libraries of a rebuilt set/skeleton that no longer exist in the game."""
    for directory in shared.iterdir():
        if directory.name in current or not shared_animations.LIBRARY_NAME.fullmatch(directory.name):
            continue
        try:
            raw = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not directory.is_symlink() and isinstance(raw, dict) and raw.get("format") == shared_animations.FORMAT
                and raw.get("game") == "wuwa" and (raw.get("body"), raw.get("set")) in current.values()):
            retired = shared / (".retired-" + uuid.uuid4().hex)
            directory.rename(retired)
            shutil.rmtree(retired, ignore_errors=True)


def build(client, roots, progress, cancel):
    """Extract every body-type set once and install it into each WWMI Asset Folder root."""
    shared_dirs = [_shared_root(root) for root in roots]
    if not shared_dirs:
        return
    for shared in shared_dirs:
        # Only one index job runs at a time; these are leftovers of an interrupted one.
        for leftover in shared.iterdir():
            if leftover.name.startswith((".staging-", ".retired-")) and not leftover.is_symlink():
                shutil.rmtree(leftover, ignore_errors=True)
    progress("shared", 0, 0)
    plans = []
    for body, name in _sets(client, cancel):
        folder = pipeline.ROLE_ROOT + "/" + body + "/" + name
        packages = sorted(dict.fromkeys(list_packages(client, "AnimSequence", folder, cancel)))
        if not packages:
            continue
        metadata_dir = client.work / "shared-metadata" / body / name
        groups, digests = _metadata(client, packages, metadata_dir, cancel)
        # Long clips carry large curve exports; keep the native work directory within its budget.
        shutil.rmtree(metadata_dir, ignore_errors=True)
        for skeleton, items in sorted(groups.items()):
            check_cancelled(cancel)
            try:
                bones = shared_animations.validate_skeleton(pipeline.source_skeleton(client, skeleton))
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            sid = shared_animations.skeleton_id(bones)
            fingerprint = hashlib.sha256(json.dumps(
                [CONVERTER, sid, sorted((package, digests[package]) for package in items)]).encode()).hexdigest()
            label = name if len(groups) == 1 else f"{name} ({_owner(skeleton, body)})"
            # Named by the skeleton package so a game update replaces the same library.
            library = f"{body}-{name}-{hashlib.sha256(skeleton.encode()).hexdigest()[:10]}"
            if not shared_animations.LIBRARY_NAME.fullmatch(library):
                continue
            plans.append(dict(body=body, set=name, folder=folder, skeleton=skeleton, bones=bones, skeleton_id=sid,
                              label=label[:64], fingerprint=fingerprint, library=library, items=items))
    pending = [(plan, [shared for shared in shared_dirs if not _current(shared / plan["library"], plan["fingerprint"])])
               for plan in plans]
    pending = [(plan, targets) for plan, targets in pending if targets]
    total = sum(len(plan["items"]) for plan, _ in pending)
    done = [0]
    staging = client.work / "shared-libraries"
    for plan, targets in pending:
        output = staging / plan["library"]
        output.mkdir(parents=True)
        if _convert(client, plan, output, progress, done, total, cancel):
            for shared in targets:
                check_cancelled(cancel)
                _install(shared, plan["library"], output)
        shutil.rmtree(output, ignore_errors=True)
    current = {plan["library"]: (plan["body"], plan["set"]) for plan in plans}
    for shared in shared_dirs:
        _retire_outdated(shared, current)
    progress("shared", total, total)
