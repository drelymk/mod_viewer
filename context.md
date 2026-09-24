# 3DMigoto Mod Viewer - Project Context

A pywebview desktop viewer for ZZZ/ZZMI, Genshin/GIMI, WuWa/WWMI and HSR/SRMI
mods: Three.js/WebGPU rendering, archive previews, animation reconstruction and
staged INI/mesh editing.

Keep this file limited to durable contracts and non-obvious failure modes.
Use source and README for implementation details and usage. Keep credentials,
private environment details, machine-specific paths and local test results out
of documentation, comments and tests; use portable fixtures instead.

## Trust boundaries and shared state

- Keep core analysis GUI-free. The bridge authorizes native-picker paths and
  canonical descendants of registered Mod Folder roots before loading, metadata
  writes or export. Browser-invented paths are never authorization. Promoted
  exact session paths survive registry edits/removal; adding or changing a root
  path requires the native picker. Keep the native window private at `_window`.
- Keep three distinct boundaries: picker/root authorization,
  `core.resource_paths.safe_resource_path()` for mod resources, and the server's
  static-root join. Do not duplicate their rules or merge their responsibilities.
  Mod resource resolution rejects absolute/drive paths and excessive traversal.
- Serve the privileged UI from ephemeral `127.0.0.1`; no `NavigateToString`,
  runtime CDN or third-party scripts. Publish geometry through one shared
  localhost blob, without base64 round trips or internal builder keys in payloads.
- Share one canonical analysis per INI across controls, draws, shapes and
  resources. Never share discovery state across sibling INIs or repeat semantic
  stages on the normal load path.
- Diagnostics are lazy, read-only, detached reports cached by edit-session
  revision. Commits invalidate them; diagnostic failures must not fail mod loads.
- App config is versioned and atomically replaced. Missing config uses defaults;
  malformed/unsupported files remain untouched. Panel opacity is omitted until
  changed, then persists explicitly even if restored to its default.
- Mod Folder browsing lists immediate directory children deterministically and
  skips symlink escapes. Navigation must not load/validate mods, discover INIs
  or expose root Edit/Delete actions on ordinary children.
- ZIP, 7z and RAR mods are virtual, read-only `ModSource` instances. Normalize
  member paths, reject traversal and case-ambiguous members, and keep resource
  lookup inside the archive. Preview and diagnostics may read them; Export,
  metadata mutation, mesh editing and texture saving must remain unavailable.

## Lossless staged editing

- `core.ini.parser` is read-only analysis. All INI edits use `IniDocument` and
  the shared `app.session.edit` session. Preserve BOM, mixed line terminators
  and absent final newline byte-for-byte on untouched round trips.
- Report ambiguous/malformed structure through `structure_errors()` instead
  of guessing rewrites. Single-section accessors select the first duplicate;
  operations needing all duplicates must iterate the document.
- Reload, diagnostics, Toggle CRUD and Record consume staged documents.
  Reopening the current mod must not overwrite them from disk. `peek()` exposes
  live staged state; all edits use begin/commit/rollback, atomically across INIs.
- INI and index-buffer edits share the staged session and reload through its text
  and byte overrides. INI writes happen only at Export: write each dirty document
  once, create its timestamped `.BAK`, leave failures pending, and exclude backups
  from discovery. A confirmed mod switch or restart discards the session; there
  is no standalone Discard action. Viewer metadata and confirmed texture saves
  have separate writes.
- Namespaced globals are cross-INI and read-only. Toggle CRUD targets only plain
  variables declared in that INI. Existing unwired utility keys stay hidden;
  newly added unwired keys appear only while session-pending and block Export.
- Toggle identity is its `[Key...]` section and complete variable tuple, never
  one variable. Resolve duplicate tuples to their last cycle position.
  `[KeyModViewerPresent]` is one aligned mod-wide cycle with atomic fan-out;
  incomplete/misaligned state stays visible but unapplied.
- Record regenerates only provably safe single-variable if/elif chains. Refuse
  ambiguous or partially observed cases, independently reparse the result and
  roll back failed verification. Toggle controls use assignable `.onclick` so
  Record can replace and restore exactly one handler.
- Persisting separated mesh parts requires a writable, authored draw with one
  current canonical identity and exact source provenance. Accept only a complete,
  non-overlapping partition of every authored triangle in a 16- or 32-bit IB;
  reject synthetic/stale draws, partial overlap with another draw, duplicate
  submissions and cross-component requests. Reorder only that draw's raw triangle
  records and rewrite every authored `drawindexed` source in one transaction.
