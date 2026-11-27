"""Bake a character's own ribbon layers for the shared body-type clips it can play.

Shared libraries hold body motion once per game skeleton; ribbon and accessory
motion is authored per character as reference-pose layers
(``<skin>/RibbonAnim*/Rib_<clip>``) on the character's own skeleton. Extraction
pairs a layer with every proven shared clip of the same sequence name and
identical frame count and duration, and stores only the layer's tracks for
bones the shared clip leaves unmapped, in the character's Animation folder.
"""

import hashlib
import json
import math
import shutil

import numpy as np

from app.assets import paths, shared_animations
from . import pipeline
from .native import read_limited
from .runtime import check_cancelled


DIRECTORY = "SharedLayers"
LAYER_FOLDERS = ("RibbonAnim", "RibbonAnim3")
MAX_LAYER_BYTES = 1024 ** 3


def _clip_sources(library):
    """Each admitted clip's source sequence name, frame count and duration from its metadata."""
    result = []
    for clip in library["clips"]:
        file = paths.safe_asset_path(library["directory"], clip["file"])
        try:
            with open(file, "rb") as stream:
                raw = json.loads(stream.read(1024 * 1024))
            source = raw.get("source")
            frames, duration = raw.get("frame_count"), raw.get("duration")
            if (isinstance(source, str) and "/" in source and type(frames) is int
                    and type(duration) in (int, float) and math.isfinite(duration)):
                result.append((clip, source.rsplit("/", 1)[1], frames, duration))
        except (OSError, TypeError, ValueError):
            continue
    return result


def _is_layer(item, frames, duration):
    properties = item["Properties"]
    length = properties.get("SequenceLength")
    return (properties.get("AdditiveAnimType") == "AAT_LocalSpaceBase" and properties.get("RefPoseType") == "ABPT_RefPose"
            and properties.get("NumFrames") == frames and type(length) in (int, float)
            and math.isfinite(length) and abs(length - duration) <= 0.00001
            and isinstance(properties.get("TrackToSkeletonMapTable"), list))


def extract(client, root, rig, sequences, skin_root, convert, library_dir, reject, cancel):
    """Write paired layers; returns (manifest entries, their distinct bone masks, every baked layer bone)."""
    shutil.rmtree(library_dir / DIRECTORY, ignore_errors=True)
    pairs = []
    # Unreal package names are case-insensitive; names differing only in case stay ambiguous.
    named = pipeline.folded_paths(sequences)
    for library in shared_animations.discover(str(root), rig):
        targets = set(library["mapping"].values())
        for clip, name, frames, duration in _clip_sources(library):
            check_cancelled(cancel)
            candidates = [path for folder in LAYER_FOLDERS for path in named.get(f"{skin_root}/{folder}/Rib_{name}".casefold(), [])
                          if _is_layer(sequences[path], frames, duration)]
            if len(candidates) > 1:
                reject("shared-layer", candidates[0], "Several ribbon layers match this shared clip's timing.",
                       f"{library['name']}/{clip['id']}")
            if len(candidates) == 1:
                pairs.append((library, clip, candidates[0], frames, duration, targets))
    if not pairs:
        return [], [], set()
    layer_paths = sorted({pair[2] for pair in pairs})
    work = client.work / "shared-layer-tracks"
    tracks = client.batch("anim", layer_paths, work, "psa")
    converted = {}
    for path in layer_paths:
        check_cancelled(cancel)
        file = pipeline.exported_file(tracks.get(path), work, ".psa")
        try:
            if file is None:
                raise ValueError("Secondary clip is unavailable.")
            properties = sequences[path]["Properties"]
            layer_frames, binary = convert(read_limited(file, pipeline.MAX_ANIMATION_BYTES, "Native animation"),
                                           sequences[path], properties["SequenceLength"])
            if layer_frames != properties["NumFrames"]:
                raise ValueError("Secondary clip frame count differs from its sequence metadata.")
            ids = {row["BoneTreeIndex"] for row in properties["TrackToSkeletonMapTable"]}
            if any(type(bone) is not int or not 0 <= bone < len(rig["bones"]) for bone in ids):
                raise ValueError("Secondary clip authored bone mask is invalid.")
            converted[path] = (np.frombuffer(binary, "<f4").reshape(layer_frames, len(rig["bones"]), 7), ids)
        except (ValueError, KeyError) as error:
            reject("shared-layer", path, str(error) if isinstance(error, ValueError) else "Secondary clip data is incomplete.")
        finally:
            if file is not None:
                file.unlink(missing_ok=True)
    output = library_dir / DIRECTORY
    entries, masks, baked, written, total = [], [], set(), set(), 0
    for library, clip, path, frames, duration, targets in pairs:
        if path not in converted:
            continue
        rows, ids = converted[path]
        # Only tracks the shared clip leaves to the rig's rest pose; mapped joints keep the body motion.
        mask = sorted(ids - targets)
        if not mask:
            reject("shared-layer", path, "Secondary clip has no tracks outside the shared body.", f"{library['name']}/{clip['id']}")
            continue
        data = np.ascontiguousarray(rows[:, mask]).tobytes()
        digest = hashlib.sha256(data).hexdigest()
        file = f"{DIRECTORY}/{digest[:32]}.bin"
        if digest not in written:
            total += len(data)
            if total > MAX_LAYER_BYTES:
                raise ValueError("The shared clip ribbon layers exceed their size limit.")
            output.mkdir(exist_ok=True)
            (library_dir / file).write_bytes(data)
            written.add(digest)
        if mask not in masks:
            masks.append(mask)
        entries.append(dict(library=library["name"], fingerprint=library["fingerprint"], clip=clip["id"],
                            file=file, sha256=digest, frame_count=frames, mask=masks.index(mask), source=path))
        baked.update(mask)
    return entries, masks, baked
