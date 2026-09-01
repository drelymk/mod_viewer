"""Report per-mip BC1/BC7 alpha compatibility without modifying a DDS."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from PIL import Image

from app.mods import texture_bake
from core.textures.color_adjustment import normalize_color_adjustment
from core.textures.dds import inspect_dds_layout


_ADJUSTMENT_FIELDS = frozenset({
    "hue", "saturation", "brightness", "contrast", "red", "green",
    "blue", "tint", "tint_strength",
})


def _adjustment(values):
    result = {"hue": 30}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or key not in _ADJUSTMENT_FIELDS:
            raise ValueError(f"invalid adjustment field: {item}")
        result[key] = value if key == "tint" else float(value)
    normalized = normalize_color_adjustment(result, reject_invalid=True)
    if normalized is None:
        raise ValueError("the adjustment is invalid")
    return normalized


def _read(path):
    try:
        return path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read DDS: {error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyze BC1/BC7 alpha compatibility without changing the source.")
    parser.add_argument("dds", type=Path, help="DDS file to analyze")
    parser.add_argument(
        "--adjustment", nargs="*", default=[], metavar="FIELD=VALUE",
        help="representative RGB adjustment (default: hue=30)")
    parser.add_argument(
        "--compression-backend", choices=("auto", "cpu", "gpu"),
        default="auto", help="texconv backend (default: auto)")
    args = parser.parse_args(argv)

    try:
        layout = inspect_dds_layout(args.dds)
    except (OSError, ValueError) as error:
        parser.error(f"could not inspect DDS: {error}")
    if layout is None:
        parser.error("the DDS header or payload is invalid")
    print(f"format: {layout.info.format}")
    print(f"dimensions: {layout.info.width}x{layout.info.height}")
    print(f"mip levels: {layout.info.mip_count}")
    if layout.info.format not in texture_bake._ALPHA_COUPLED_FORMATS:
        print("alpha: independently preserved by the existing patch path")
        return 0

    try:
        adjustment = _adjustment(args.adjustment)
        original = _read(args.dds)
        with tempfile.TemporaryDirectory(
                prefix="modviewer-alpha-analysis-") as workdir:
            for mip in layout.mips:
                source_image = texture_bake._decode_alpha_coupled_mip_rgba(
                    original, layout, mip)
                alpha = source_image.getchannel("A")
                safe_mask = bytearray([1]) * (mip.units_x * mip.units_y)
                atlas_rgba, safe_indices, atlas_width, atlas_height = (
                    texture_bake._build_safe_block_atlas(
                        source_image, mip,
                        bytearray([1]) * (mip.width * mip.height),
                        safe_mask, adjustment))
                png_path = os.path.join(workdir, f"analyze-mip-{mip.level}.png")
                target_image = Image.frombytes(
                    "RGBA", (atlas_width, atlas_height), atlas_rgba)
                target_image.save(png_path, format="PNG")
                candidate_path = texture_bake.encode_png_to_dds(
                    png_path, workdir, layout.info.format, 1, srgb=True,
                    compression_backend=args.compression_backend)
                candidate = _read(Path(candidate_path))
                candidate_layout = texture_bake._validate_atlas_candidate(
                    layout, atlas_width, atlas_height, candidate_path, candidate)
                candidate_mip = candidate_layout.mips[0]
                candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
                    candidate, candidate_layout, candidate_mip)
                candidate_indices = [0] * len(safe_mask)
                for candidate_index, source_index in enumerate(safe_indices):
                    candidate_indices[source_index] = candidate_index
                compatible, stats = (
                    texture_bake._alpha_compatibility_for_mapping(
                        original, layout, mip, candidate, candidate_layout,
                        candidate_mip, safe_mask, candidate_indices,
                        source_image=source_image,
                        candidate_image=candidate_image))
                target_rgba = target_image.tobytes()
                candidate_rgba = candidate_image.tobytes()
                rgb_squared_error = 0
                rgb_absolute_error = 0
                rgb_max_error = 0
                for source_index in safe_indices:
                    quality = texture_bake._block_candidate_quality(
                        target_rgba, candidate_rgba, mip, candidate_mip,
                        source_index,
                        candidate_indices[source_index],
                        compatible[source_index])
                    rgb_squared_error += quality.rgb_squared_error
                    rgb_absolute_error += quality.rgb_absolute_error
                    rgb_max_error = max(rgb_max_error, quality.rgb_max_error)
                compatible_count = sum(compatible)
                total_units = mip.units_x * mip.units_y
                alpha_min, alpha_max = alpha.getextrema()
                print(
                    f"mip {mip.level}: {layout.info.format} "
                    f"{mip.width}x{mip.height}; "
                    f"atlas {atlas_width}x{atlas_height}; "
                    f"safe/all blocks {stats.tested_units}/{total_units}; "
                    f"source alpha {alpha_min}..{alpha_max}; "
                    f"candidate-compatible {compatible_count}; "
                    f"incompatible {total_units - compatible_count}; "
                    f"changed alpha pixels {stats.changed_pixels}; "
                    f"max alpha delta {stats.max_delta}; "
                    f"RGB squared/absolute/max "
                    f"{rgb_squared_error}/{rgb_absolute_error}/{rgb_max_error}")
    except (OSError, ValueError, texture_bake.TextureBakeAnalysisError) as error:
        print(f"analysis failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
