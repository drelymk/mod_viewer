# Geometry rig experiment — Iterations 2 and 3

Iteration 2 fixes the lower-face neck placement and removes clothing-taper knee
detection and surface-driven hip widening. Odette's frontal wrist residuals
decrease modestly. Shoulders, elbows, feet, and Chisa's successful wrists retain
their Iteration 1 positions exactly. The result remains a temporary geometry
preview; it does not replace production Rig, binding, deformation, production IK, Physics,
Weight, or saved poses. Hidden joint depth is still uncertain.

## Evaluation and uncertainty

The same displayed Remielle, Odette, and Chisa variants are evaluated without
textures or Weight. The [frozen baseline](iteration-1/README.md) was captured
before anatomical fitting changed. The original perspective measurements and
guides are retained as `iteration-1/legacy-measurements.json` and
`iteration-1/legacy-guides.json`; the original six images remain in this folder.
Perspective and orthographic residuals are separate evaluations.

The tool now uses front and left-side orthographic cameras aligned to the
character's right/up/forward axes, with identical height normalization and
camera settling before measurement. The same existing overlay displays the
proportional, Iteration 1, and Iteration 2 positions. Camera direction never
enters the fitter, and no application UI controls were added.

Each of the 16 guide entries classifies **each view** independently:

- **Visible:** an approximate silhouette landmark; front uncertainty is about
  1% of character height and side uncertainty about 1.5%.
- **Estimated:** an anatomical estimate or a broad silhouette envelope,
  useful for plausibility rather than positional accuracy.
- **Indeterminate:** clothing or occlusion prevents a defensible reference.

Only visible guides receive error measurements. Estimated and indeterminate
rows retain null errors. Segment proportions, joint alignment and bilateral
height/depth differences are recorded for plausibility review. The side neck
envelope is broad enough to describe the neck silhouette; it cannot establish
an exact internal spine joint. Confidence means **surface evidence strength**,
not verified anatomical accuracy. The guides were frozen before runtime
algorithm changes and are never inputs to the fitter.

## Measured changes

All numbers are projected distances as percentages of character height.
Triples are **proportional / Iteration 1 / Iteration 2**. Bilateral values are
averages. Small residual changes within guide uncertainty are classified as
stable, rather than proof of improved anatomy.

| Front visible control | Remielle | Odette | Chisa |
| --- | ---: | ---: | ---: |
| Neck | 0.94 / 3.40 / 0.87 | 0.13 / 1.08 / 0.30 | 1.05 / 2.79 / 0.54 |
| Shoulder | 4.61 / 0.85 / 0.85 | 4.96 / 0.80 / 0.80 | 3.29 / 0.48 / 0.48 |
| Wrist/hand | 3.12 / 1.30 / 1.34 | 1.10 / 1.68 / 1.06 | 5.05 / 0.65 / 0.65 |

Neck improvement exceeds frontal guide uncertainty for Remielle and Chisa.
Odette's neck returns near the proportional location, although its original
proportional residual is still smaller. Odette's wrist improvement is below
manual-guide uncertainty, so it is a promising numerical change, not a strong
accuracy claim. Remielle's small wrist residual change is also below that
uncertainty; the controls remain on the forearm and before the hand extremity.

| Side visible control | Remielle I1 → I2 | Odette I1 → I2 | Chisa I1 → I2 |
| --- | ---: | ---: | ---: |
| Near wrist/hand | Indeterminate | 1.28 → 1.31 | 0.25 → 0.25 |
| Near foot | 1.30 → 1.30 | 0.58 → 0.58 | 0.91 → 0.91 |

The small Odette side wrist change is well below side-guide uncertainty. The
former Remielle and Chisa necks lie above the broad side neck envelope; the
updated necks are inside it. No visible-guide regression exceeds uncertainty
in either view. This does not establish hidden hip/knee or occluded-arm depth.

The [measurements](measurements.json) identify improvements, regressions,
stable changes, and uncertainty without comparing rows by hand. Detailed
[Remielle](iteration-2/remielle.json), [Odette](iteration-2/odette.json), and
[Chisa](iteration-2/chisa.json) captures retain all 16 positions in both views,
evidence, individual residuals, and three-dimensional plausibility measures.

