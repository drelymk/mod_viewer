# 3DMigoto Mod Viewer — Project Context

pywebview + Three.js desktop viewer for ZZMI/ZZZ, WWMI/WuWa and GIMI/Genshin
3DMigoto mods. It reads flat mod folders, resolves INIs/buffers/textures, renders
meshes, previews menu state and stages toggle edits without running the game.

> Keep this file to rules a future change must preserve. Record the invariant
> and the failure it prevents; omit implementation inventories, verification
> narratives, decision history and facts already obvious from code or git.

## Context hygiene

- Never record secrets or machine-specific data here: credentials, tokens, API
  keys, private URLs, usernames, hostnames, absolute local paths, environment
  dumps, attachment paths or workspace paths. Use generic placeholders instead;
  every entry must remain repository-portable and safe to publish.

## Commands

    pip install -r requirements.txt
    python src/viewer_app.py

Development tests use pytest for discovery; install the test-only dependency
with `pip install -r requirements-dev.txt`, then run from the repository root:


Each test file remains directly runnable through a small dynamic compatibility
runner, but new `test_*` functions are discovered automatically. The corpus
checks in `test_document_vs_parser`, `test_record_editor` and related modules
may scan external mod libraries and can be skipped or fail independently of the
current change.

Build with `python src/build.py`; useful flags are `--onedir`, `--console` and
`--rebuild-bootloader`. Builds require `src/features.ini`, vendor Three.js for
offline use and target 64-bit Windows with Evergreen WebView2. One-file
PyInstaller binaries resemble droppers because they unpack and execute from
temp; `--noupx`, version metadata and preferably `--onedir` reduce false
positives. `--rebuild-bootloader` needs MinGW-w64 and must verify that bootloader
hashes changed—installing PyInstaller from sdist alone does not rebuild it.

## Architecture boundaries

- `src/core/` is GUI-free domain logic; `src/app/` bridges it to pywebview;
  `src/web/` is the localhost-served UI. Core cross-imports are relative, while
  app/tests import `core` absolutely.
- `ini_parser` is the lossy READ path. `ini_document` is the lossless WRITE
  path. Never write through the reader.
- `edit_session` owns the authoritative loaded `IniDocument`s. Reload, health,
  toggle CRUD, Record and text editing all use this shared in-memory state.
- `mod_loader.load_mod()` parses active flat INIs independently and returns the
  structured application payload: `meshes`, `textures`, `controls`, `state`,
  `geometry`, `metadata` and `health`. `overrides` previews staged text and
  `pending_new_sections` keeps new unwired toggles reachable. The low-level
  mesh builder may retain its direct-fixture representation, but reserved
  keys must not cross the backend/frontend boundary.
- One active INI produces one `IniAnalysis`; controls, state, shapes and draw
  groups consume its shared canonical-variable/resource results. Geometry
  construction returns a named `MeshBuildResult` and appends binary fields
  directly to the shared blob, so the normal load path must not base64 round
  trip or rediscover semantic stages.
- Full diagnostics are lazy and cached against the edit-session revision.
  Any authoritative document commit invalidates that cache; a cached report
  is detached before crossing the API boundary.
- `find_inis` recurses only when a direct root INI has geometry, preventing a
  library/category folder from combining unrelated mods. Always include every
  active direct INI; add nested INIs through two directory levels up to ten
  total files unless the direct set already exceeds that limit. Resource filenames remain
  relative to the INI that declares them, while payload/editor identities are
  paths relative to the selected root so duplicate basenames stay distinct.

## Parsing and state invariants

- 3DMigoto variables are case-insensitive. `canonical_var_names(sections)` is
  the spelling source of truth (global declaration wins, otherwise first use)
  and must feed `extract_toggle_keys`, `extract_variable_defaults`,
  `extract_menu_toggles` and `normalize_dnf`. Partial canonicalization is worse
  than none because untracked DNF clauses fail open and make meshes always
  visible.
- `parse_sections` returns `SrcLine`, a `str` subclass carrying
  `ini_path/line_no/section`; use `line_source()`. Every draw has a `sources`
  LIST because payload dedup can merge several lines/files. Authoring changes
  must fan out to every source.