- Stage dependent INI and IB changes atomically. Export revalidates the IB source
  hash, writes a collision-safe sibling backup, verifies an equal-length temporary
  file and atomically replaces the buffer before its dependent INIs. A failed IB
  blocks those INIs; a committed IB remains tracked and revalidated until all of
  its dependent INIs export, so retry never applies the byte edit twice.

## Parsing, execution order and identity

- `canonical_var_names()` governs case-insensitive spelling in toggles,
  defaults, menu state and DNF. Partial normalization can make gates fail open.
- Preserve `SrcLine` provenance through `line_source()` and every merged draw's
  full `sources` list; edits fan out to all authored sources. Draw labels are
  unique across all active INIs and distinct from UI display names.
- Recognize `elif` and `else if`. Draw DNF preserves contradictions and treats
  empty DNF as true; only state-rule DNF may discard contradictory groups.
- Menu recognition requires nested self-assignment structure and outer guards.
  Increment/modulo cycles expose all `0..N-1` positions. Unsupported `&&` forms
  may weaken a guard rather than hide geometry; do not guess menu structure.
- Texture bindings are per-draw execution-order state: retain the latest
  applicable assignment, conditional alternatives and no-map fallbacks for
  every role. Never collapse bindings to a component-level texture.
- Follow explicit `Resource = copy ...` and compute UAV copy/ref chains when
  resolving authored geometry and animation sources. Preserve ordered alternatives,
  stop cycles, and never infer a source through an unsupported shader operation.
- A direct root INI anchors bounded depth/count discovery; do not merge unrelated
  library/category folders. Resolve resources relative to the declaring INI,
  but publish resource/editor identities relative to the selected mod root.

## Geometry and texture loading

- Decode conservatively by supported format; reject malformed, unknown or tiny
  buffers safely. New explicit/typed geometry recognition must not erase
  previously supported geometry merely because recognition fails. Validate
  geometry changes against representative compatibility cases; unexplained
  geometry loss is a regression.
- Preserve authored normals; reconstruct only as fallback and never weld solely
  for smoothing. Normalize winding independently of authored normal direction.
- Deduplicate normalized effective buffer/material state. Classify new draw
  fields explicitly as render identity, visibility or provenance; do not derive
  identity reflectively from every field. Preserve numeric VB slots and the
  distinction between untouched and explicit null; VB changes need no IB change.
- An IB section without `drawindexed` may emit one synthetic whole-buffer draw;
  `handling=skip` without an explicit draw emits none. Authored rows retain
  `count,start,base`; only synthetic rows receive generated display markers.
- Apply shapes only with complete recognized buffer layouts. Other slider-like
  variables remain controls; shape targets cannot leak between position buffers.
- Resource identity is mod-relative, never basename-only. Texture keys are
  `role::mod-relative-path` for `diffuse`, `normal_map`, `normal_data`, `light_map`,
  `material_map` and `emission_map`. Diffuse is sRGB; auxiliary roles are non-color
  data. Normalize legacy path-only keys using the caller's known role.
- Candidate discovery supplies viewer choices without inferring semantic
  bindings. Keep texture processing independent of game/material interpretation.
- WWMI mod candidates use declared component-named Resource files across active
  INIs, then replacements matching exact Asset TextureUsage hashes, then images
  containing those hashes in the matched Asset metadata directory. Resolve each
  INI's index against its own rebased resources; aggregate only filenames and
  resolved indexes. Keep these candidates in the existing texture pool, with
  Asset identities and labels distinct from mod files, and never scan loose mod
  files or infer automatic roles from these associations.
- Texture pools and backend loading must not eagerly decode/render sources.
  Production texture rendering stays at two concurrent jobs unless controlled
  benchmarks justify changing it. Native DDS uses validated eligibility only;
  model DDS must be supported, no larger than 8192 in either
  dimension, and structurally valid; otherwise publication rejects it. Menu
  DDS alone uses the existing lazy 256px PNG thumbnail path.

## Animation reconstruction

- Baked clocks, GIMI compute animation and WWMI sparse shape animation are
  conservative reconstructions, not a general INI or shader interpreter. Emit
  typed, ordered programs only for verified condition syntax, shader adapters,
  resource layouts, strides and file sizes; reject an unsupported or ambiguous
  chain without weakening its guards or hiding otherwise valid static geometry.
- Keep discovery and resource state per INI. Nested GIMI children may inherit
  only the validated parent bindings they do not replace. WWMI sparse tracks must
  match the narrow Present/two-pass shader contract and the exact shape-buffer
  layout. A matching authored plain slider with an externally driven phase remains
  a slider; synthetic sinusoidal or program-assigned phases remain compute tracks.