## Front and side comparisons

| Model/view | Proportional | Iteration 1 | Iteration 2 |
| --- | --- | --- | --- |
| Remielle front | ![](iteration-1/remielle-front-proportional.png) | ![](iteration-1/remielle-front-geometry.png) | ![](iteration-2/remielle-front-geometry.png) |
| Remielle side | ![](iteration-1/remielle-side-proportional.png) | ![](iteration-1/remielle-side-geometry.png) | ![](iteration-2/remielle-side-geometry.png) |
| Odette front | ![](iteration-1/odette-front-proportional.png) | ![](iteration-1/odette-front-geometry.png) | ![](iteration-2/odette-front-geometry.png) |
| Odette side | ![](iteration-1/odette-side-proportional.png) | ![](iteration-1/odette-side-geometry.png) | ![](iteration-2/odette-side-geometry.png) |
| Chisa front | ![](iteration-1/chisa-front-proportional.png) | ![](iteration-1/chisa-front-geometry.png) | ![](iteration-2/chisa-front-geometry.png) |
| Chisa side | ![](iteration-1/chisa-side-proportional.png) | ![](iteration-1/chisa-side-geometry.png) | ![](iteration-2/chisa-side-geometry.png) |

## Anatomy and remaining limitations

The neck uses the shoulder-to-head relationship and consistent upper-torso
sections in three dimensions. A single narrow face section no longer controls
its height. The wrist uses distal forearm direction, the hand expansion and
anatomical length constraints; it no longer chooses the narrowest sleeve.
Shoulder and arm-path detection are unchanged.

Leg evidence is traced upward from each foot. Only continuous, bounded
three-dimensional sections can refine the hip/knee centerline. Segment
relationships retain plausible joint heights; width taper is never a knee
detector. Hidden hips remain at the proportional estimate on all three models.
The largest knee departures from that estimate are 0.44% of height for Remielle,
0.23% for Odette, and 0.75% for Chisa. These small corrections describe supported
limb alignment, not a measured internal knee. A synthetic skirt with obscured
thighs leaves hip/knee positions at their anatomical estimates.

Remielle's wings do not become hand endpoints. With wings hidden, shoulder
positions exactly match the stored Iteration 1 hidden-accessory fit. Accessory
visibility still changes surface normalization: central height changes by
0.89%, and the largest full-versus-hidden control shift is 2.25% of height.
This existing sampling sensitivity should not be confused with an Iteration 2
arm regression. The [accessory capture](iteration-2/remielle-hidden.json)
records both views and its comparison with the original hidden-accessory fit.

Chisa's 24 inactive meshes remain excluded. Independent duplication of every
displayed mesh causes zero control displacement. Approximately symmetric
height/depth relationships are retained. Exact duplicate draws and triangles
are removed; spatial occupancy mitigates other overlap. Differently triangulated
overlapping surfaces still need not produce identical samples.

## Workload and performance

| Model | Displayed/inactive meshes | Displayed triangles | Deduplicated draw triangles | Examined / unique triangles | Area samples / retained points | I1 → I2 observed fit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Remielle | 10 / 2 | 45,285 | 45,285 | 45,285 / 45,279 | 16,000 / 15,706 | 0.41 → 0.42 s |
| Odette | 35 / 10 | 75,131 | 72,346 | 72,346 / 72,165 | 16,000 / 15,554 | 0.64 → 0.61 s |
| Chisa | 19 / 24 | 145,749 | 145,749 | 90,000 / 90,000 | 16,000 / 15,619 | 0.76 → 0.70 s |

These sequential observations use the same browser and hardware, with cold
viewer loads. They are illustrative rather than a statistically controlled
benchmark. The 90,000-triangle and 16,000-area-sample caps and cooperative
yielding remain unchanged. Displayed counts include repeated draws; exact-draw
deduplication precedes the scan, triangle deduplication follows it, and spatial
occupancy produces retained fitting points. This reconciles the earlier report's
incorrect use of Odette's displayed count as its examined count.

## Reproduce and review

```text
python tools/evaluate_geometry_rig.py <mod-folder> --output <report-folder> --guides docs/experiments/geometry-rig/<model>-guides.json --baseline docs/experiments/geometry-rig/iteration-1/<model>.json
```

