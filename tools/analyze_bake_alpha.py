"""Report final Safe Bake alpha resolution without modifying a DDS."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

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
        description="Analyze final Safe Bake alpha resolution without changing "
                    "the source.")
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
        print("scope: final Safe Bake resolution (texconv retries plus "
              "deterministic BC7 fallback)")
        with tempfile.TemporaryDirectory(
                prefix="modviewer-alpha-analysis-") as workdir:
            safe_masks = tuple(
                bytearray([1]) * (mip.units_x * mip.units_y)
                for mip in layout.mips)
            pixel_coverages = tuple(
                SimpleNamespace(
                    mask=bytearray([1]) * (mip.width * mip.height))
                for mip in layout.mips)
            prepared = SimpleNamespace(
                info=layout.info,
                layout=layout,
                selected_pixels=pixel_coverages[0],
                selected_pixel_coverages=pixel_coverages,
                safe_masks=safe_masks)
            candidate, writable_masks, protected_masks, _stats = (
                texture_bake._encode_alpha_coupled_mips(
                    original, prepared, adjustment, workdir,
                    compression_backend=args.compression_backend))

            for level, mip in enumerate(layout.mips):
                source_image = texture_bake._decode_alpha_coupled_mip_rgba(
                    original, layout, mip)
                candidate_image = texture_bake._decode_alpha_coupled_mip_rgba(
                    candidate, layout, mip)
                alpha = source_image.getchannel("A")
                compatible, alpha_stats = (
                    texture_bake._alpha_compatibility_for_mapping(
                        original, layout, mip, candidate, layout, mip,
                        safe_masks[level], tuple(range(len(safe_masks[level]))),
                        source_image=source_image,
                        candidate_image=candidate_image))
                target_rgba, safe_indices, atlas_width, atlas_height = (
                    texture_bake._build_safe_block_atlas(
                        source_image, mip,
                        pixel_coverages[level].mask, safe_masks[level],
                        adjustment))
                rgb_squared_error = 0
                rgb_absolute_error = 0
                rgb_max_error = 0
                for atlas_index, source_index in enumerate(safe_indices):
                    source_x, source_y, unit_width, unit_height = (
                        texture_bake._unit_bounds(mip, source_index))
                    target_pixels = texture_bake._atlas_block_pixels(
                        target_rgba, atlas_width, atlas_index)
                    candidate_pixels = tuple(
                        candidate_image.getpixel((source_x + column,
                                                  source_y + row))
                        for row in range(unit_height)
                        for column in range(unit_width))
                    quality = texture_bake._bc7_candidate_quality(
                        target_pixels, candidate_pixels,
                        unit_width, unit_height,
                        compatible[source_index])
                    rgb_squared_error += quality.rgb_squared_error
                    rgb_absolute_error += quality.rgb_absolute_error
                    rgb_max_error = max(rgb_max_error, quality.rgb_max_error)
                total_units = mip.units_x * mip.units_y
                alpha_min, alpha_max = alpha.getextrema()
                print(
                    f"mip {mip.level}: {layout.info.format} "
                    f"{mip.width}x{mip.height}; "
                    f"atlas {atlas_width}x{atlas_height}; "
                    f"writable/protected blocks "
                    f"{sum(writable_masks[level])}/{sum(protected_masks[level])} "
                    f"of {total_units}; "
                    f"source alpha {alpha_min}..{alpha_max}; "
                    f"final alpha-compatible {sum(compatible)}; "
                    f"incompatible {total_units - sum(compatible)}; "
                    f"changed alpha pixels {alpha_stats.changed_pixels}; "
                    f"max alpha delta {alpha_stats.max_delta}; "
                    f"RGB squared/absolute/max "
                    f"{rgb_squared_error}/{rgb_absolute_error}/{rgb_max_error}")
    except (OSError, ValueError, texture_bake.TextureBakeAnalysisError) as error:
        print(f"analysis failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
