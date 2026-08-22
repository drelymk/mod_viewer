# 3DMigoto Mod Viewer - Project Context

This is a pywebview desktop viewer for 3DMigoto character mods. It reads INI
files, buffers and textures from a mod folder, builds meshes, renders them with
Three.js/WebGPU, previews menu state, and stages INI edits until Export.
Supported families include ZZZ/ZZMI, Genshin/GIMI, WuWa/WWMI and HSR/SRMI.

Keep this file focused on durable invariants and the failure each one prevents.
Do not add implementation inventories, historical explanations, test output,
machine-specific paths or facts that are obvious from the source.

## Working rules

- Never put credentials, tokens, private URLs, usernames, hostnames, absolute
  local paths or environment dumps here.
- Read-only analysis may use the lossy parser. Any user-visible edit must use
  the lossless document and shared edit session described below.
- Preserve the existing trust boundaries and execution-order semantics even if
  a simpler implementation appears equivalent.

## Repository map and data flow

- `src/core/` contains GUI-free parsing, analysis, geometry, texture and
  editing logic. `src/app/` owns the pywebview bridge, sessions and payload
  orchestration. `src/web/` contains the localhost-served frontend.
- `ini_parser` is the read/analysis path. `IniDocument` and `edit_session` are
  the authoritative staged write path. Never write through `ini_parser`.
- `app.api` loads the active INIs through `mod_loader.load_mod()`. The loader
  parses each INI independently, consumes staged text when present, and returns
  named payload fields for meshes, textures, texture pools, controls, state,
  geometry, metadata and health.
- `app.mod_folders` owns the optional versioned app config, including the Mod
  Folder registry and global panel opacity. A missing config uses defaults;
  valid writes use an atomic replace, while malformed or unsupported config
  must remain untouched. Untouched panel opacity is omitted from the file;
  once changed, its explicit value remains persisted even when it equals the
  default.
- Geometry is published through one shared localhost blob. The normal load path
  must not base64 round-trip geometry or rediscover semantic stages. Direct
  low-level fixtures may retain their older representation, but reserved
  builder keys must not cross the public application payload boundary.
- One active INI produces one canonical analysis shared by its controls, draw
  groups, shapes and resources. Sibling INIs must not share resource discovery
  state.
- Diagnostics are read-only, lazy and cached by the edit-session revision.
  Authoritative commits invalidate the cache, and returned reports must be
  detached from mutable internal state. A diagnostics failure must not turn a
  loadable mod into a load failure.

## Lossless editing and staging

- Real INIs may contain CRLF, LF, mixed terminators, UTF-8 BOMs and no final
  newline. Every line owns its terminator and untouched round trips must be
  byte exact.
- Malformed or ambiguous `if`/`elif`/`else` nesting, headers, conditions or
  branch content is reported by `structure_errors()` and is not rewritten by
  guessing. `section()` and similar single-section accessors intentionally use
  the first duplicate; callers that need all duplicates must iterate the full
  document.
- Reload, diagnostics, Toggle CRUD, Record and INI editing all read the same
  staged `IniDocument`s. Reopening the current mod must not reread disk over
  staged state. A confirmed mod switch or restart discards the session.
- All edits use `begin`/`commit`/`rollback`. `peek()` exposes the live staged
  document so a subsequent edit sees earlier unexported changes.
- Nothing reaches disk before Export. Export writes each dirty INI once,
  creates one timestamped `.BAK` backup per file, and leaves failed documents
  pending. Backup names must not be discovered as loadable INIs.
- Namespaced globals are cross-INI and read-only. Add/edit/delete operations
  target only plain variables declared in the edited INI.
- Existing wired Toggle sections are shown normally. A newly added unwired
  section is shown only while it is tracked as pending in the current session;
  old unwired utility keys stay hidden. Export must also reject a still-unwired
  newly added section.
- `[Key...]` is the identity of a toggle, not an individual variable. A key
  may cycle several variables in lockstep, so the UI and editor resolve a
  position from the complete variable tuple. Duplicate tuples retain their
  last-position tie break.
- `[KeyModViewerPresent]` is one mod-wide, logically aligned cycle. Its edits
  fan out atomically across participating INIs through the same edit session;
  misaligned or incomplete state is visible but not applied.
- Record rewrites only a provably safe single-variable if/elif shape. It
  regenerates a whole chain, refuses ambiguous or partially observed cases,
  reparses independently, and rolls back when verification fails.

## Parsing and mod state

- Variable names are case-insensitive. `canonical_var_names()` is the source
  of truth for spelling and must feed toggle extraction, defaults, menu state
  and DNF normalization. Partial canonicalization can make an untracked gate
  fail open and incorrectly show every mesh.
- `SrcLine` retains source INI, line number and section metadata; use
  `line_source()` instead of losing provenance. Every draw has a `sources`
  list because merged payload entries can represent several authored lines or
  files, and edits must fan out to every source.