Use `--check-duplicates` for duplication testing and `--hide Wings` for the
accessory comparison. The read-only bridge fixture opens the existing viewer,
captures both views and three skeletons, and writes `comparison.json`. It does
not change mod files or load skinning data. A compatible local WebGPU browser
is required. Hiding geometry changes the normalization frame; compare that
capture with the matching hidden-accessory baseline when interpreting errors.

Static previews use existing pose/edit/Physics ownership state instead of
scanning every live vertex. Animated participants retain a conservative rest
comparison. Pending fits reject changed geometry buffers, draw ranges, rest
arrays, visibility, and transforms. Relevant mesh/transform events clear
completed previews. Results remain temporary, and explicit requests rerun fitting.

Focused coverage checks neck/head/shoulder relationships, wrist alignment,
skirt uncertainty, orientation, topology, duplicates, Weight-free preview,
cancellation, restored controls and model switching. The existing CI quality
audit and unit/core/app/integration selection are unchanged.

## Iteration 3 — skeleton-only posing

This section preserves the initial `a5f2cbe` evaluation. The review follow-up
below records the revised preferred planes and return-to-rest behavior.

**Test IK**, **Reset Pose**, and **Flip Bend** extend the existing preview under
Rig Advanced Settings. Drag a hand or foot to solve its two-bone chain with
`solveHumanoidControlIk()`. The shoulder/hip stays fixed, other temporary limb
poses remain intact, and the existing markers and connections update in place.
Geometry and Proportional both support posing; changing modes starts from that
skeleton's rest positions. The fitted rest controls remain authoritative and
unchanged. No Weight, ModelRig, binding, deformation, preset, or metadata state
is involved. Camera orbit resumes when dragging ends.

Bend directions are frozen from the fitted rest-joint geometry. Nearly straight
limbs use the anatomical forward axis: elbows toward the back, knees toward the
front. The camera does not determine bend direction. Flip Bend reverses only
the selected limb. Reset, disable, Current Rig, panel closure, regeneration,
model switching, geometry changes and model transforms clear temporary poses
or interactions as appropriate. Pointer cancellation and capture loss restore
camera controls without acquiring production transform/deformation ownership.

### Standardized movement comparison

All four limbs of the same three displayed variants were tested with identical
height-normalized displacements applied separately to each skeleton. Hands and
feet move inward by 0.04 height and upward by 0.12, with either +0.12 or -0.12
along anatomical forward. A third target extends each endpoint outward by 0.25
height along the geometry rest-chain direction; the same displacement vector is
also used for the proportional and reference comparisons. Each path has 40
steps and a return to the rest endpoint. Camera orientations and orthographic
scale match the static evaluation.

The compact [pose measurements](iteration-3/measurements.json) retain aggregate
invariants, segment lengths, endpoint reachability/residuals, bend orientation,
and return-to-rest differences. They contain no per-frame geometry snapshots.

| Check | Remielle | Odette | Chisa |
| --- | ---: | ---: | ---: |
| Forward/backward targets reached, geometry and proportional | 8 / 8 | 8 / 8 | 8 / 8 |
| Beyond-extension targets clamped, geometry and proportional | 4 / 4 | 4 / 4 | 4 / 4 |
| Largest segment-length error / height, including reference cases | < 3e-16 | < 3e-16 | < 3e-16 |
| Anchor / unrelated-limb displacement | 0 / 0 | 0 / 0 | 0 / 0 |
| Unexpected bend-sign changes in sampled paths | 0 | 0 | 0 |
| Weight requests / ModelRig loaded | 0 / no | 0 / no | 0 / no |
| Fitted rest / active geometry unchanged | yes / yes | yes / yes | yes / yes |

Counts above are per skeleton, with matching outcomes for Geometry and
Proportional. All control positions remained finite. Elbows bent backward and
knees forward relative to the solved chain in both forward/backward target
cases. Chains stayed connected to their anchors and endpoints, with no segment
stretching or pose leakage into other limbs. Chisa duplicate displacement
remains zero. Its count is **19 displayed + 24 inactive = 43 meshes**; the earlier
Iteration 2 prose/table incorrectly said 32 inactive, while the capture JSON
already recorded 24. The count correction does not change fitting inputs.