- Draw groups have a unique `label` and clean `display_name`. Payload keys use
  `label`; UI names use `component`/`display_name`. Share the `seen` label map
  across all INIs in a mod or same-named components overwrite each other.
- Toggle identity is the `[Key...]` SECTION, not one variable. A key can cycle
  several variables in lockstep. The UI cycle position must be resolved from
  the complete variable tuple, not `indexOf()` on a lead variable whose values
  may repeat (`1,0,0`); retain the last position to break duplicate-tuple ties.
- Recognize both `elif` and `else if`. Simplification removes redundant elif
  exclusions, but draw DNF deliberately preserves contradictions: an empty
  draw DNF means TRUE, so deleting an impossible group would make it visible.
- Clickable menu chains are recognized structurally: nested
  `if $slot == N`/`elif` branches that assign variables back to themselves.
  Track outer guards as a stack. A simple `else` is the exact negation of its
  one preceding branch; after multiple elifs its guard is unknown. Check the
  `< N`/else cycle idiom before the trailing `> N` reset idiom. Also support
  `$v = ($v + 1) % N`, which exposes `0..N-1`.
- A diffuse is execution-order state, not one texture per component. Each draw
  uses the most recent assignment before it. Reset `_cur_diffuse_variants` when
  a new assignment is not a same-branch continuation; keep ordered independent
  assignments so the last matching write wins. `texture_default_file` belongs
  to each draw and `texture_variants` only to conditional alternatives.
- Authored `NormalMap`, `LightMap` and `MaterialMap` assignments follow the
  same per-draw execution-order model, including conditional variants and a
  no-map fallback for a conditional-only assignment. They are INI-driven only;
  manual texture pools remain diffuse-only. Two-channel normal maps have Z
  reconstructed during encoding and Three.js flips their DirectX Y axis.
  Packed LightMaps are used only as scalar AO (never RGB light, which causes a
  red cast). MaterialMaps remain loaded and toggle-aware but are not guessed
  into incompatible standard PBR channels without known shader semantics.
- A section with an `ib` but no `drawindexed` uses a synthetic whole-buffer
  draw. It inherits `diffuse_variants_at_end`; do not discard a section’s final
  diffuse. A real draw before the first diffuse legitimately remains untextured.
- `diffuse_pool_files` contains every distinct referenced diffuse in first-seen
  order and feeds manual texture choices. Texture-only condition variables count
  as gating/wired variables even when they never hide a draw.
- WWMI menus often derive draw flags in `[Present]`. `ini_state` accepts only
  safe literal assignments and the frontend replays them in source order from
  all declared defaults, including hidden internal variables. State-rule DNF
  drops contradictory groups (unlike draw DNF), and every state-rule assignment
  target must be included in `gating_var_names`; otherwise gates such as
  `qipao0..6` disappear while the simulator updates unused values.
- Menu increment/modulo (`$v=$v+1; $v=$v%N`) exposes `0..N-1`, not `[0,1]`.

## Lossless documents and editing

- Real corpus files include CRLF, LF, mixed terminators, UTF-8 BOMs and missing
  final newlines. Every `IniDocument` line owns its terminator and the document
  preserves BOM/final-newline traits; untouched round trips must be byte exact.
- Malformed if/endif nesting is common and tolerated by 3DMigoto. Report it via
  `structure_errors()` and refuse ambiguous rewrites rather than rejecting mod
  loading. Point unclosed-if errors at the unmatched opening line. Duplicate
  `else`, elif-after-else, malformed conditions, trailing branch content,
  unbalanced parentheses and malformed headers also make rewriting unsafe.
  Unknown commands/sections must not be guessed invalid.
- `section(name)`/`find_cycle_section` intentionally resolve the first duplicate
  section name; callers needing all duplicates must use `sections`.
- Nothing reaches disk until Export. CRUD, Record and INI Apply mutate the
  session document only. Reopening the same mod does not reread external disk
  changes; discard occurs on confirmed mod switch or restart.
- `begin/commit/rollback` make every edit atomic. `peek()` must return the live
  staged document so an immediate Record sees a preceding unexported edit.
- Export writes each dirty document once, creates one
  `mod.ini_YYYY-mm-DD HH-MM-SS.BAK` suffix backup per INI and is best effort;
  failed documents remain pending. The suffix must not match the `.ini` loader.