- Draw labels are unique payload identities; display names are UI text. Share
  the seen-label map across all active INIs so same-named components cannot
  overwrite one another.
- Recognize both `elif` and `else if`. Draw DNF preserves contradictions and
  treats an empty DNF as true. State-rule DNF may remove contradictory groups;
  never apply that simplification to draw visibility.
- Clickable menus are recognized from structural nested branches that assign
  variables back to themselves. Track outer guards, and do not infer a menu
  from a branch that merely resembles one. Increment/modulo cycles expose the
  full `0..N-1` range.
- Diffuse, NormalMap, LightMap and MaterialMap bindings are execution-order
  state. Each draw uses the most recent applicable assignment; conditional
  alternatives and no-map fallbacks remain per draw. Do not collapse them to
  one component-level texture.
- Discover INIs with the existing bounded rules: a direct root INI establishes
  the mod, nested INIs are added only within the configured depth/count bounds,
  and unrelated library/category folders must not be merged. Resource paths
  stay relative to the INI that declares them; published/editor identities are
  relative to the selected mod root.

## Geometry and texture invariants

- Geometry detection is conservative and format-specific. Do not infer a game
  or material meaning from a filename alone. Unknown, malformed or too-small
  buffers must be rejected or dropped safely rather than read beyond bounds.
- An `ib` section without `drawindexed` may produce one synthetic whole-buffer
  draw. A `handling=skip` section without an explicit draw produces nothing.
  Real draw rows retain authored `count,start,base`; synthetic rows are the
  only ones allowed a generated display marker.
- Shape sliders affect geometry only when a complete recognized buffer layout
  is present. Slider-looking variables without valid geometry targets remain
  controls, and an override for one position buffer must not inherit another
  buffer's shape target.
- Resource filenames are mod-relative paths, never basenames. Rendered
  texture identity is `role::mod-relative-path`; the roles are `diffuse`,
  `normal_map`, `normal_data`, `light_map` and `material_map`. Diffuse is sRGB;
  auxiliary roles are non-color data. A legacy path-only key is normalized
  using the caller's known role, never guessed in the browser.
- `core.resource_paths.safe_resource_path()` is the single sandbox for
  mod-authored geometry, manual texture selection and diagnostics. Reject
  absolute/drive paths and excessive parent traversal. The server's separate
  static-web-root join protects a different boundary and must not be merged
  with this rule.
- `core.textures` owns role-aware keys, PNG fallback, transforms, caching and
  texture profiling. `core.mesh_builder` owns geometry/payload assembly.
  Keep texture processing independent from game/material interpretation.
- Load texture sources lazily. Publishing or hydrating a texture pool must not
  decode/render every source, and backend model loading must not eagerly render
  textures. Production texture rendering is bounded at two concurrent jobs
  unless controlled benchmark evidence justifies a change.
- Native DDS delivery is allowed only for the existing validated eligibility
  path. Unsupported, transformed, oversized or malformed sources use PNG
  fallback. Compressed DDS and fallback PNG must share the same orientation;
  color-space rules remain role-based.

## Game and material interpretation

- Game, runtime, texture API and material kind are separate concepts. Resolve a
  material profile per mesh from structural evidence and `(game, texture_api,
  material_kind)`, falling back to the validated base profile. Weak component
  names and texture filenames must never activate specialized semantics.
- Profile metadata is deduplicated in the payload and referenced by immutable
  `material_profile_id`. A profile ID collision with different metadata is a
  programming error.
- Packed maps retain their authored RGBA data and stable role binding. They are
  not treated as generic Three.js AO/roughness/metalness maps without evidence.
  Binding changes update stable texture and enabled nodes with valid
  placeholders; they must not rebuild materials or patch generated shader
  source.
- Genshin uses the validated LightMap R response and G toon-shadow input;
  LightMap B gates the toon-specular area and A classifies authored regions.
  ZZZ uses LightMap G for conservative metalness and MaterialMap B for
  specular response; MaterialMap R remains material-ID data and MaterialMap G
  must remain packed because its meaning varies by character.
- WuWa `normal_data` preserves the authored normal source and reconstructs RG
  in TSL; B/A are diagnostic or profile-specific data, not stock PBR maps.
  RabbitFX LightMap G is the validated shadow-mask input. The base RabbitFX
  profile is shadow-only; the body specialization requires reliable exact body
  evidence, and only that profile gives Normalmap B/A response semantics.
- Unsupported diagnostic modes are capability-gated per material and leave
  normal rendering unchanged. A diagnostic-only packed channel must not create
  a new startup texture request.

## Frontend, hosting and security

