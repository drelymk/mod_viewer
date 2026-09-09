# 3DMigoto Mod Viewer - Project Context

A pywebview desktop viewer for ZZZ/ZZMI, Genshin/GIMI, WuWa/WWMI and HSR/SRMI
mods: Three.js/WebGPU rendering, menu previews and staged INI editing.

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

## Lossless INI editing

- `core.ini.parser` is read-only analysis. All INI edits use `IniDocument` and
  the shared `app.session.edit` session. Preserve BOM, mixed line terminators
  and absent final newline byte-for-byte on untouched round trips.
- Report ambiguous/malformed structure through `structure_errors()` instead
  of guessing rewrites. Single-section accessors select the first duplicate;
  operations needing all duplicates must iterate the document.
- Reload, diagnostics, Toggle CRUD and Record consume staged documents.
  Reopening the current mod must not overwrite them from disk. `peek()` exposes
  live staged state; all edits use begin/commit/rollback, atomically across INIs.
- INI writes happen only at Export: write each dirty document once, create its
  timestamped `.BAK`, leave failures pending, and exclude backups from discovery.
  A confirmed mod switch or restart discards the session; there is no standalone
  Discard action. Viewer metadata and confirmed texture saves have separate writes.
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
- Texture pools and backend loading must not eagerly decode/render sources.
  Production texture rendering stays at two concurrent jobs unless controlled
  benchmarks justify changing it. Native DDS uses validated eligibility only;
  unsupported, transformed, oversized or malformed sources use PNG fallback
  with the same orientation and role-based color space.

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

- Inspector Color adjustments are per-mesh diffuse previews persisted under the
  mesh's metadata identity in `.mod_viewer.json`; preserve unrelated metadata
  and remove neutral entries. Disable editing without a diffuse texture or for
  Asset textures. Reset Color clears the preview, not a previously saved DDS.
- CPU saving and GPU preview share normalization and operation order: optional
  target-color tint first seeds the editor-sRGB color while preserving source
  intensity/shading, then hue, saturation, brightness, contrast, and RGB
  channel adjustments are applied. Brightness supports up to 400%; its Inspector
  slider uses a centered nonlinear mapping so 100% remains at the midpoint
  while the stored/backend range stays 0–4. Adjust in editor-sRGB, preserving
  alpha;
  convert at shader boundaries and do not run picker hex values through
  Three.js's implicit linear color conversion.
  Preview changes update stable material nodes without recreating textures.
- Save to Texture has its own confirmation and immediately writes one mod-owned
  BC7 UNORM/sRGB DDS, independently of INI Export. Include every changed mesh
  sharing that texture, including hidden meshes. Flush queued preview metadata
  before saving; changed targets require renewed review and pending saves block
  modal dismissal and duplicate submission.
- The backend authorizes the mod, validates canonical texture/mesh/metadata
  identities and the complete model-wide role snapshot, and derives UV coverage
  from resolved authored geometry. Never accept browser-supplied paths or UVs as
  authority. Reject Asset sources, stale identities, non-BC7 DDS, unknown target
  coverage and overlaps with different adjustments. The same physical DDS used
  in any auxiliary role is unsupported, including inactive authored variants.
- Recolor affected BC7 blocks while preserving layout, headers, decoded alpha
  and unrelated blocks. A partially covered block with one color intent pads
  that intent across valid pixels; multiple intents retain per-pixel targets.
  Propagate weighted intent through authored mips. Preserve BC7 structure and
  keep the source block if refitting worsens RGB error.
- Validate the temporary DDS layout, check source hashes around backup creation,
  create a collision-safe timestamped sibling DDS backup, then atomically replace
  the source. Abort stale-source writes. Report an actual replacement as committed
  even if later cleanup fails; never invite a second application of the color.
- After commit, clear only saved preview metadata and reload affected texture
  keys in place. A metadata-reset failure remains a committed save with a visible
  warning and recovery attempt. Async completion must check the current mod and
  target identity/state before clearing live previews; refresh failures must
  disclose that the file was saved. Recovery uses the backup, not Reset Color.

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
- WuWa reconstructs authored `normal_data` RG in TSL; B/A remain diagnostic or
  profile-specific. RabbitFX uses LightMap G for shadow; its base is shadow-only
  and Normalmap B/A response requires reliable exact body-profile evidence.
- Gate diagnostic modes by material capability; unsupported modes preserve
  normal rendering. Diagnostic-only channels must not trigger startup requests.

## Weight and secondary motion

- Normal loading only advertises usable skinning streams. First Weight access
  lazily decodes model-wide into one binary blob with per-mesh ranges; failures
  degrade the feature without failing model load.