Near full extension, the two-bone solution is sensitive to small endpoint
movements. Maximum middle-joint travel between the 40 sampled steps is about
2.8% of height for the ordinary geometry/proportional cases, and 3.3% including
reference-plane comparisons. This is not a universal continuity guarantee.
Returning an endpoint to its rest target yields a deterministic constrained
pose; anatomical fallback planes can leave the middle joint slightly off its
fitted rest point (up to 1.01% height for Remielle, 0.86% for Odette, 0.08% for
Chisa). **Reset Pose** restores the fitted controls exactly.

Each image uses the same cropped viewport and scale. Rows 1–3 compare
Proportional (left) and Geometry (right): front toward, side toward, and side
away. Row 4 shows temporary corrected-reference joints from front and side.
The mesh intentionally remains in its A-pose throughout.

| Remielle | Odette | Chisa |
| --- | --- | --- |
| ![](iteration-3/remielle-poses.png) | ![](iteration-3/odette-poses.png) | ![](iteration-3/chisa-poses.png) |

### Separating joint placement, bend plane and solver behavior

Temporary reference controls use the existing manual frontal visible/estimated
guides and meaningful visible side guides; other depths remain unchanged.
Hidden hip/knee corrections are anatomical hypotheses, not ground truth. They
exist only in the evaluation fixture and never enter the runtime fitter or a
permanent editor. A second reference run holds the original geometry bend
directions fixed while correcting the joints, isolating plane-selection effects.

Odette's geometry arms respond asymmetrically: in the forward target, elbow
offsets behind their solved chains are 8.11% and 3.15% of height. Corrected joints
with recalculated rest planes yield 8.57% and 7.90%; retaining the original
planes yields 8.57% and 3.12%. Most of the right-arm imbalance therefore comes
from the bend plane inferred from the imperfect rest joints, rather than a
segment-length or reachability failure in the unchanged IK solver. Remielle's
arms remain comparatively balanced; Chisa's response changes with target depth
but remains bilateral and stable. The more symmetric proportional response
does not prove its joint centers are more accurate.

Dress and trouser geometry still prevents confident judgment of internal hip
and knee pivots. The controls move consistently, but this experiment cannot
establish natural mesh bending. Odette's rest-plane ambiguity and uncertain
internal pivots warrant review before a controlled deformation experiment.
No IK solver changes were made to compensate for inaccurate joints.

```text
python tools/evaluate_geometry_rig.py <mod-folder> --output <report-folder> --guides docs/experiments/geometry-rig/<model>-guides.json --poses
```

The existing lifecycle scenario now checks four endpoint gestures, marker reuse,
camera orbit, reset/flip, reach clamping, rest/geometry immutability, mode changes,
capture cleanup, model switching and rest-geometry invalidation. Production
overlay, Weight activation and locale checks remain separate existing coverage.
Keep PR #180 open and unmerged for review.

## Iteration 3 review — preferred planes and exact rest return

This section records `faee3b5`. The fine-interaction check in Iteration 4 below
supersedes its 2%-of-height transition distance; these measurements and captures
remain the review baseline.

Preferred planes now require rest-joint evidence with perpendicular displacement
of at least 10% of **each** segment length and at least 0.5 alignment with the
projected anatomical bend direction. Weak, nearly straight or anatomically
conflicting evidence uses the anatomical orientation instead. This avoids the
old character-height cutoff accepting a misleading small elbow offset. Chisa's
stronger, anatomically consistent arm evidence remains usable. The preferred
directions stay fixed throughout posing and do not depend on camera or pose
history. The fitter and existing two-bone solver remain unchanged.

Within 2% of character height of the rest endpoint, the plane rotates smoothly
from fitted rest evidence toward the preferred direction as target distance
increases. This deterministic transition preserves continuity while allowing the
original rest joint to be recovered. Returning exactly to the rest endpoint
restores the fitted middle joint and endpoint exactly, including after Flip Bend;
the flipped preference applies again when moving away. Reset Pose still clears
all temporary poses. Segment lengths, anchors and unrelated limbs remain intact.

