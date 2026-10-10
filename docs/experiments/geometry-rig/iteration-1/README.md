# Frozen Iteration 1 baseline

These captures were recorded before changing anatomical fitting for Iteration 2.
Each model has front and left-side orthographic views of the proportional and
Iteration 1 geometry skeletons. Camera axes come from the character frame;
the same character height sets both views' scale. Camera settling and projection
measurement occur before each screenshot.

The three model JSON files record all 16 control positions in both views,
evidence, workload, segment lengths, and bilateral height/depth differences.
The root `*-guides.json` files now classify each view as visible, estimated,
or indeterminate. Only visible guides produce positional accuracy errors.
The side neck envelope checks broad anatomical plausibility; it is not a
precise internal-joint reference. Uncertainty is approximately 1% of height
in front and 1.5% in side views. Confidence means evidence strength.

`legacy-measurements.json` and `legacy-guides.json` preserve the original
perspective evaluation. The original six screenshots remain in the parent
directory. Their numbers should not be compared directly with the new
orthographic measurements.

| Model | Front proportional | Front geometry | Side proportional | Side geometry |
| --- | --- | --- | --- | --- |
| Remielle | ![](remielle-front-proportional.png) | ![](remielle-front-geometry.png) | ![](remielle-side-proportional.png) | ![](remielle-side-geometry.png) |
| Odette | ![](odette-front-proportional.png) | ![](odette-front-geometry.png) | ![](odette-side-proportional.png) | ![](odette-side-geometry.png) |
| Chisa | ![](chisa-front-proportional.png) | ![](chisa-front-geometry.png) | ![](chisa-side-proportional.png) | ![](chisa-side-geometry.png) |

For Iteration 2, supply one of these model JSON files as `--baseline` to the
evaluation tool. It captures the same overlay with the old positions, and
reports improvements/regressions only when changes exceed guide uncertainty.
Hidden guides retain null accuracy errors and are reviewed through limb
alignment, segment ratios, symmetry, and the side silhouette.