- Weights come from authored Blend streams; IBs only preserve compact vertex
  mapping. Bone identity is normalized mod-relative Blend source plus resolved
  bone offset, with framework handling in the backend. Model-wide selections
  remain source-scoped, shared only by exact source keys, and persist in
  `.mod_viewer.json` without losing unrelated metadata or filtered selections.
- Pick-from-model discovers influences on the exact hit mesh within 2% of model
  bounding-sphere radius, distance-weighted with exact-triangle fallback. Keep
  results source-scoped; discovery never selects bones or enables physics.
- A nonempty bone selection enables model-scoped physics; empty disables it.
  Unselected influence stays at baseline, selected influence receives the bone
  transform; never renormalize selected weights or add depth-derived mobility.
- One rig per exact skinning source owns canonical centers, topology, physics
  state and bone transforms. All loaded members, including hidden meshes,
  contribute evidence; member meshes consume shared transforms with their own
  authored weights to avoid seams tearing.
- Rig influence evidence integrates linearly interpolated skin weights over
  indexed (or non-indexed consecutive) valid triangles when every loaded
  member of a source has usable positive-area geometry. Node support, moments,
  overlap, pivots and root evidence are then triangle-domain quantities; raw
  Weight-panel statistics remain vertex-weighted and are never mixed into the
  Rig graph.
- If any member of a source lacks usable surface evidence, the complete source
  falls back to vertex evidence. Aggregate graphs reject mixed evidence modes.
  Exact duplicate members are counted once for Rig evidence while their
  provenance and diagnostic-only partial overlaps remain visible in the Rig
  debug projection. Shape rebaselines invalidate and lazily rebuild surface
  evidence; visibility, material, texture and per-frame paths do not rebuild
  it.
- Infer topology only from influence overlap and weighted centers. Blend data
  supplies no names, canonical skeleton, hierarchy, bind pose or animation.
  Keep maximum-spanning relationships, weak-bridge pruning and static-boundary
  attachments conservative; never infer semantic labels such as hair or skirt.
- Cross-source Rig/Pose reconciliation is a viewer-owned model graph layered
  over the source rigs. Preserve `SourceBoneRef {sourceKey,boneId}` and the
  canonical `${sourceKey}#bone=${boneId}` key; equal numeric IDs from different
  sources never merge without geometry/topology evidence, and authored indices
  and weight buffers are never rewritten. Build model joints from strict
  mutual-best equivalences, guarded one-member-per-source clusters,
  topology-assisted propagation and ambiguity rejection. Collapse source edges
  into a model-level maximum-spanning forest, then add only conservative,
  cycle-free cross-source attachment edges between component/boundary joints.
- Cross-source reconciliation connects multiple skinning palettes for inferred
  posing. Because the model-wide inferred hierarchy may differ from each source
  palette's original weighting topology, some cross-source weighted regions can
  stretch during rotation. This remains a known Rig limitation.
- Each ModelJoint exposes a stable signature made from its sorted canonical
  source-bone keys. Runtime joint, component and root indices are ephemeral and
  must not be persisted as preset identities.
- M3 Rig pose presets use the existing per-mod `.mod_viewer.json` under
  `rig.version = 1` with an array of stable-ID records containing only a name,
  explicit root signatures and normalized non-identity local joint quaternions.
  Preset names are trimmed and bounded; IDs do not change on rename, and
  unrelated metadata is preserved on save, rename and delete. Missing or
  malformed preset metadata is a partial feature failure and must not prevent
  the model from loading.
- Preset resolution is exact by ModelJoint signature. Missing, ambiguous,
  duplicate or malformed entries are reported and skipped individually; valid
  entries still apply. Saved presets are never auto-applied after load or shape
  rebaseline, and Reset Pose returns to the default inferred roots and identity
 rotations without deleting saved presets.
- Applying a preset is one batch transaction: restore valid model-root
  overrides first, rebuild rest frames/caches once, install all valid local
  rotations, run one model deformation/bounds pass, then notify and render once.
  Pose presets and manual Rig edits remain available while Character Physics is
  active; Rename and Delete remain metadata-only operations.
- Normalize reconciliation distances by model reference radius with candidate,
  strict, propagation and attachment gates; retain candidate evidence and
  rejection reasons for diagnostics. Model joints own rest center/pivot/frame,
  source members, model parent/children and the representative member.
  `ModelSkinningRig.poseRotationByJointId` is authoritative for manual pose;
  source pose maps are derived aliases only. Reuse the forest transform builder,
  alias model transforms back to each source's authored IDs, preserve affected
  vertex caching and baseline restoration, and keep Character Physics
  source-scoped secondary rotation/velocity offsets. Physics never clears or
  overwrites the manual model pose.
- Normal Rig snapshots contain only panel and overlay data. Source membership,
  reconciliation evidence and performance diagnostics are available only from
  the explicit Rig debug projection.
