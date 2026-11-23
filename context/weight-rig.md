# Weight, palettes and rigs

Authored weights, skeleton/palette proof, the model rig, posing and Physics.
Clip playback is in [animation-playback.md](animation-playback.md).

## Weight data

- Advertise only usable authored Blend streams. First Weight access lazily decodes
  one model-wide blob with per-mesh ranges from the loaded model's retained
  manifest (never rebuild geometry); failures degrade the feature, not loading.
  Inputs: GIMI float/u32, WWMI R8 (4/8 influences), WWMI R16 and rigid u32, all
  into one u32/float runtime representation.
- Bone identity is mod-relative Blend source plus resolved offset, namespace and
  verified layout domain; IBs only preserve compact vertex mapping. Selection is
  exact-source-scoped and persists without dropping unrelated metadata or
  filtered choices. Picking is distance-weighted on the exact hit mesh within 2%
  of model radius, with triangle fallback; discovery never selects bones or
  enables Physics.
- Large Blend streams without VertexVG remaps, and rest-position morph matching,
  may use bounded NumPy batches that must equal the scalar paths (per-vertex
  failures, `sum()` rounding, unique-nearest ties); unusual inputs take the scalar
  path. Surface graphs build on at most four same-origin module workers started
  with Weight loading (copying only each view's bytes), with the cooperative
  main-thread build as fallback. Keep source and member order.

## Source rigs

- One rig per exact skinning source owns centers, topology, Physics and
  transforms; hidden members contribute, each mesh uses its own weights. Require
  triangle-integrated evidence with positive-area geometry in every member; an
  unusable member disables Rig/Physics for that source only. Keep vertex-weighted
  panel statistics apart from the Rig graph. Deduplicate exact members for
  evidence but keep provenance.
- Without authored data, infer only conservative maximum-spanning topology from
  overlap/centers: Blend data has no names, hierarchy, bind pose, animation or
  semantics.

## Authored skeletons (`Rig.json`)

- Exact registered WWMI/ZZMI Asset/range bindings may supply a root `Rig.json`
  (`mod-viewer-rig` v1/v2, Asset coordinates), read only on Weight/Rig access from
  retained manifests through Asset containment. Validate ordered parents, finite
  translations, near-unit quaternions, uniform v1 reference scales (uniform mirror
  allowed) or nonsingular v2 affine scales, and reference VB/IB fingerprints. v2
  keeps nonuniform scale and sparse palettes.
- The palette binding must be proven by one of: an explicit Blend-content
  fingerprint; an Asset-declared stock WWMI merged palette with same-INI template
  proof; a stock ZZMI binding proven against captured native position/Blend hashes;
  or a native WWMI per-component palette (below). Unproven pipelines, unsupported
  namespaces, stale reference geometry and invalid sidecars keep inference.
- A ZZMI/GIMI section without `match_first_index` matches every draw of its hash, so it
  has no range: it is never exact evidence for an Asset, but when the mod's exact parts
  prove one Asset it binds to it with an unknown range. Its rig binds only through the
  one native stream every captured component of that hash shares (no attachment, no
  conflicting stream) and carries no morph reference. A first index matching no captured
  range stays ambiguous.
- Retain every proof in the loaded manifest/skinning source; never reread INIs on
  Weight access. Admission never depends on character/mod names or numeric ID
  ranges, and generic admission verifies every referenced captured VB/IB.
- A proven palette missing some used IDs still binds: those IDs get no joint, are
  reported in `game_unmapped_bone_ids` and their weight holds the bind pose.
- Meshes sharing a weight source may carry different sparse subsets of its bone
  map: merge them for source-Rig admission, keep each mesh's exact palette for
  playback, require every mapped ID to name a valid joint, reject conflicting
  aliases and never mutate per-mesh bindings. Authored graph adoption needs every
  source to resolve the same skeleton.
- Source IDs/weights stay unchanged; aliases supply exact parents, pivots, rest
  axes and names, including unweighted ancestors.
- Optional `humanoid_controls` maps all 16 controls to distinct hierarchy-checked
  joints, used with exact rest pivots and ownership boundaries; never infer from
  names. Only game extraction writes it, from exact `Bip001` biped names passing the
  hierarchy check, preserving existing mappings. Without semantics keep fitted
  controls and source Physics composition.
- Saved humanoid joint-ID mappings carry the skeleton fingerprint (transforms and
  semantics, not admission lists, so adding a verified mod keeps IDs). Switching
  authored/inferred skeletons discards IDs; valid semantic positions may rebind.

## Mod palette proofs

- WWMI native per-component palette: a same-INI `match_first_index` override in an
  INI that never runs a skeleton/Blend remapper or assigns `vs`, `vs-cb3/4`, Blend
  layout or palette variables outside custom shader sections, with an unaltered
  file-backed R8 Blend. Its domain names the root override and must equal the exact
  Asset binding; IDs outside the component's palette stay at rest.
- WWMI external calls are limited to WWMI core, RabbitFX pixel helpers, output-only
  built-ins and sibling pixel/draw lists. A sibling INI's list (unique namespace or
  unique un-namespaced global) may only branch, write unqualified/RabbitFX variables
  and IniParams, bind pixel/output-merger state (geometry stages only in custom
  shaders), draw and, if namespaced, write its own resources; never compute, UAVs,
  VB/IB or qualified writes. The skeleton contract itself stays same-INI.
