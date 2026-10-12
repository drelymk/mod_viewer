# Geometry Rig experiment

Geometry-first fitting provides an independent skeleton and IK preview. The
first direct vertex-skinning candidate now deforms its left arm without ModelRig
reconciliation. It fails the three-game deformation gate, so full-body expansion
and production replacement remain pending. PR #180 stays open and unmerged.

## Retained feature

Open the Weight / Rig panel, expand Advanced, and choose **Preview Geometry Rig**.
The preview works without loading Weight or constructing the production ModelRig.
Compare Geometry and Proportional skeletons, enable **Test IK**, and drag a hand
or foot. To deform the mesh, enable **Test Left Arm Deformation**, then drag the
left hand. This explicit experiment reads authored skin streams independently
of Weight analysis. Other limbs remain skeleton-only. **Flip Bend** changes the
selected limb's bend direction; **Reset Pose** restores the skeleton and exact
original positions/normals. **Current** exits the preview; **Load Weight / Rig**
starts the existing authored-weight workflow explicitly.

The fitter samples displayed immutable rest-pose triangles in model space,
deduplicates exact draws/triangles, and uses character orientation rather than
camera direction. It fits the existing 16-control topology with bounded surface
evidence and anatomical estimates. Sampling is capped at 90,000 triangles and
16,000 area samples, with cooperative cancellation when inputs change.

Geometry and Proportional IK each use their own rest controls, segment lengths
and bend frame through the existing two-bone solver. Skeleton-only IK does not
read weights or acquire deformation ownership. The arm experiment temporarily
suspends animation on affected members, uses immutable rest geometry and the
existing position/normal kernels, and releases ownership on exit. Comparison
changes, panel closure, model changes and input invalidation restore geometry
and release dragging. A shape change preserves the newly edited member. Neither
mode saves mappings or activates production Rig/Physics.

## Direct skinning candidate and three-game gate

The geometry skeleton remains authoritative. A compact squared distance kernel
with radius 6% of character height estimates each vertex's relationship to the
rest segments. Upper arm and forearm are moving drivers; other segments provide
stationary torso/context support. Triangle-area-weighted spatial evidence is
averaged for each exact authored source/influence, then combined with each
vertex's original authored weights and normalized. An elbow-spanning influence
can contribute to both drivers. WWMI indices are already remapped by the shared
decoder; IDs from distinct source keys are never merged. No ModelJoint graph is
built. Generated weights stay cached throughout dragging; unsupported geometry
remains stationary. Authored decode failures stop activation.

The displayed Remielle (ZZZ), Odette (Genshin) and Chisa (WuWa) variants were
compared at 15°, 45°, 90°, forward/backward hand targets and exact return to rest.
The old binding, soft transfer and spatial-only ablation received identical
posed geometry controls. The old binding comparator uses the existing
reconciliation and deformation functions with those common controls, rather
than conflating binding changes with its different automatic skeleton. Visual
comparisons use one fixed camera and material mode per model.

Displayed unique edges longer than 0.001% of height include clothing and sleeves.
The following 90° results show extreme compression/stretch ratios; 1 is unchanged.

| Model | Existing binding min/max | Soft transfer min/max | Spatial-only min/max |
| --- | ---: | ---: | ---: |
| Remielle | 0.159 / 5.719 | 0.571 / 14.066 | 0.449 / 156.839 |
| Odette | 0.327 / 3.540 | 0.462 / 2.550 | 0.373 / 2.390 |
| Chisa | 0.064 / 6.477 | 0.070 / 52.653 | 0.113 / 201.833 |

Affected-region 1st–99th percentile ratios at 90° expose compression as well as
stretch, without diluting the result with the stationary model:

| Model | Shoulder existing → soft | Elbow existing → soft |
| --- | --- | --- |
| Remielle | 0.679–1.769 → 0.852–1.183 | 0.689–2.167 → 0.683–2.302 |
| Odette | 0.676–1.579 → 0.800–1.117 | 0.889–1.109 → 0.733–1.733 |
| Chisa | 0.596–2.423 → 0.841–1.308 | 0.393–2.000 → 0.398–1.362 |

Regions are fixed rest-space spheres of radius 8% height around the fitted
shoulder/elbow, with 4,172/1,715 edges for Remielle, 12,019/2,929 for Odette and
14,247/6,554 for Chisa. The remaining displayed edges were also checked, including
short edges on hand, clothing and accessory boundaries.

Soft transfer smooths the shoulder and reduces torso displacement, but visible
hand/sleeve spikes remain on Remielle and Chisa. Odette improves global extremes
while its elbow region compresses and stretches more. Chisa's worst backward
stretch is 72.766×; its worst edge-length increase is 11.26% of character height,
so this is consequential deformation rather than only a tiny-edge ratio.
At 90°, maximum wrist-region lag relative to the forearm transform is 0.35%,
0.31% and 1.33% height respectively. Central torso displacement is 0.14%, 0.06%
and 0.35%; head-height vertices still move by 1.35%, 0.62% and 1.22%. Odette's
bangs move by 0.46%, confirming unwanted hair motion. No topology is rewritten,
but elongated triangles and poor wrist following fail acceptance.