- Execute program assignments, resets, conditions and dispatches in authored
  order. Controls remain external inputs and state rules are the shared derived
  dependency model. Tracks from one program share program state while retaining
  independent output identities; visibility and control changes wake existing
  tracks rather than rebuilding meshes.
- Mutate stable position/normal attributes from an immutable canonical baseline.
  Sparse WWMI output overlays shape/rest state; disabled passes freeze without
  erasing other passes. Rig/Physics temporarily suspends animation ownership;
  release restores canonical geometry and bounds before resuming. Advance program
  time at render cadence but cap expensive compute-geometry application at 30 Hz.

## Asset loading and composition

- Index Assets through metadata only. Heavy geometry/textures require explicit
  indexed-Asset loading or the explicit missing-original-parts action.
  Direct Assets never synthesize INIs/mods, modify sources or write
  `.mod_viewer.json`; source paths stay within registered roots and texture
  choices remain session-only. Ambiguity may yield candidates, never bindings.
- Compute coverage from authored geometry override identities across all INIs,
  including staged documents and `handling=skip`, rather than rendered draws.
  Hash-only geometry overrides cover every range under that hash; texture-only
  hashes identify Assets without claiming geometry. Automatic filling requires
  one unique Asset and retains component/range provenance; filled geometry is
  session-only and removed on reload/switch.
- GIMI head-local faces may use one geometry-derived rigid alignment from native
  full-body Eyes and a Face/FaceEye anchor. Transform positions and normals,
  preserve UVs/winding, leave native Eyes untouched and use no character offsets.
  Selective loads may read alignment dependencies without emitting them.
- Asset UVs are viewer-space Float32 with V flipped exactly once. GIMI/ZZMI
  ranges resolve by parsed IB header identity; WWMI `Component N.fmt/.vb/.ib`
  supplies local geometry while metadata offsets remain provenance.
- GIMI/ZZMI missing texture records may use only a unique range-matched IB dump
  family; authored hashes win and ambiguous families stay unbound. Validated
  immediate-component `hash.json` records retain their own metadata provenance
  and the selected Asset root.
- GIMI/ZZMI geometry dumps require authored hashes, never same-label fallbacks.
  Parse same-hash IB candidates independently so malformed siblings cannot
  discard valid ranges; missing counts use the resolved IB header count.
- WWMI metadata components recover independently. Texture candidates are rooted
  at the registered Asset Folder, using only `Components-N...` ordinal matching;
  exclude unknown filenames and never infer roles from that association.
- Report recoverable part failures as Asset warnings; fail the load only when
  no renderable parts survive.

## Texture color preview and saving

- Color adjustments are per-mesh diffuse previews stored by metadata identity in
  `.mod_viewer.json`; preserve unrelated data and omit neutral entries. Disable
  them for Asset/no-diffuse meshes. Reset Color clears only the preview.
- CPU save and GPU preview share normalization and order: optional target tint,
  then hue, saturation, brightness, contrast and RGB channels in editor-sRGB
  while preserving alpha. Brightness is 0–4 with a nonlinear slider centered at
  100%. Convert only at shader boundaries; picker hex is already sRGB. Update
  stable material nodes without recreating textures.
- Save to Texture has separate confirmation and immediately writes one mod-owned
  BC7 UNORM/sRGB DDS, independent of INI Export. Include hidden changed meshes
  sharing it, flush preview metadata first, renew review when targets change and
  block dismissal/duplicate submission while saving.
- The backend authorizes the mod, validates canonical texture/mesh/metadata IDs
  and the complete role snapshot, and derives UV coverage from authored geometry;
  browser paths/UVs are never authority. Reject Asset/stale/non-BC7 sources,
  unknown coverage, conflicting overlaps, and any physical DDS also used in an
  auxiliary role, including inactive variants.
- Recolor only covered BC7 blocks while preserving layout, headers, alpha and
  unrelated blocks. Pad one intent across valid pixels of a partial block, retain
  per-pixel targets for multiple intents, propagate weighted intent through mips,
  and keep the source block when refitting worsens RGB error.
- Validate temporary layout and source hashes, create a collision-safe timestamped
  sibling backup, then atomically replace. Abort stale writes. Once replacement
  occurs it is committed even if cleanup fails; never invite a second application.
- After commit clear only saved preview metadata and reload affected keys in place.
  Async completion must recheck current mod/target state before clearing live
  previews. Metadata reset or refresh failure remains a committed save with a
  visible warning and backup-based recovery; Reset Color is not recovery.

