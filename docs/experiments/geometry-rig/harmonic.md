# Bounded harmonic skinning experiment

The constrained graph test fails the three-game deformation gate. It fixes
Remielle's isolated hand case, but increases Odette wrist lag and propagates
Chisa arm motion into unrelated regions of the same connected draw. Adding a
few constraints does not fix those failures. No graph binding, automatic labels
or further weighting rules are added to the viewer.

## One experiment and its scope

The [offline probe](harmonic_probe.py) reuses the continuity inspector's triangle
connectivity, exact-edge seam candidates and source-scoped authored overlap.
Original weights determine seam compatibility; normalized copies determine edge
conductance. Zero-overlap edges contribute no conductance. A fixed-label harmonic
extension produces stationary, upper-arm and forearm weights. Unlabelled
components remain explicitly unresolved, without spatial or influence-average
fallbacks. The numerical solve uses conjugate gradients with a `1e-10` residual
tolerance and a bounded iteration count; that tolerance is not a spatial rule.

The [compact evidence](harmonic-cases.json) records the three existing case draws,
their geometry/skin hashes, fixed vertex labels, identical rest controls and
posed controls, solver diagnostics and all five pose comparisons. Driver indices
are 0 = stationary, 1 = upper arm, 2 = forearm. Labels are inspected experimental
vertex constraints, not character rules, influence owners or saved Rig mappings.

Front-view surface plots and original rest coordinates were inspected before
choosing constraints. Remielle's selected draw contains the two distal arms/hands,
so its left component is labelled forearm and its right component stationary;
there is no upper-arm region in that draw to label. Odette constraints mark its
upper-arm strip, distal strip/sleeve, left hand, right arm and neck/head details.
Chisa constraints mark the torso, left upper arm, left wrist and disconnected
stationary details. Every positive-conductance component receives an inspected
label: 2 components for Remielle, 11 for Odette and 8 for Chisa.

Three label sets test the same objective: base constraints, each base constraint
moved to a neighbouring triangle vertex, and a small extra-constraint set. Base
counts are 2, 12 and 10 respectively; extra counts are 3, 14 and 17. The extra
Chisa labels mark neck, opposite arm/wrist, pelvis, waist, hand and proximal arm.
These are sensitivity comparisons, not a sequence of model-specific rules.

Weights were injected into the selected member of the existing cached
`buildGeometryArmSkinning` prototype. Both candidates used the same geometry-fit
skeleton and existing position/normal kernels. Comparisons cover 15°, 45° and
90° forearm bends relative to the upper-arm direction, forward/backward IK
targets and exact restoration after every pose. The complete selected draw was
measured, including stationary regions; other draws remained at rest for both
comparators. This tests local and whole-draw feasibility, not full-model binding
or automatic hair attachment. Hair surfaces absent from these draws are not
validated by their remaining still.

## Results

These are recorded experimental measurements. The offline probe reproduces the
harmonic weights and solver diagnostics only; it does not replay the complete
pose, deformation and measurement comparison. The complete comparison harness
is not included in this repository.

The 90° whole-draw edge extrema and maximum wrist lag show the tradeoff. Wrist
lag is distance from the posed forearm transform, as a percentage of model height.

| Case | Soft-transfer min/max | Base harmonic min/max | Soft → base wrist lag |
| --- | ---: | ---: | ---: |
| Remielle hand | 0.407 / 15.982 | 1.000 / 1.000 | 0.581% → approximately 0% |
| Odette elbow | 0.494 / 2.387 | 0.459 / 3.943 | 0.689% → 3.174% |
| Chisa sleeve | 0.079 / 102.600 | 0.080 / 6.307 | 0.580% → 3.277% |

Remielle's two traced edge ratios both become approximately 1. Its labelled
components each have one driver, so the harmonic solution is constant within
each component. This is a rigid-hand success, not evidence of smooth elbow
binding or a general three-driver anatomical solution.

Odette's stretched probe changes from 2.387 to 0.884; its compressed probe changes
from 0.494 to 0.706. But the worst distortion moves elsewhere in the draw, and
wrist lag increases substantially. Its elbow-region 1st–99th percentile range
changes from 0.716–1.816 to 0.699–1.001: smoother stretching does not remove
compression or guarantee wrist following.

Chisa's stretched probe changes from 102.600 to 0.664 at 90°. Maximum absolute
edge-length increase drops from 15.16% to 1.21% of height, so the local improvement
is consequential. However, elbow-region 1st–99th percentile ratios change from
0.218–2.153 to 0.143–1.099. The draw's head-height region moves by 2.90% and its
opposite side by 5.15% of height at 90°. At the forward target, opposite-side
movement reaches 10.32%, versus 0.36% with soft transfer. A small set of point
constraints allows motion to diffuse far beyond the intended arm.

Shoulder/elbow metrics use the same rest-space 8%-height neighbourhoods as the
prior measurements; wrist metrics use a 4%-height neighbourhood. Whole-draw edges
longer than 0.001% of height are checked. These are fixed diagnostic regions,
not skin-generation thresholds. Every comparison restores positions and normals
exactly. All solves cover their complete draw, with maximum residual below
`1e-10` and maximum unit-sum error below `2.2e-9`.

## Constraint sensitivity and decision

Moving labels to neighbouring vertices changes Odette's driver weights by up to
19.55 percentage points and Chisa's by 19.70. At 90°, Odette's maximum edge ratio
changes from 3.943 to 4.736 and wrist lag from 3.17% to 4.01%; Chisa's maximum
changes from 6.307 to 4.893 and wrist lag from 3.28% to 2.65%. The local hand
case is stable, while mixed-driver components are sensitive to constraint placement.

Extra constraints reduce Odette's 90° wrist lag to 2.10%, still worse than 0.69%
with soft transfer. Chisa's extra set reduces opposite-side forward movement to
1.58%, but retains 2.41% wrist lag at 90°, approximately 2% head-height movement
at forward/backward targets and strong elbow compression. Its sleeve probe also
changes to 1.450 at 90°. More labels improve selected quantities without passing
the checks together. The experiment stops here; larger label sets, automatic
anatomical labels, alternative conductance rules and fallback methods were not tried.

The data supports continuity within a known rigid region. A few point constraints
do not establish acceptable bending and isolation within a large connected
surface. This result does not justify extending generated weights to the full
body or building a permanent graph-processing subsystem. The fitter, IK,
deformation prototype and production Rig remain unchanged. PR #180 stays open.

## Reproducing the weights

Install `tools/requirements-dev.txt`, then read the selected draw with the
inspector's `read_surface(mod_folder, mesh_key)`. Match both evidence hashes.
Load `harmonic_probe.py` with `runpy.run_path`, translate a recorded label set
into `{vertex: driver_index}`, and call `propagate` with the returned positions,
triangles, indices and weights. It returns generated weights plus diagnostics.
The evidence supplies rest/posed controls and recorded measurements, but using
them with `buildHumanoidDriverFrames` and the cached position/normal deformation
kernels requires a separate replay harness. Running this probe alone does not
independently reproduce the deformation metrics above.