- The frontend uses the vendored Three.js `0.185.0` WebGPURenderer and TSL
  Node Materials. WebGPU is required; verify the initialized backend and never
  silently fall back to WebGL2. The default material is DoubleSide, roughness
  1 and smooth shading. Viewer-only render modes, including glossy materials,
  must not alter INI state.
- The UI is served from an ephemeral `127.0.0.1` origin. Do not use
  `NavigateToString`, runtime CDNs or third-party scripts in the privileged
  page. Geometry uses the one-shot localhost blob transport.
- Bridge filesystem operations accept exact native-picker paths or canonical
  descendants of persisted Mod Folder roots. Browser-invented paths must never
  reach loaders, metadata or export. Descendants promoted to exact session
  authorization remain usable after their registry root is edited or removed.
  Adding a root or changing an edit path still requires a native picker result;
  keep the native window at the private `_window` bridge attribute.
- Mod Folder browsing is read-only filesystem navigation: list only immediate
  directory children of a registered root, sort deterministically, and skip
  symlink escapes. It must not load mods, discover INIs, validate mod shape or
  expose Edit/Delete actions for non-root children.
- Keep the existing separation between native file-picker authorization,
  `safe_resource_path()` for mod resources and the server's static-root join;
  do not duplicate or weaken any of the three boundaries.
- The Toggle cycle control uses assignable `.onclick`, not an added listener,
  so Record can capture, replace and restore exactly one handler.
- Mesh and Toggle panels group by source: subfolder, root INI stem or `None`
  for a single INI. Mesh source rows must retain their originating INI.
- Manual mesh texture selection is viewer-only state in `.mod_viewer.json`.
  `undefined` means automatic, `null` means none, and a texture key means a
  sticky selection. Clearing returns to the immutable draw default; automatic
  highlighting follows the live resolved key.
- Environment presets, outlines and toolbar render modes are viewer-owned.
  They must not reinterpret INI materials or become part of staged edits.
  Outlines remain child inverted-hull passes sharing base geometry, and
  wireframe/debug suppression must not change the user's outline preference.
- Inspector/Controls tab choice, panel collapse state and Mod Library expansion
  are presentation preferences stored in browser localStorage only. They are
  not mod state, are not loaded from or written to INI/session edits, and must
  never affect geometry, materials or export. Global panel opacity is the one
  app-config preference; it is omitted until changed and then retained as an
  explicit value.
- MESHES is the navigation surface: component and mesh selection should not
  duplicate editing controls there. Inspector owns material kind, texture pool
  management, per-mesh texture overrides and draw details; Controls owns
  Present, Toggle and Menu state. Keep selection changes event-driven so the
  Inspector follows the selected component or mesh without taking ownership of
  mesh creation or texture state.
- Authoritative mesh visibility, reset, refresh and texture-override mutations
  must publish one shared frontend state notification so Inspector values cannot
  drift from the rendered model or MESHES state.
- Viewport camera actions (Reset, Turn and Tilt) belong in the compact viewport
  toolbar with the render and navigation tools. Do not restore a separate
  Orientation panel when changing camera behavior.

## Feature flags and test strategy

- `features.ini` is build-time input only. `build.py` bakes its booleans into a
  temporary generated module and removes that module afterward. Source
  checkouts show all features; frozen builds may hide Export or Toggle CRUD in
  the UI, but backend authorization remains authoritative. Missing or malformed
  values default to enabled.
- Tests are pytest-discovered from `src/tests` and run from the repository
  root. Install `requirements-dev.txt`, then use `pytest`; use
  `pytest --collect-only -q` to inspect the current suite rather than relying
  on a hard-coded count.
- The reduced suite is intentionally focused on representative boundaries and
  regressions. Keep future coverage behavior-based and do not add a test when a
  simpler existing case already proves the same contract.
- Frontend tests use Playwright against the real local server and vendored
  Three.js assets with an installed Edge browser. They may skip when Edge or
  the assets are unavailable. Mock only `window.pywebview.api` for UI-state
  tests; do not import `app.api` into a bare browser fixture because it pulls in
  GUI webview state.
- Real-mod corpus checks, if added again, must be explicitly opt-in through
  `MOD_VIEWER_TEST_CORPUS`; the default suite must remain self-contained.
  Add new regression coverage to the nearest existing test module and avoid
  restoring tests that only repeat a simpler case.
- Use `tools/benchmark_texture_pipeline.py` when changing texture concurrency,
  lazy loading or transport. Keep deterministic formatting and mechanical
  checks in CI; code review should focus on behavior and these invariants.

## Known intentional limitations

- Menu-guard parsing does not fully model every `&&` form; unsupported cases
  weaken a guard rather than hide geometry.
- There is no standalone Discard button; switching mods with confirmation or
  restarting drops staged edits.
- Record mode refuses ambiguous real-world gating instead of guessing.
- Complex or unsupported mod layouts may fail to load; conservative omission is
  preferable to silently displaying incorrect geometry or material semantics.