- The INI viewer opens one file directly or a picker for many; diagnostic rows
  open the same editor at their one-based source line. Apply remains memory-only.
- INI syntax highlighting treats single and double quotes as row-local. An
  unmatched quote may color the rest of its own row, but must never carry string
  state into later rows; 3DMigoto values use quotes as ordinary characters.
- Namespaced globals (`$\Mod\Master\swapvar`) are cross-INI and read-only.
  Add/edit/delete target only plain variables declared in the edited INI.
- Wired toggle sections always show. An unwired section shows only when tracked
  in `pending_new_sections` as newly added this session, so Record is reachable;
  all old unwired utility keys remain hidden. Backend Export—not only UI state—
  refuses while a tracked new section is unwired.
- `[KeyModViewerPresent]` is one logical, mod-wide cycle shown only in the
  PRESENT panel, never duplicated under KEY TOGGLE. Add asks only for its shared
  key/back binding, then atomically creates the reserved section in every INI
  that owns an ordinary key or clickable-menu toggle. Each generated section
  captures only key, clickable-menu and recognized shape-slider variables
  controlled by its own INI and copies that INI's first existing key condition.
  All section/value edits remain staged through
  `edit_session` until Export and fan out atomically across the participating
  INIs. The logical cycle holds 1..10 aligned positions; deleting one shifts
  sparse custom-name metadata. Duplicate complete cross-INI value tuples require
  explicit confirmation. Implicit names are `Present N`; only customized names
  are stored sparsely in `.mod_viewer.json`. Loading a mod applies its first
  present to viewer state; an authoring refresh may restore the position that
  was just created, edited or selected for deletion. The panel-header action is
  Add only while no PRESENT exists, then becomes the section Delete action; the
  item itself keeps only its binding Edit action. A legacy partial PRESENT uses
  that header action to complete missing eligible INIs, repeating their current
  snapshot to match the existing logical position count. Misaligned position
  counts are a visible non-interactive error state; never cycle or apply them.
- `add_toggle` gates hotkeys behind on-screen detection: reuse
  `$object_detected`, then `$active`, otherwise add `global $active = 0`,
  self-resetting `[Present] post $active = 0`, `$active = 1` in the first two
  TextureOverrides and `condition = $active == 1`. This plumbing is idempotent;
  `active`/`object_detected` cannot be the new toggle’s own variable.

## Record mode

- Record rewrites only a safe pattern: one target variable, no `else`, no mixed
  conditions, no nested if inside a branch, drawindexed-only branch content and
  complete observations for every position. Refuse every other line with a
  reason; never guess.
- Regenerate a safe if/elif chain as one whole splice. Editing branches
  independently is wrong because earlier elif siblings can claim new values.
- A bare draw with no ancestor referencing the variable may receive a private
  wrapper. Refuse a line gated only at an outer level. `_refs()` treats `else`
  as referencing a variable used by earlier siblings, preventing an unreachable
  private wrapper inside that else.
- `verify_recording` reparses independently and requires every rewritten draw
  to match its recorded positions; mismatch rolls back. Identify draws by
  `(section,count,start,base)`, not shifted line numbers. Verify only chains or
  wrappers with no untouched outer ancestor. Remove backend-only `verify`
  details before returning through the JS bridge.
- Frontend Record gets its position count from `get_record_positions`, not the
  Toggle panel; namespaced variables can enlarge the read-only panel cycle but
  cannot match the writer’s plain-variable regex.

## Geometry and textures

- Supported families: ZZMI hash sections (stride-40 position, stride-24 UV,
  f16 UV offset 4), WWMI shared resources (vb0 position, vb2 preferred UV and
  sparse shape keys), GIMI Position/Texcoord/Blend (f32 UV offset 4), and
  RabbitFX diffuse references.