Manual semantic guide adjustments were applied through the existing control
override helper, without source-bone mappings. These separate placement from
binding: Remielle's 90° maximum improves from 14.066× to 2.450×, while Chisa's
52.653× remains 50.332×. Guide depths remain uncertain; front guides have about
1% height uncertainty. Chisa's binding failure survives that correction, so
another fitting heuristic is not justified by these results.

Authored information improves the worst spatial-only failures by spreading
segment relationships across connected influences, but also spreads motion to
unrelated support. The compact spatial support boundary and broad influence
averaging remain weaknesses of this candidate. There is one candidate and a
spatial-only diagnostic ablation, with no automatic fallback chain.

Observed cached binding construction took approximately 50–70 ms, 80–90 ms and
150–200 ms respectively, yielding cooperatively. Warm pose application took
approximately 2–4 ms, 6–7 ms and 8–12 ms, excluding rendering and backend fetch/
decode; initial application was slower. All three restored positions and normals
exactly after actual candidate deformation. Responsiveness is promising, but
mesh quality fails the gate. Do not expand this method, remove production Rig,
or merge until a subsequent design decision resolves those failures.

## Locality review: binding limitation remains open

The current transfer uses an influence-wide mean of spatial support. Its final
vertex weights depend on authored weights but discard that vertex's own spatial
support. Two nearby vertices with the same spatial classification can therefore
receive different moving/stationary mixtures simply because their authored
influence mixtures differ. Conversely, distant vertices sharing one influence
receive the same driver distribution. Source-scoped IDs prevent cross-stream
mixing; they do not prevent this mixing within one influence.

The hand and sleeve failures illustrate both cases. A traced Remielle hand edge
has pure forearm spatial support at both ends, but different authored mixtures
introduce different stationary weights after transfer. A traced Chisa sleeve edge
has stationary spatial support at both ends, yet influence-wide transfer gives
its ends different forearm contributions. Their stretching is introduced by the
transfer rather than by a spatial disagreement along those particular edges.
Elbow edges can already mix upper-arm and forearm spatial support before transfer;
their compression also needs investigation of the segmentation and joint frames.
The spatial-only ablation also moves some head-height and central torso geometry
under forward arm targets. Hair/accessories close to arm segments can receive
moving support before transfer. Influence averaging is therefore not the sole
cause of unintended movement; spatial proximity does not establish anatomy.

Locality alone has not resolved the three-game gate. Local area-weighted influence
averaging with a continuous tent lattice improved some elbow and wrist behavior,
but retained sleeve leakage and worsened the Remielle hand spike. Conditioning
the transferred distribution on each vertex's spatial support removed that
particular hand mixture, but exposed the compact kernel's discontinuous switch
to stationary weights outside all segment support. Replacing that boundary with
a smooth distance kernel improved Remielle and Chisa stretching while worsening
Odette stretching and compression. These candidates are not retained: none
preserved smooth transitions and local anatomical behavior across all three models.

The influence-wide transfer remains an experimental limitation, not a resolved
binding. No extra threshold, model-specific exception or fallback chain is added.
Production Rig, Weight and Physics remain unchanged. Full-body expansion and
merging remain blocked on a binding design that passes shoulder/elbow quality,
wrist following and unrelated-geometry checks together.

## Fitting results and limitations

The subsequent [surface continuity investigation](continuity.md) examines the
same hand, sleeve and elbow failure edges using indexed connectivity, exact seam
evidence and authored co-deformation relationships. It includes a reproducible
read-only inspector and compact case artifact. Connectivity supports testing
constrained propagation, but does not establish automatic attachment regions;
the fitter, IK and current deformation implementation remain unchanged.

The completed investigation covered displayed Remielle (ZZZ), Odette (Genshin)
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

## Earlier hard-binding findings

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
new threshold, fallback, special case or hard-ownership rule from that study is
retained.

Binding complex authored skinning to a simplified humanoid Rig needs a separate
design decision. These results do not prove automatic mapping impossible. The
[completed research](https://github.com/drelymk/mod_viewer/blob/062a5acce650de71508b62e9e744461f6ab8e1f3/docs/experiments/geometry-rig/README.md)
and detailed captures remain available in Git history; the active branch removes
the accumulated captures, standalone evaluators and deformation-only test.

## Focused verification

`src/tests/web/test_geometry_rig.py` covers orientation, topology, visible rest
sampling, duplicate geometry, obscured legs and the preview/IK lifecycle. Its
arm scenario covers source-isolated deformation, repeated cached IK, transformed
members, exact reset, comparison changes, cancelled loading, decode failure and
shape invalidation without Weight/ModelRig activation. The
overlay contract in `src/tests/web/test_modules.py` verifies that preview works
without a ModelRig and restores the current overlay. Existing Weight rendering
tests exercise the explicit load action and production pose/reset behavior.
Browser/WebGPU tests remain local or dedicated-environment checks; normal CI
continues to run the quality audit and unit/core/app/integration tests.