The Rig event handler restores disabled-control state before synchronizing Weight
and Physics, then synchronizes Rig and Geometry Preview once. This removes the
duplicate preview refresh while retaining the existing event and panel ownership.
The 200-solve probe in `tools/evaluate_geometry_rig.py --poses` records one pose
status write per solve, down from two. In the paired probe, median synchronous
solver/panel/overlay update cost was about 0.4 ms before and after; the observed
95th percentile was about 0.5 and 0.6 ms. These timings exclude rendering and vary
with the browser runtime; the result supports removing duplication without
introducing another event architecture.

### Repeated movement evaluation

The [compact review measurements](iteration-3-review/measurements.json) use the
same normalized targets, rest fits, camera scale and displayed variants as the
initial comparison. Each movement now includes 40 outward and 40 return steps,
so continuity checks cover the rest transition as well as full extension.

| Geometry check | Remielle | Odette | Chisa |
| --- | ---: | ---: | ---: |
| Forward-target elbow offsets backward, left / right | 7.00% / 6.95% | 8.11% / 8.06% | 3.51% / 3.60% |
| Largest rest-return joint error / height | 0 | 0 | 0 |
| Largest joint step / height, outward and return | 2.49% | 2.14% | 2.27% |
| Unexpected bend-sign changes | 0 | 0 | 0 |

Geometry and Proportional still reach 8/8 forward/backward targets and clamp 4/4
overextension targets per model. Segment-length errors remain below 3e-16 height;
anchor and unrelated-limb displacement remain zero. All controls remain finite,
including the corrected-reference comparisons. Consecutive bend directions stay
aligned throughout the sampled paths; preferred-plane alignment is measured
outside the rest-transition neighborhood. These are sampled continuity results,
not a guarantee for arbitrary targets. Rest controls and active mesh geometry
remain unchanged, Weight requests remain zero, and no ModelRig is loaded. Chisa
still has 19 displayed and 24 inactive meshes, with zero duplicate displacement.

Odette's original 8.11% / 3.15% elbow response becomes 8.11% / 8.06% without
correcting any joint position. The image preserves the same viewport crop and
scale: original front/side views above, revised front/side views below.

![Odette original and revised geometry poses](iteration-3-review/odette-bend-comparison.png)

The existing lifecycle scenario now also checks weak, strong and conflicting
rest-plane evidence, exact rest return after a flip, a continuous rest approach,
near-full-extension continuity and history-independent target solutions. No new
UI or production Weight, skinning, deformation, reconciliation or persistence
behavior is introduced. The bend-plane issue is addressed in these comparisons;
occluded internal pivots and natural mesh deformation still require separate
evaluation. Keep PR #180 open and unmerged.

## Iteration 4 — fine hand interaction and one-arm deformation

The updated review requested actual hand drags near rest before deformation.
The check uses both hands on Remielle and Odette, front and oblique views, four
screen directions, and both default and flipped bends. Each of the 32 paths per
model moves the pointer through 2% of character height in 20 steps and returns
in 20 steps. Selection, drag acquisition and camera release are checked through
the existing pointer handlers. The model remains in its A-pose for this check.

The `faee3b5` captures show the elbow sweeping back across its initial arc while
the hand continues inward. The fitted and preferred planes can differ by roughly
90–111 degrees; completing that rotation inside a 2%-of-height neighborhood is
too abrupt for fine adjustments. The existing smooth transition now spans one
quarter of the limb's total reach. It retains the same preferred-plane criterion,
exact rest restoration and history independence. No fitter, solver, camera-based
rule or additional interaction UI is introduced. This is a preview transition
scale, not an anatomical accuracy claim or a universal motion guarantee.

| Fine-drag check                              | Remielle             | Odette               |
| -------------------------------------------- | -------------------- | -------------------- |
| Pointer paths, including Flip Bend           | 32                   | 32                   |
| Largest bend-plane turn in the revised paths | 24.87°               | 24.19°               |
| Largest elbow displacement, before → after   | 4.82% → 4.45% height | 5.49% → 4.12% height |
| Rest-return error                            | 0                    | 0                    |
| Weight requests / loaded ModelRig            | 0 / no               | 0 / no               |