- Final deformation composes manual source transforms with Physics offsets
  before one authored-baseline skinning pass. Never skin already-deformed
  geometry a second time. The final active vertex set is the union of manual
  pose vertices and Physics-selected vertices; every influence receives its
  manual transform, while selected influences additionally receive Physics,
  without renormalizing weights. Positions and normals use the same composed
  transform/rotation maps.
- Physics solver angular, translation and velocity vectors are expressed in
  the model reference frame. Composition applies an offset in that frame and
  conjugates it only by the accumulated parent Physics delta, never by a
  child's manual model rotation.
- A nonempty Weight selection continues to enable model-scoped Physics and an
  empty selection disables it. Starting, stopping or reconfiguring Physics
  preserves manual pose. Manual pose changes, presets, Reset Joint and Reset
  Pose preserve Physics rotation/velocity state, refresh pose-dependent
  equilibrium, and wake the existing simulation rather than restarting it.
  Reset Physics changes only secondary motion; Reset Pose changes only manual
  pose/root state. Saved presets serialize manual Rig state only, never
  instantaneous Physics offsets.
- The Rig picker maps source influences to model joints. The Rig panel selects
  model joints and displays topology without semantic labels; the
  explicit Rig debug projection exposes source membership; the
  combined overlay renders model joints, source edges and distinguishable
  attachment edges with O(1) Three.js objects. Reconciliation rebuilds on
  source membership/shape changes and resets pose; model structure revisions
  do not change for pose, materials, textures, visibility or model turns.
- Rig IK remains ModelJoint-native manual semantic limb posing: users persist
  only stable anchor signatures for the four supported limb roles, and manual
  anchor/end/bend overrides remain authoritative. The solver rotates only the
  mapped anchor and bend controls; end local rotation, helper local rotations,
  Physics composition, and pose preset schema remain authoritative elsewhere.
  Speculative solves use private transforms and one target update commits all
  changed local quaternions through one batch manual-pose transaction. IK never
  owns a skeleton, deformation path, IK Physics state, or persisted target.
- Automatic humanoid anatomy is a viewer-owned `HumanoidControlRig` fitted from
  the registered meshes' post-shape, pre-pose rest positions. It does not read
  ModelJoint pivots, topology, inferred forests, IK, or Physics, and it never
  writes limb mappings back through the manual resolver. Asset-fill meshes are
  excluded. It normalizes semantic up/right/forward coordinates by model
  height, keeps Foot side/height anchors, and estimates one occupancy-based
  central torso depth plane from narrow bilateral height slices rather than
  using Foot depth. Robust depth envelopes reject thick, one-sided, and
  vertically discontinuous slice outliers. Both Feet and every proportional
  control share that plane. Controls are Chest, Pelvis, Shoulder/Elbow/Hand,
  and Hip/Knee/Foot on both sides; Knee and Elbow are virtual midpoint
  controls. Diagnostics retain the detected Foot positions, per-slice
  acceptance/rejection, central-depth support/spread, and explicit fallback.
  A later phase may explicitly bind these semantic controls to authored bones.
- Joint selection is independent from pose state: Clear removes the selected
  ModelJoint through the state API, leaving
  manual pose, presets, Weight selection, Physics and overlay visibility
  unchanged. Reset Joint preserves the selected Joint; Reset Pose preserves
  it too, clears only the runtime selected-preset/apply-result state, and never
  deletes saved preset records.
- Step each source rig once at fixed 1/120 second with bounded catch-up; deform
  visible members only at selected-weight vertices and transform baseline normals
  with the same influence. Defer exact bounds and shadow-camera fitting until
  settling, retaining conservative frustum behavior. Update character shadows
  on every visible deformation frame; optimize cost without lowering frequency.
- Shape changes rebaseline positions/normals and invalidate affected rigs.
  Material, texture and visibility changes must not reload weights or redefine
  rigs. Release participants before disposing member geometry.

## Frontend ownership

- Manual textures persist as viewer metadata: undefined is automatic, null is
  none, a key is sticky. Clearing restores the immutable draw default; automatic
  highlighting follows the live resolved key.
- Environment, outlines and render modes are viewer state, never staged INI or
  material reinterpretation. Outlines use child inverted hulls sharing geometry;
  wireframe/debug suppression retains the user's preference.
- MESHES provides navigation, Inspector owns mesh/material/texture/color editing,
  and Controls owns Present/Toggle/Menu. Group Meshes/Toggles by subfolder, root
  INI stem or None for a single INI; retain source INI rows. Selection is
  event-driven. Visibility, reset, refresh and texture mutations publish shared
  state notifications so Inspector and rendering stay synchronized.
- Tabs, panel collapse and library expansion live only in localStorage and
  cannot affect mod state, geometry, materials or Export. Global panel opacity
  belongs to app config under the persistence rule above.
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
