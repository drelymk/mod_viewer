"""Convert CUE4Parse ActorX samples to the viewer's Asset-coordinate tracks."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app.assets.rig import validate_rig  # noqa: E402


from app.assets.animation_conversion import (  # noqa: E402, F401
    convert_clip,
    apply_layer,
    merge_reference_layer,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rig", type=Path)
    parser.add_argument("samples", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--layers",
        type=Path,
        help="Manifest of verified, reference-pose additive layers and bone masks.",
    )
    args = parser.parse_args()
    rig = validate_rig(json.loads(args.rig.read_text(encoding="utf-8-sig")))
    source = json.loads(
        (args.samples / "manifest.json").read_text(encoding="utf-8-sig")
    )
    converted, entries = {}, []
    layers = {}
    if args.layers:
        for layer in json.loads(args.layers.read_text(encoding="utf-8-sig"))["layers"]:
            if layer["clip"] in layers:
                raise ValueError("Duplicate layer target.")
            layers[layer["clip"]] = layer
        if not set(layers) <= {clip["name"] for clip in source["clips"]}:
            raise ValueError("Layer target is absent from the sample manifest.")
    for entry in source["clips"]:
        name = entry["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError("Invalid clip name.")
        data = (args.samples / f"{name}.psa").read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["psa_sha256"]:
            raise ValueError("Source clip checksum differs.")
        frames, binary = convert_clip(data, rig, entry["duration_seconds"])
        if name in layers:
            binary = apply_layer(
                binary,
                rig,
                frames,
                entry["duration_seconds"],
                layers[name],
                args.layers.parent,
            )
        metadata = dict(
            format="mod-viewer-animation",
            version=1,
            coordinate_space="asset",
            layout="frame-major-tq-f32le",
            skeleton_id=rig["id"],
            frame_count=frames,
            bone_count=len(rig["bones"]),
            duration=entry["duration_seconds"],
            data_file=f"{name}.bin",
            sha256=hashlib.sha256(binary).hexdigest(),
        )
        if name in layers:
            metadata["reference_layer"] = dict(
                sha256=layers[name]["sha256"], bone_ids=layers[name]["bone_ids"]
            )
        converted[f"{name}.bin"] = binary
        converted[f"{name}.json"] = (json.dumps(metadata, indent=2) + "\n").encode()
        entries.append(
            dict(id=name, name=name, file=f"{name}.json", duration=metadata["duration"])
        )
    manifest = dict(
        format="mod-viewer-animations", version=1, skeleton_id=rig["id"], clips=entries
    )
    converted["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in converted.items():
        (args.output / name).write_bytes(data)
    print(f"Converted {len(entries)} clips for {len(rig['bones'])} bones.")


if __name__ == "__main__":
    main()