- Shape sliders are conservative and layout-specific:
  - Simple full buffers use `base + (target-base)*value`.
  - Repeated ZZMI shader blocks may bind `x88=$var`, `cs-t50=base`,
    `cs-t51=target` several times with arbitrary suffixes such as `.1/.2`.
    Recognize only 2+ complete blocks sharing one file-backed base; apply each
    target additively and attach it to every matching position-buffer group.
  - WWMI sparse data uses a 256-entry cumulative offset buffer, parallel uint
    vertex IDs and six float16 values per record (position then normal).
    `buffer_id = shape_id + shape_id//127`; add the declared per-batch vertex
    offset before indexing. Sum repeated vertex-ID deltas like `InterlockedAdd`.
  - Five-buffer ZZMI shapes have base, low/high target A, low/high target B and
    two scalars. At `0.5` they are neutral. Preserve endpoint extrapolation:
    low factor `2-4v`, high factor `4v-2`, then average independently shaped
    positions. The high branch moves base toward bigger; zero is a real pose.
  Slider-looking animation variables without geometry targets remain controls.
  A mid-section position-buffer override never inherits another buffer’s target.
- UV auto-detection is fragile. Sample evenly across the full buffer; reject a
  constant U or V axis; tolerate a small fraction of outliers; rank cleanliness
  before spread; and return only formats/offsets that fit the stride. Corpus
  changes require a full buffer sweep, not a spot check.
- Texture source paths are mod-relative, never basenames, because variant
  folders commonly repeat filenames.
- Rendered texture identity is `role::mod-relative-path`, because one source
  file may be used as diffuse, normal, light or material data and each role
  has a different encoding/color-space contract. Diffuse maps are sRGB;
  auxiliary maps are non-color data. Legacy path-only viewer metadata must be
  normalized when loaded.
- `safe_resource_path`/`_safe_join` is the one sandbox implementation for both
  geometry and health. Reject absolute/drive paths, allow relative parent climbs
  only up to live `_MAX_ESCAPE_DEPTH`, and do not duplicate these rules.
- `read_texcoords` bounds with `struct.calcsize(uv_fmt)`; f32 pairs are 8 bytes.
  Convert RGBA to RGB before LANCZOS thumbnails or transparent textures turn
  black. Prefer vb2 over vb1 for WWMI UV except stride-32 ZZMI blend buffers.
- `ResourceShapeKeyedPosition` without a file falls back to an R32G32B32_FLOAT
  resource. Runtime writable positions may resolve through explicit resource
  copy edges, the established `.B` rest-pose convention, or a complete LL
  skeleton compute pattern whose file-backed `cs-t1` position and stride-32
  `cs-t2` blend write `cs-u0`; `vb0 = ref` must bind the resource after `ref`,
  not the literal word `ref`.
- Auxiliary maps may be authored through namespaced `Resource\\...\\NormalMap`,
  `LightMap` and `MaterialMap` assignments or direct `ps-tN` assignments whose
  resource name identifies the map role.
- Load resources per INI to prevent sibling-mod contamination. Component names
  strip only literal `TextureOverride`, never a common prefix.
- Draw rows display the authored `count,start,base`; only synthetic whole-buffer
  draws use `#N`. `handling=skip` sections with no explicit draw draw nothing;
  ordinary `ib`-only sections may use the synthetic draw.
- `read_indices` clips to actual buffer length. Too-small buffers drop a draw
  rather than reading beyond bounds. R16 index resources remain 16-bit.
- Grid scale uses the median bounding-box dimension; camera near/far planes
  scale from model bounds. Camera framing targets the
  unobstructed viewport: keep orbit target at the true model center and shift
  projection with `setViewOffset`; back up when horizontal space is limiting.
  Materials use DoubleSide, roughness 1 and flat shading by default.

## Frontend, hosting and security

- INI Diagnostics is read-only and survives geometry failure. Analyze staged
  text when present; attach `health` even to error payloads. A health failure
  never makes a loadable mod fail. Resource/file analysis is conservative,
  transitive, case-insensitive and per INI; comments/self-references do not mark
  use, namespaced resources may be framework-provided, and disabled/viewer-only
  files are classified separately. It never deletes or rewrites. Viewer texture
  metadata changes invalidate the cached report and the open frontend report.
- Recognized menus use authored item images when at least 60% and at least two
  entries have images; otherwise use text controls. Images are clickable cycle
  buttons and sliders keep native range inputs. `#menu-list.image-layout` must
  retain an explicit `.image-layout.collapsed {display:none}` override.
- The UI is served from `http://127.0.0.1:<ephemeral>`; never use
  `NavigateToString` or a runtime CDN. String pages have opaque origins and size
  limits, while third-party scripts would inherit the privileged bridge.
