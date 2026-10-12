# Surface continuity investigation

The existing data supports testing continuity on a surface graph. It does not
establish automatic anatomical regions, and does not justify a new production
binding. The Geometry Rig fitter, IK, direct-deformation prototype and production
Rig remain unchanged. This investigation follows the locality review at `dfbc053`.

## Scope and reproduction

[`tools/inspect_surface_continuity.py`](../../../tools/inspect_surface_continuity.py)
reads one resolved rest draw and its retained compact-to-authored vertex mapping.
It uses the shared skin decoder, including WWMI VertexVG remapping. It does not
construct ModelRig, pose a model, load textures, infer visibility or write mod
files. Overlap metrics normalize detached analysis copies; seam compatibility
compares the original weight magnitudes, including their totals.

The small [case artifact](continuity-cases.json) records the three failure cases,
source identities, original vertex mappings and hashes of packed geometry and
decoded skin streams. Supply the corresponding mod folders to reproduce:

```text
python tools/inspect_surface_continuity.py REMIELLE_FOLDER --mesh RemielleMoonlightWhispersbodyA-1 --edge 765 808 --edge 808 348
python tools/inspect_surface_continuity.py ODETTE_FOLDER --mesh OdettebodyB-1 --edge 121 138 --edge 88 86
python tools/inspect_surface_continuity.py CHISA_FOLDER --mesh Component3-6 --edge 6682 6688
```

Vertex numbers are compact indices in those resolved draws, not global authored
indices. Match the artifact's hashes before interpreting numbers from a different
archive or edited mesh. The cases are Remielle (ZZZ), Odette (Genshin) and Chisa
(WuWa); the tool has no model-specific logic. Each report is limited to its selected
draw, including all its triangles. It does not assert that other draws, hidden
variants or intersecting surfaces have the same properties.

## What connectivity establishes

An indexed graph connects vertices that share triangle edges. A position-only
comparison also joins exact coincident vertices. A stricter comparison requires
an entire coincident edge; its authored-compatible version additionally requires
identical source-scoped authored vectors at both corresponding endpoints. These
are diagnostic comparisons, not automatic attachment decisions. No distance
tolerance is used, and geometry is never welded or rewritten.

| Case | Indexed islands | Position-only islands | Exact-edge islands | Authored-compatible edge islands |
| --- | ---: | ---: | ---: | ---: |
| Remielle hand | 24 | 2 | 2 | 2 |
| Odette elbow | 212 | 11 | 11 | 11 |
| Chisa sleeve | 323 | 6 | 8 | 8 |

All inspected failure edges are genuine triangle edges inside a single indexed
island. They are not gaps between disconnected endpoints. Whole-island ownership
therefore cannot fix their inconsistent generated weights.

Remielle's hand edge lies in a 402-vertex indexed island. Compatible seam links
join 12 indexed islands into a 1,036-vertex component. All 430 duplicate-position
pairs have different UVs but identical authored vectors. Influences 166 and 194
span multiple indexed islands but each stays within one compatible-edge component
in this draw. The failure edge's authored-vector overlap is about 0.400 despite
both endpoints having pure forearm spatial support in the earlier trace. Different
authored mixtures need not imply different simplified-driver motion within a hand.

Odette's stretched and compressed elbow edges lie in the same 218-vertex indexed
island; compatible seams extend it to 246 vertices. All 2,088 duplicate-position
pairs have identical authored vectors, including 1,489 pairs with different UVs.
The stretched edge's authored overlap is about 0.734; the compressed edge's is
0.912. Influence 26 spans 81 indexed islands but only two compatible-edge
components. About 64.9% of its triangle-integrated support belongs to the probe's
component. A shared influence must not create an attachment to the other component.
Influences 30 and 59 remain within the elbow component in this draw.

Chisa's sleeve edge lies in a 557-vertex indexed island. Compatible seam links
join 298 indexed islands into a 33,716-vertex component out of 35,014 draw vertices.
Its endpoints have authored overlap about 0.569. Influences 357 and 358 each stay
within this large component after seam linking. Connectivity cannot distinguish
the sleeve from the other connected regions. Of 3,694 duplicate-position pairs,
two have different authored vectors; even a coincident position is not an exact
co-deformation guarantee. Position-only joining also gives fewer components than
complete-edge joining, so isolated contacts must not be treated as seam proof.

No case contains duplicate triangles with identical three rest positions within
the selected draw. This rules out exact duplicate triangle records in that scope,
not differently triangulated overlays, surface intersections or separate draws.
Those attachment questions remain unresolved.

## What authored relationships establish

Coincident points with identical weights over the same resolved source influences
receive identical deformation for any transforms of those influences. This is
useful seam-continuity evidence without ModelRig or influence-to-driver ownership.
UV differences alone do not invalidate it. Equal vectors on distant surfaces,
however, do not prove that those surfaces are physically attached.

For a triangle edge, the shared normalized weight mass
`c(u,v) = sum_b min(w(u,b), w(v,b))` measures common authored transform support.
The three failure edges retain positive common support despite different mixtures.
This supports testing a continuity penalty along actual surface edges. It does not
provide an upper-arm/forearm label, a bind pose or semantic anatomy. Numeric IDs
remain source-scoped; remapped model-wide IDs must still come from the shared
decoder, not from matching raw slots or basenames.

## Smallest practical next test

Use a detached graph for each selected rest surface, with triangle-edge adjacency
and explicitly inspected compatible seam links. Do not add edges between separate
components merely because they are nearby or share an influence. Exact overlapping
surfaces also require inspection before treating their coincident edges as seams.

For this experiment, mark a small set of inspected stationary, upper-arm and
forearm vertices as fixed boundary values. These are per-vertex experimental
labels, not source-influence owners or persisted production Rig mappings. Test one
constrained harmonic extension of the three driver weights by minimizing
`sum_(u,v) c(u,v) * ||W(u)-W(v)||^2` on the graph, with nonnegative, unit-sum
weights and the boundary labels held fixed. This changes neither the authoritative
fitted skeleton nor the existing deformation kernels. It adds no spatial radius,
distance blend, model exception or fallback chain.

This test must explicitly report components without adequate inspected labels,
zero-conductance cuts, incompatible seams and disconnected garments as unresolved.
Do not fill those gaps by proximity or influence-wide averages. Within a large
connected component such as Chisa's, stationary constraints are essential: graph
connectivity alone can still diffuse arm motion into connected torso or clothing.
Smooth elbow blends also require both arm regions to be constrained; uniform
component assignment would lose the bend.

Compare the same left-arm poses across all three models: 15°, 45°, 90°, forward,
backward and exact return to rest. Check the existing failure edges, shoulder and
elbow compression/stretch, wrist following and hair/torso/clothing movement.
Use inspected labels first to determine whether the graph can satisfy those
constraints at all. Automatic label discovery is a separate unresolved problem.
No graph-based deformation method has been implemented or accepted in this study;
PR #180 remains open, with no full-body expansion or production replacement.
