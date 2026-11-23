# ZZZ (ZZMI) game extraction

Shared rules: [game-extract.md](game-extract.md). ZZMI mod palette proof is in
[weight-rig.md](weight-rig.md); playback in
[animation-playback.md](animation-playback.md).

## Reader and index

- A separate minimal native reader built from pinned original AnimeStudio source,
  its rebuilt ZZZ V2 ACL decoder and WuWa's pinned Oodle decoder; no GUI, forks,
  media exporters or updater.
- Index complete local block metadata keyed by runtime plus archive names, sizes
  and mtimes. Keep block path, bundle offset, internal CAB, file ordinal and PathID
  together; never collapse duplicate CAB names. External resource streams stay in
  their bundle. Reuse a valid index across extractions and client lifetimes; index
  compatibility has its own reader version (clip-only runtime changes do not
  invalidate it). A schema/runtime change needs one successful rebuild; index
  failures stay visible and abort extraction, never retried as an index-free scan.
- Pool repeated renderer bone-path strings losslessly, restoring exact ordered
  paths with bounded tables.
- The file table lists every serialized file holding a Material or Texture2D (block,
  bundle offset, ordinal) so prop material/texture pointers resolve; a CAB in
  several bundles is read from each copy and must agree. The scan covers every block
  (any invalid row fails the build) but `zzz_index.limit` keeps only what extraction
  reads: `Avatar_…` family Avatars, meshes a family name hint discovers, the
  families' `<family>_Ani_` clips, renderers whose Avatar pointer (case-insensitive
  CAB + PathID) names a kept Avatar, and every AnimatorController. The file table
  stays complete; kept rows keep every copy, so ambiguity is unchanged.

## Avatar and mesh proof

- Only shape-plausible character families need Avatar reads; shapeless families
  cannot block another verified model, but a shape-compatible unreadable candidate
  fails closed. Read skeleton, reference pose and exact path-hash table without
  needing HumanDescription; accept constants with or without the root-motion flag
  only through a unique object-bounded path table (valid UTF-8, unique CRCs,
  complete hash coverage).
- Prove captured positions, UV triangles and positive float weights before adopting
  a sparse palette. Path hashes and all used inverse binds establish one common
  mesh-to-Avatar basis: bones carrying at least 99% of positive weight agree within
  tolerance (2e-4) and fix it; the rest may drift only within a small bound;
  non-finite binds never agree. Keep the basis as an extra root rather than
  rewriting geometry. Resolve symmetric coordinate hypotheses model-wide. Bind-pose
  arrays keep ZZZ's serialized row-major order; never transpose by field names.
- Equally supported incompatible placements stay ambiguous. A larger animation
  Avatar may replace the model Avatar only with identical names, ancestry and
  remapped palettes and identical reference worlds for proven palette bones and
  ancestors (unskinned helpers may differ). Two model or two animation Avatars need
  identical worlds for every bone. Versions of one Avatar object (same CAB/PathID)
  in different blocks: the most recently written block wins; unreadable or tied
  times stay ambiguous.
- Legacy and short Unity mesh names are discovery hints only; include legacy meshes
  named for child prop/variant families when scoring a body Avatar. Equivalent
  copies in one family are chosen deterministically by hierarchy/world/palette proof
  (float noise included); different families or skeletons stay ambiguous. Reuse a
  reference pose only when the proof keeps every bone index.
- Decode ACL, dense, streamed Hermite and constant tracks into endpoint-inclusive
  TQS samples.

## Facial curves

- Need captured-geometry correspondence, native shape hashes and a renderer path
  under the proven Animator Avatar. Resolve mesh pointers through complete locators;
  a streamed null-mesh renderer may prove its mesh through its complete ordered bone
  paths (and exact default-weight count when serialized). Several proven copies form
  one route only when their channels, hashes, weights and vertex sets match on every
  captured part and deltas lie within 1e-6 of one fixed representative (never
  chained). Ambiguous references or incompatible targets stay omitted.
- Single-frame shapes keep their full-weight scale and axis reflection; in-between
  shapes are never flattened. Read `legacyClampBlendShapeWeights` lazily from
  contained `globalgamemanagers`: clamp to 0..1 only when true, keep signed weights
  when false, refuse unknown policy; engines before Unity 2018.3 (by serialized
  version) always clamp. Record the policy in clips; it applies equally to runtime
  data and facial GLB.
- Each facial clip carries a contained, checksummed playable GLB with an embedded
  sparse runtime view, checked lazily, using the shared morph-before-skinning
  lifecycle.

## Controllers and sequences

- AnimatorControllers are indexed (`zzz-4`) and read with the Genshin controller
  graph; sequences follow the controller rules in
  [game-extract-gimi.md](game-extract-gimi.md) with `FrameCount` windows (60 Hz
  frames since step start). Sequences come only from the family's controllers
  (`<family>…_Controller…`, compared without the family's internal underscores and
  ending on a token boundary); names only pick candidates. Copies of one object are
  one controller; different objects sharing a name are skipped. Leaves bind by
  pointer to converted clips sharing one clip key; sequence names carry the
  controller suffix.
- Root motion: keep a clip's complete Animator `MotionT`/`MotionQ` muscle set (an
  incomplete set stays an omitted channel) as the optional root-motion track.

## Props

- A prop is a skinned native model mesh of the verified family that no captured
  component came from, whose inverse binds prove placement against the verified
  Avatar, and whose positive weight lies only on bones no captured palette uses.
  Names only discover; hair shadows, outfit variants and meshes driven by captured
  bones never qualify; static effect meshes are ignored. Geometry goes to asset
  space through the rig root and placement, with UVs and triangle order following
  captured conventions. Different native copies under one name are skipped.
- Name exception: candidates compete only when they route (by complete bone-path
  tuples) to the same verified-Avatar renderers; then, per renderer GameObject name,
  the best match stays (exact name over a legacy `..._Model_<name>` copy; ties all
  stay) and competitors matching none of their renderers best are skipped. A
  candidate no verified-Avatar renderer routes to is skipped without names.
- Textures come from materials of renderers proven to draw the prop (complete
  ordered bone paths plus an Animator Avatar among verified copies). A slot takes a
  role (diffuse, light_map, material_map) only when its top mip is byte-identical
  to a captured texture of that role on a proven body part (compare the captured
  DDS's top mip too), with no competing slot or role; routed materials must agree.
  Write the top mip unchanged as a single-mip DX10 DDS (`Props/<digest>.dds`, sRGB
  only for diffuse) under the prop's `textures`. Normal maps stay unproven, so props
  keep mesh normals. Multi-material meshes become one prop per section
  (`<mesh> <n>`, keeping only that section's vertices), each textured from its own material.
  Untextured props report `texture_error`.
- Visibility windows (clips have no visibility curves): manifest `visible` maps clip
  id to ordered disjoint `[start, end]` seconds (at most 64 per clip). A frame shows
  the prop only when the clip moves the prop's own bones (skin bones and ancestors
  outside the captured body chain) beyond 1e-3 from rest (skipped for parts without
  own bones) and the posed bounding-box centre is not below the Avatar ground; runs
  widen by half a frame. No window hides the prop; a prop no clip shows is not
  exported. Shared-library clips hide windowed props.
- Prefab context: the renderer read returns each prefab's default visibility and
  its Animator's controller. A controller draws props when a renderer routed to an
  exported prop is drawn by default in a prefab using it. A clip played only by
  contexts drawing no props hides every prop; clips no known context plays keep
  their windows; older readers without this data change nothing.