- Geometry crosses the bridge as one packed localhost blob. API payload entries
  contain offsets into a shared ArrayBuffer; the random URL is consumed on first
  GET and new publication expires older blobs. Keep legacy base64 decoding for
  direct fixtures/core tests.
- Every bridge filesystem operation is restricted to normalized folders
  returned by the native picker. Browser-invented paths must not reach loaders,
  metadata or export. Keep the native window in underscore-private
  `ModViewerAPI._window`; a public self-referential window breaks pywebview
  reflection.
- WebView2 detection reads the WOW6432Node registry view and treats
  `pv="0.0.0.0"` as absent.
- Toggle cycle buttons use assignable `.onclick`, not `addEventListener`, so
  Record can capture, replace and restore exactly one handler.
- Record UI pre-populates snapshots for every position. Mesh `sources` map
  checkboxes back to lines and must be filtered to `src.ini === info.ini`.
  `exitRecordingUI()` calls the item’s `describe()` closure to restore its label.
- Mesh and Toggle panels share `source` grouping: subfolder name, root INI stem,
  or `None` for one INI. Stamp it on groups and payload entries.
- `#sidebar` and `#tool-panel` share the flex `#left-dock`; do not restore fixed
  pixel offsets. Tool buttons remain available before load.
- The trackball button controls custom `#view-gizmo`. Take pointer capture only
  after drag threshold so click-to-snap works; reorder SVG axes only when depth
  order changes or pressed nodes lose clicks.
- Menu is read-only and never filters ungated items; it is the mod’s own control
  inventory.
- Environment presets are viewer-only presentation state. They use procedural
  backgrounds and viewer-owned lighting only: no external/background assets,
  mod metadata, INI state or material reinterpretation. Default restores the
  original scene and light state exactly. The environment accent follows the
  current model target, while the movable key light and its depth-tested marker
  remain independent user controls.
- Manual mesh texture selection is viewer-only `.mod_viewer.json` state, never
  staged into INIs. `manualTexOverride` is `undefined` (automatic), `null`
  (none), or a texture key. Clearing restores immutable `defaultTexKey`, and
  automatic highlighting uses live `resolvedTexKey`, not load-time default.
- A component’s popup and every child list share the same mutable
  `texture_options` array (normally attached for pools of 2+). When absent,
  create one fresh component-local empty array. Always render the component
  texture button, even with zero textures; its active state follows whether any
  component mesh resolves a real `material.map`. Child lists are rerenderable
  closures invoked after pool changes and reread `manualTexOverride`; one-time
  DOM snapshots become stale. Automatic texture-run propagation starts from
  each boundary’s live `resolvedTexKey`.

## Feature flags and frontend verification

- Build chain: build-time-only, unshipped `features.ini` → temporary
  `_baked_features.py` →
  `app/features.py` → server-rendered body class → CSS. Bake booleans before
  PyInstaller analysis via a literal import and delete the generated module in
  `finally`. Missing/malformed values default True.
- Source checkouts always return all-True flags. Flags affect frozen builds only.
  Server-side body classes prevent UI flash; CSS hides Export or toggle CRUD,
  while backend guards remain authoritative. `Modify_Toggle` hides Add and the
  Edit/Delete/Record wrapper, not the cycle preview.
- There is no Node test stack. For frontend behavior use a disposable Playwright
  venv with installed Edge (`channel="msedge"`) and the real local server. Mock
  only `window.pywebview.api` for UI state tests, or expose real pure-Python
  loader/session functions for end-to-end tests. Do not import `app.api` into a
  bare fixture because it pulls in GUI webview; use a small compatible FakeAPI.
- Real geometry fixtures need sufficiently large zero-filled buffers; index
  clipping otherwise removes draws. A browser-chrome favicon 404 is expected.
  Delete scratch venv/scripts after use.

## Accepted gaps

- `_GUARD_RE` does not model `&&` menu guards; rare cases only weaken a guard,
  never hide geometry.
- There is no Discard button; mod-switch confirmation or restart drops staging.
- Responsive/generated image grids may intentionally reuse one icon across many
  slots; preserve those mappings.
- Record mode intentionally refuses ambiguous real-world gating; edit such INIs
  by hand.