- WWMI stock merged adapter: frame-state and merge-status programs verify merger
  inputs/outputs, publishing, native CB/Blend bindings, direct or verified-helper
  draw ownership and every component's merge offset/count (skipped ones included).
  Layouts may exceed 256 bones (each component at most 256, total at most 1024);
  out-of-range writes drop. Verified shared R8 streams keep raw palette IDs without
  adding `vg_offset` (which stays provenance); namespace/domain keys separate them
  from legacy offset-decoded sources. Translate saved legacy Weight IDs once, only
  for exact loaded verified sources, without writing metadata on load; presets keep
  unambiguous legacy aliases.
- Stock remap adapters use full R16 VertexVG IDs with unchanged weights, verifying
  forward/reverse maps on positive influences; 512 default or explicit 512/1024
  maps, 4/8 influences, R16/R32 tables. Blend and VertexVG hold equal vertex counts;
  `custom_vertex_count` may overstate but never understate. Tables read lazily
  through ModSource/BufferStore; invalid or truncated maps fail Weight explicitly.
  Shared Asset palettes may reach 1024 entries when a verified proxy program
  projects each drawn palette to R8.
- Exporter variants that cannot change bone-ID mapping keep the proof (mesh scale,
  CB copies with/without `ref`, spacing, dotted names, pixel/flag state, ignored
  bare words, merge-only components, null proxies, unused remaps, neutral command
  lists). A component's proxy triple may follow unconditional pixel setup but must
  be unconditional and precede the shared binder. Pixel-slot save/restore helpers
  may use empty placeholders only when every consumer is pixel-only, never touching
  protected rig resources; their draws inherit the proof only when called from the
  proven override. Pixel-only commands are projected out of template matching but
  kept for protected-resource and remap validation; never project variable writes,
  geometry or compute. Blend stays file-backed; any other writer of vb4, `vs-cb3/4`
  or protected resources, or another merger/binder run, rejects.
- ZZMI merged palette: a root override's closure runs one skin list whose exact
  reference kernel reads a file-backed 32-byte Blend and 40-byte position stream
  against one merge list's output and binds the result as `vb0`. The merge kernel
  copies contiguous whole-bone row ranges, each from a resource written only by
  `copy vs-t0` under `DRAW_TYPE == 1` in one hashed override naming the native Blend
  stream whose captured palette maps that segment. The drawn stream must be a
  segment; uncaptured segments stay unmapped; nothing else in the closure rebinds
  `vb0`/`vb2`. Never infer a namespace from numeric IDs or offsets.
- ZZMI v2 mods otherwise need an exact Blend-content fingerprint or a same-INI
  file-backed vb2 binding whose captured position/Blend hashes all name one captured
  stream (uncaptured hashes are not evidence). Any raw-ID Blend layout qualifies
  (32-byte, packed 8/16-byte, rigid 4-byte); only 32-byte is inferred from Position
  files. A stream's mapping is the union of all its captured components (conflicting
  joints reject), applying even when another override draws it (that one gets no
  morph binding). Use canonical binding history; names and statement order are not
  evidence. Reject conflicting Blend writers/aliases, custom matrix/vertex programs
  and unknown helpers in the Blend pass closure; later overrides drawing the stream
  never gate it (ZZZ and GIMI). Menu shaders and position-only shape compute do not
  change palette identity. Absent hash-Asset ordinals resolve only through a unique
  geometry-hash/range match.
