"""Convert a verified source-skeleton/component binding into an Asset Rig.json."""

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.assets.rig import validate_rig  # noqa: E402


def convert(
    binding, skin_bindings=(), humanoid_controls=None, wwmi_merged_palette=None
):
    skeleton = binding["skeleton"]["referenceSkeleton"]
    info = skeleton["FinalRefBoneInfo"]
    poses = skeleton["FinalRefBonePose"]
    if len(info) != len(poses):
        raise ValueError("Bone names and reference transforms have different lengths.")
    bones = []
    for bone, pose in zip(info, poses):
        scale = pose["Scale3D"]
        bones.append(
            dict(
                name=bone["Name"],
                parent=bone["ParentIndex"],
                translation=[pose["Translation"][axis] for axis in "XYZ"],
                rotation=[pose["Rotation"][axis] for axis in "XYZW"],
                scale=[scale[axis] for axis in "XYZ"],
            )
        )
    components = []
    for ordinal, component in enumerate(binding["components"]):
        identity = component["captureIdentity"]
        mapping = component["boneMap"]
        if set(mapping) != {str(i) for i in range(len(mapping))}:
            raise ValueError("Component palette is incomplete.")
        components.append(
            dict(
                geometry_hash=binding["geometrySourceHash"],
                component_ordinal=ordinal,
                first_index=identity["index_offset"],
                index_count=identity["index_count"],
                vertex_sha256=component["sourceVbSha256"],
                index_sha256=component["sourceIbSha256"],
                bone_map=[
                    mapping[str(i)]["skeletonIndex"] for i in range(len(mapping))
                ],
            )
        )
    normalized = validate_rig(
        dict(
            format="mod-viewer-rig",
            version=1,
            coordinate_space="asset",
            bones=bones,
            components=components,
            skin_bindings=list(skin_bindings),
            humanoid_controls=humanoid_controls,
            wwmi_merged_palette=wwmi_merged_palette,
        )
    )
    normalized.pop("id")
    return normalized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--skin-bindings",
        type=Path,
        help="JSON list of independently verified Blend fingerprints and bone maps.",
    )
    parser.add_argument(
        "--wwmi-merged-palette",
        type=Path,
        help="JSON list of verified component identities and stock WWMI merge offsets.",
    )
    parser.add_argument(
        "--humanoid-controls",
        type=Path,
        help="JSON mapping of all 16 semantic controls to verified skeleton indices.",
    )
    args = parser.parse_args()
    profiles = (
        json.loads(args.skin_bindings.read_text(encoding="utf-8-sig"))
        if args.skin_bindings
        else []
    )
    humanoid = (
        json.loads(args.humanoid_controls.read_text(encoding="utf-8-sig"))
        if args.humanoid_controls
        else None
    )
    result = convert(
        json.loads(args.binding.read_text(encoding="utf-8-sig")),
        profiles,
        humanoid,
        json.loads(args.wwmi_merged_palette.read_text(encoding="utf-8-sig"))
        if args.wwmi_merged_palette
        else None,
    )
    args.output.write_text(
        json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {len(result['bones'])} bones and {len(result['components'])} component palettes."
    )


if __name__ == "__main__":
    main()