The before/after strips show the same inward hand movement from rest through
0.5%, 1%, 1.5% and 2% of height, including Flip Bend. The revised elbow motion is
more gradual. Near full extension, even a small inward target change can still
produce a larger joint displacement because fixed-length segments must bend.

![Fine hand interaction before and after](iteration-4/fine-hand-comparison.png)

The repeated skeleton-only comparisons retain Odette's 8.11% / 8.06% forward
elbow offsets and zero rest-return error on all three models. The saved paths
remain finite, preserve segment lengths and anchors, and show no unexpected
bend-sign changes. Chisa's duplicate displacement remains zero. The existing
lifecycle coverage also checks a fine inward adjustment before and after a flip.

### Controlled left-arm deformation

After inspecting the revised hand drags, the experiment proceeds with only
Remielle's left arm. `tools/evaluate_geometry_arm.py --deform-left-arm` first
fits the independent geometry skeleton, then decodes the retained authored Blend
streams. It reuses the existing source-graph construction, ModelJoint
reconciliation, binding and position/normal deformation helpers in a disposable
evaluation session. Authored indices and weights are unchanged and unnormalized.
Only transforms belonging to `left_upper_arm` and `left_lower_arm` are applied;
other influences retain their authored rest contribution.

The comparator is the current automatic humanoid Rig, constructed with the same
anatomical axes and the existing weight-inferred ModelJoint binding. Its control
positions currently use the proportional geometry template; it is not a new
skeleton inferred from weights for this experiment. Both rigs use the same
authored streams, binding rules and height-normalized up/forward or up/backward
target displacement. Geometry generates its controls before skinning data is
loaded. The camera frame and image crop are shared, and the overlay is hidden
only in these mesh-quality captures.

| Deformation check                                 | Current automatic Rig | Geometry Rig |
| ------------------------------------------------- | --------------------- | ------------ |
| Arm-bound ModelJoints                             | 57                    | 46           |
| Moved vertices, including inactive draws          | 11,880                | 9,418        |
| Moved displayed hair vertices                     | 951                   | 0            |
| Moved displayed leg vertices                      | 5                     | 5            |
| Forward changed-edge length ratio, p95 / maximum  | 1.49 / 4.91           | 1.38 / 4.56  |
| Backward changed-edge length ratio, p95 / maximum | 1.87 / 9.27           | 2.77 / 10.13 |

Edge ratios compare posed and authored rest lengths on displayed triangles,
deduplicating edges per draw and excluding rest edges shorter than 0.001% of
height. The percentile covers changed edges rather than all edges. These are
distortion diagnostics, not anatomical ground truth. Moved-vertex counts use a
0.0001%-of-height cutoff; displayed draws are listed separately in the raw data.

![Current and geometry left-arm deformation](iteration-4/one-arm-deformation.png)

Both forward and backward targets are reached and all output positions/normals
are finite. Vertices with no bound-arm influence remain fixed. Returning to rest
restores positions and normals exactly, and neither fitted controls nor authored
influences change. A neutral synthetic lifecycle checks these contracts through
the experiment's helpers. The production Weight UI and ModelRig remain inactive;
the evaluation explicitly reads skinning data outside that UI.

The geometry binding avoids the comparator's hair movement and has a smaller
forward edge-stretch percentile, but backward stretching is worse. The shared
leg leakage and visible shoulder/cloth pulling show that automatic deformation
ownership still needs review. The experiment does not establish an overall
quality improvement. Keep it limited to this arm and model; review binding
boundaries before expanding to other limbs or games. No production deformation,
Weight assignment, pose persistence or ModelJoint algorithm changes are made.
PR #180 remains open and unmerged.

The [compact measurements](iteration-4/measurements.json) retain the interaction
aggregates, regression checks, arm controls, binding counts and deformation
diagnostics. To reproduce with the same displayed mod variant:

```text
python tools/evaluate_geometry_arm.py <mod-folder> --near-rest --output <capture-folder>
python tools/evaluate_geometry_arm.py <mod-folder> --deform-left-arm --output <capture-folder>
```

Use a compatible browser/WebGPU environment. The first command drives the
existing viewport controls; the second is an explicit evaluation operation.
Browser/rendering tests remain outside the normal GitHub CI selection.