- GIMI mods prove a palette only through a stock skinning pass: a safe
  `TextureOverride` closure binding a file-backed unaliased stride-32 Blend at vb1
  with `handling = skip` and `draw = <count>, 0`; only stock texture/ORFix/NNFix
  helpers are allowed. First-index-only hash Assets match a unique captured
  component by geometry hash and first index.
- Direct indexed WWMI Assets keep private captured skin layouts and compact IDs,
  load weights lazily through containment and validate palettes against Rig.json
  reference hashes. Asset Weight/Rig posing is temporary; never persist selection
  or presets to an Asset.

## Model rig, posing and Physics

- Model Rig assembly is a viewer-owned graph over source rigs. Keep
  `SourceBoneRef {sourceKey,boneId}` and `${sourceKey}#bone=${boneId}`. Equal
  numeric IDs group directly only when every source declares validated model-wide
  IDs in one domain (R16 Blend, active VertexVG remap or one verified shared WWMI
  layout); different layouts never share numeric identity, and R16 IDs never get
  offsets added again. Otherwise joints and hierarchy stay source-local, each
  source binding independently to the same humanoid controls.
- Each source joint picks its nearest eligible control before local inheritance;
  partial sources never fill unrelated controls to reserve anchors. Explicit
  mappings claim their exact joint (the control stays eligible for other
  disconnected joints in that source). Never infer bone equivalence or cross-source
  attachments from vertex proximity; missing references never prove equivalence.
  Build deterministic spanning forests and never rewrite authored indices/weights.
- `ModelJoint` signatures sort canonical source-bone keys; deterministic `joint_id`s
  bind Main Rig mappings. Assemble lazily, in memory only (no model-rig cache);
  builder provenance versions saved mappings. Keep one source-to-joint Map plus
  diagnostic counts, no per-mesh correspondence. Collection and assembly share one
  cancellation checkpoint; only the public build boundary turns stale work into
  `null`. Root editing keeps synchronous orientation.
- Rig presets: `rig.version = 1` in `.mod_viewer.json` with stable IDs, bounded
  names, explicit root signatures and normalized non-identity local quaternions.
  Keep unrelated metadata and IDs on rename; resolve by exact signature and skip
  malformed/ambiguous entries without failing load. Never auto-apply; applying is
  one restore/cache/deformation/bounds/notification transaction.
- `ModelSkinningRig.poseRotationByJointId` is authoritative; source pose maps are
  aliases. Compose manual transforms with source-scoped Physics offsets in one
  authored-baseline pass for positions and normals. Never skin deformed geometry,
  renormalize weights, move unselected influence, add depth-derived mobility or let
  Physics replace manual pose. Solver vectors stay in model space, conjugated only
  by accumulated parent Physics delta. Nonempty Weight selection enables Physics,
  empty disables it; state changes wake rather than restart. Reset Physics affects
  only motion, Reset Pose/Joint only manual state; presets never store Physics.
- `HumanoidControlRig` is the primary automatic skeleton: 16 controls from verified
  Asset semantics, else fitted from immutable A-pose geometry/orientation,
  independent of `ModelJoint` topology. Authored controls reserve exact joints
  before inheritance; saved edits win. Edit Rig starts at rest; overrides and
  presets stay separate. Binding uses ordered direct ownership, nearest normalized
  anchors and boundary-aware inheritance with
  `inverse(restDriverWorld) * restJointWorld`; convert posed targets to rest deltas
  before source aliasing. IK depends on accepted controls, uses virtual two-bone
  controls with immutable rest lengths and a transported rest bend pole (full
  extension keeps the bend plane); authored limb drivers keep rest roll across
  axis singularities; IK composes before manual pose/Physics. Metadata save/reset
  refreshes mappings and pose without reconciliation. Drop joint IDs from older
  builders but keep semantic positions; mappings with malformed IDs or
  missing/unknown provenance stay unbound.
- Rig snapshots expose only panel/overlay view models. Joint selection is
  independent of pose, Weight selection, Physics and overlay; Reset Joint/Pose keeps
  selection and presets. O(1) overlay objects; reuse the in-memory rig until
  invalidated. Step each source rig at fixed 1/120 s with bounded catch-up; deform
  visible selected-weight vertices and baseline normals, defer exact bounds until
  settled, keep conservative culling and update character shadows every frame.
- Shape changes rebaseline positions/normals and lazily rebuild affected rigs and
  evidence; material, texture, visibility, pose and animation-frame changes never
  redefine rigs. Release participants before disposing member geometry.