## Material interpretation and rendering

- Use vendored Three.js WebGPURenderer/TSL Node Materials; verify the initialized
  backend and never silently fall back to WebGL2. Game, runtime, texture API and
  material kind are distinct. Resolve per-mesh profiles from structural evidence
  and `(game, texture_api, material_kind)`, with validated base fallback; weak
  component names or filenames cannot activate specialized semantics.
- Deduplicate immutable `material_profile_id` metadata; conflicting IDs are
  programming errors. Keep packed RGBA and role bindings intact, without mapping
  channels to stock PBR inputs absent evidence. Binding updates use stable
  texture/enabled nodes and valid placeholders, never material rebuilds or
  generated-shader patches.
- Genshin LightMap R controls response, G toon shadow, B specular area and A
  regions. ZZZ LightMap G supplies conservative metalness and MaterialMap B
  specular response; MaterialMap R stays ID data and G stays packed/unknown.
- Genshin and ZZZ reconstruct authored `normal_map` RG in TSL. WuWa
  reconstructs authored `normal_data` RG in TSL; B/A remain diagnostic or
  profile-specific. RabbitFX uses LightMap G for shadow; its base is shadow-only
  and Normalmap B/A response requires reliable exact body-profile evidence.
- Gate diagnostic modes by material capability; unsupported modes preserve
  normal rendering. Diagnostic-only channels must not trigger startup requests.

## Weight and secondary motion

- Advertise only usable authored Blend streams. First Weight access lazily decodes
  one model-wide blob with per-mesh ranges; failures degrade the feature, not model
  loading. Bone identity is mod-relative Blend source plus resolved offset; IBs
  only preserve compact vertex mapping. Selection is exact-source-scoped and
  persists without dropping unrelated metadata or filtered choices. Picking is
  distance-weighted on the exact hit mesh within 2% of model radius, with triangle
  fallback; discovery never selects bones or enables Physics.
- One rig per exact skinning source owns centers, topology, Physics and transforms;
  hidden members still contribute, while each mesh uses its authored weights.
  Use triangle-integrated evidence only when every member has positive-area
  geometry, otherwise fall the entire source back to vertex evidence. Never mix
  modes or mix vertex-weighted panel statistics into the Rig graph. Deduplicate
  exact members for evidence but keep provenance. Infer only conservative
  maximum-spanning topology from overlap/centers: Blend data provides no names,
  canonical skeleton, hierarchy, bind pose, animation or semantic labels.
- Cross-source reconciliation is a viewer-owned graph over source rigs. Preserve
  `SourceBoneRef {sourceKey,boneId}` and `${sourceKey}#bone=${boneId}`; only
  validated all-VertexVG models may group equal numeric IDs directly. Otherwise
  require normalized mutual-best geometry/topology evidence, one member per
  source, ambiguity rejection, a maximum-spanning forest and cycle-free boundary
  attachments; retain rejected evidence for diagnostics and never rewrite authored
  indices/weights. Cross-palette posing may stretch where inferred and authored
  topology differ. `ModelJoint` signatures sort canonical source-bone keys and
  deterministic `joint_id` values hydrate Main Rig mappings. Persist structure in
  `.mod_viewer.rig.json`; reuse only for matching format, builder and source table.
  Automatic asset-change detection is deferred.
- Rig presets use `rig.version = 1` in `.mod_viewer.json`: stable IDs, bounded
  names, explicit root signatures and normalized non-identity local quaternions.
  Preserve unrelated metadata and IDs on rename; resolve entries exactly by
  signature and skip malformed/ambiguous ones without failing model load. Never
  auto-apply after load/rebaseline. Apply valid state as one restore, cache rebuild,
  deformation, bounds and notification transaction.
- `ModelSkinningRig.poseRotationByJointId` is authoritative; source pose maps are
  aliases. Compose manual transforms with source-scoped Physics offsets in one
  authored-baseline pass for positions and normals. Never skin deformed geometry,
  renormalize weights, move unselected influence, add depth-derived mobility or
  let Physics replace manual pose. Solver vectors stay in model space and are
  conjugated only by accumulated parent Physics delta. Nonempty Weight selection
  enables Physics; empty disables it. State changes wake rather than restart the
  simulation. Reset Physics affects only motion; Reset Pose/Joint affects only
  manual state, and presets never serialize live Physics offsets.
