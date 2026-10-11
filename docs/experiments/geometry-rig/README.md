# Geometry Rig experiment

Geometry-first fitting provides a useful experimental skeleton and independent
preview. Automatically binding authored influences to its simplified drivers
remains unresolved. This branch retains the fitter and skeleton-only IK;
automatic binding research stops here.

## Retained feature

Open the Weight / Rig panel, expand Advanced, and choose **Preview Geometry Rig**.
The preview works without loading Weight or constructing the production ModelRig.
Compare Geometry and Proportional skeletons, enable **Test IK**, and drag a hand
or foot. **Flip Bend** changes the selected limb's bend direction; **Reset Pose**
restores its skeleton. **Current** exits the preview; **Load Weight / Rig** starts
the existing authored-weight workflow explicitly.

The fitter samples displayed immutable rest-pose triangles in model space,
deduplicates exact draws/triangles, and uses character orientation rather than
camera direction. It fits the existing 16-control topology with bounded surface
evidence and anatomical estimates. Sampling is capped at 90,000 triangles and
16,000 area samples, with cooperative cancellation when inputs change.

Geometry and Proportional IK each use their own rest controls, segment lengths
and bend frame through the existing two-bone solver. Poses remain temporary
overlay state. The preview does not deform meshes, read authored weights, acquire
animation ownership, save mappings or alter production Rig/Physics state. Reset,
comparison changes, panel closure, model changes and input invalidation release
active dragging and camera ownership.

## Fitting results and limitations

The completed investigation covered displayed Remielle (Genshin), Odette (ZZZ)
and Chisa (WuWa) variants. Approximate front-view silhouette residuals below are
percentages of character height, shown as proportional to geometry-fitted:

| Visible control | Remielle | Odette | Chisa |
| --- | ---: | ---: | ---: |
| Neck | 0.94 → 0.87 | 0.13 → 0.30 | 1.05 → 0.54 |
| Shoulder | 4.61 → 0.85 | 4.96 → 0.80 | 3.29 → 0.48 |
| Wrist/hand | 3.12 → 1.34 | 1.10 → 1.06 | 5.05 → 0.65 |

These guides have about 1% height uncertainty in front and 1.5% in side views;
small differences do not establish anatomical improvement. Confidence describes
surface evidence, not verified internal anatomy. Hidden hips and obscured knees
retain anatomical estimates; clothing taper does not identify a knee. Wrist
fitting uses distal forearm direction and hand expansion rather than sleeve
narrowness. Accessories and differently triangulated overlapping surfaces can
still affect normalization and sampling. This remains an A-pose experiment,
not a general skeleton extractor or a replacement for production Rig fitting.

## Why automatic binding was deferred

Authored-weight deformation was demonstrated, but geometry alone did not establish
which simplified driver should own every authored influence. Pivot-to-segment
assignment and mean squared area-weighted support distance both improved selected
cases and introduced cross-model regressions. The support method changed four
ModelJoints on Remielle, four on Odette and nineteen on Chisa. Full-displacement
maximum edge-length ratios were:

| Pose | Original ownership | Support ownership |
| --- | ---: | ---: |
| Remielle left forward | 4.558× | 3.056× |
| Remielle right forward | 4.215× | 2.865× |
| Odette left forward | 3.210× | 3.500× |
| Odette right forward | 2.923× | 3.204× |
| Chisa right forward | 4.933× | 10.562× |
| Chisa right backward | 10.374× | 28.267× |

The comparisons used the same displayed variants, fixed targets and common edge
samples, preserved authored influences, and restored positions/normals exactly.
Stretch alone was insufficient: Odette's left-forward minimum ratio worsened
from 0.431× to 0.346×, and Chisa's right-backward half-target minimum worsened
from 0.119× to 0.044×. Chisa's 28.267× maximum concerns a very short local edge,
not movement across 28 character heights.

Support avoided Odette influence 49's incorrect pivot-only assignment but moved
other influences onto new boundaries. Chisa influence 89 still preferred upper
arm even though 61.69% of its area-weighted support lay beyond the elbow. It
shared 83.33% of its support with forearm influence 91. Conversely, successful
Remielle 170–173 corrections separated owners despite 96.63–98.30% overlap with
forearm influences. Shared vertices describe blends; they do not establish a
same-owner constraint. No reliable generic refinement was demonstrated, and no
new threshold, fallback, special case or ownership rule is retained.

Binding complex authored skinning to a simplified humanoid Rig needs a separate
design decision. These results do not prove automatic mapping impossible. The
[completed research](https://github.com/drelymk/mod_viewer/blob/062a5acce650de71508b62e9e744461f6ab8e1f3/docs/experiments/geometry-rig/README.md)
and detailed captures remain available in Git history; the active branch removes
the accumulated captures, standalone evaluators and deformation-only test.

## Focused verification

`src/tests/web/test_geometry_rig.py` covers orientation, topology, visible rest
sampling, duplicate geometry, obscured legs and the preview/IK lifecycle. The
overlay contract in `src/tests/web/test_modules.py` verifies that preview works
without a ModelRig and restores the current overlay. Existing Weight rendering
tests exercise the explicit load action and production pose/reset behavior.
Browser/WebGPU tests remain local or dedicated-environment checks; normal CI
continues to run the quality audit and unit/core/app/integration tests.