- `HumanoidControlRig` is the primary automatic skeleton: fixed 16 controls fitted
  from immutable A-pose geometry/orientation, independent of non-semantic
  `ModelJoint` topology. Edit Rig starts at rest; control overrides and presets
  remain separate. Binding uses ordered direct ownership, nearest normalized
  anchors and boundary-aware descendant inheritance with
  `inverse(restDriverWorld) * restJointWorld`; convert posed targets to authored-rest
  deltas before source aliasing. IK depends on accepted controls, uses virtual
  two-bone controls and composes before manual pose/Physics. Metadata save/reset
  refreshes mappings and pose on the loaded ModelRig without reconciliation.
- Rig snapshots expose only panel/overlay view models; evidence and performance
  stay private. Joint selection is independent of pose, Weight selection, Physics
  and overlay state; Reset Joint/Pose preserves selection and saved presets. Keep
  O(1) overlay objects. Reconcile only for absent/incompatible sidecars. Step each
  source rig once at fixed 1/120 second with bounded catch-up; deform visible
  selected-weight vertices and baseline normals, defer exact bounds until settling,
  retain conservative culling and update character shadows every deformation frame.
- Shape changes rebaseline positions/normals and lazily rebuild affected rigs and
  surface evidence. Material, texture, visibility, pose and animation-frame changes
  must not redefine rigs. Release participants before disposing member geometry.

## Frontend ownership

- Manual textures persist as viewer metadata: undefined is automatic, null is
  none, a key is sticky. Clearing restores the immutable draw default; automatic
  highlighting follows the live resolved key.
- Automatic texture runs follow ordered authored binding identities, not the
  first occurrence of each resolved texture key. A later repeated key starts a
  new run when its default/conditional binding differs, and in-place semantic
  refresh must recompute those boundaries before reconciling the run.
- Environment, outlines and render modes are viewer state, never staged INI or
  material reinterpretation. Outlines use child inverted hulls sharing geometry;
  wireframe/debug suppression retains the user's preference.
- MESHES provides navigation, Inspector owns mesh/material/texture/color editing,
  and Controls owns Present/Toggle/Menu. Group Meshes/Toggles by subfolder, root
  INI stem or None for a single INI; retain source INI rows. Selection is an
  event-driven set with one primary Inspector target: plain click replaces,
  Ctrl-click toggles and Ctrl-drag additively selects visible geometry crossing
  the viewport rectangle. Rig picking/dragging owns its gestures and must block
  view selection. Visibility, reset, refresh and texture mutations publish shared
  state notifications so Inspector and rendering stay synchronized.
- Loose-part separation is transient viewer state. Detect connected triangle
  islands from exact positions or a bounded user tolerance, retain original
  triangle ordinals, share the semantic source's attributes/material/state, and
  create no authored draw or independent metadata identity. Merging is allowed
  only for selected siblings from one source and preserves their stable source
  order. Clearing/disposal restores the source draw range and selection safely;
  only explicit Apply enters the staged mesh-edit path.
- Tabs, panel collapse and library expansion live only in localStorage and
  cannot affect mod state, geometry, materials or Export. Global panel opacity
  belongs to app config under the persistence rule above.
- Runtime UI text goes through the locale catalog with placeholder-compatible
  translations and English fallback. Language is app configuration, never mod
  state; do not localize stable resource, mesh, section or metadata identities.
- Reset/Turn/Tilt stay in the viewport toolbar. Apply auto-upright, game/base
  facing and manual rotation in that order, including late-adopted meshes.
  Reset retains the base transform; removed meshes leave the reset baseline.

## Builds and test discipline

- `features.ini` is build-time only: bake a temporary module and remove it after
  building. Source enables all features; frozen UI gates never replace backend
  authorization. Missing/malformed values default to enabled.
- Keep the default pytest suite self-contained; real-mod corpus checks are
  opt-in via `MOD_VIEWER_TEST_CORPUS`. Follow AGENTS.md for the test environment.
  Use one scenario for consecutive lifecycle states and parametrized tables for
  pure input/output matrices. Avoid duplicate coverage and tests that merely
  mirror constants; retain independent security, atomicity, race, public-contract,
  corpus and rendering regressions in the nearest existing test module.
- Browser UI/rendering tests use the real local server, vendored assets and
  compatible Edge, with generic skips when unavailable. Pure JS contracts use
  the lightweight module fixture. Bypass ambient proxies for loopback traffic;
  prefer observable readiness over fixed sleeps. Mock `window.pywebview.api`
  for UI state without importing GUI-bound `app.bridge.api` into browser fixtures.
- Benchmark texture concurrency, lazy loading or transport changes with
  `tools/benchmark_texture_pipeline.py`. Keep formatting/lint/mechanical checks
  in CI and focus review on consequential behavior and these contracts.
