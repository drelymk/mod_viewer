# Frontend ownership

## Panels and selection

- MESHES navigates, Inspector owns mesh/material/texture/color editing, Controls
  owns Present/Toggle/Menu. Group Meshes/Toggles by subfolder, root INI stem or None
  for a single INI; keep source INI rows.
- Selection is an event-driven set with one primary Inspector target: click
  replaces, Shift-click selects a range of expanded MESHES rows, Ctrl+Shift-click
  adds the range, Ctrl-click toggles, Ctrl-drag additively selects visible geometry
  crossing the rectangle by drawn triangles (never sampled bounds). Rig
  picking/dragging owns its gestures and blocks view selection.
- Ctrl+A selects all mesh rows (hidden/collapsed included) in MESHES, or only rows
  passing an active filter, and only visible targets in the viewport; text editing,
  dialogs and rig gestures keep priority. H hides the selection. A selected mesh's
  eye applies its new state to the whole selection; an unselected eye affects only
  its mesh.
- Visibility, reset, refresh and texture mutations publish shared notifications so
  Inspector and rendering stay synchronized. Control refreshes replace manual
  visibility only for meshes wired to changed visibility variables (derived state
  included); explicit forced refreshes may rebaseline all.
- The MESHES filter is view-only: it hides rows (ranges and drag see only displayed
  rows), never changes selection, visibility, collapse or staged state, and resets
  when a new mod or Asset replaces the panel.
- Mod INI-header Remove persists a mod-relative `removed_inis` entry in
  `.mod_viewer.json` and reloads without that INI's meshes/controls, keeping source
  files and staged documents. Exclusions apply to discovery and editor targets;
  same-basename siblings stay independent. Compressed mods cannot persist them.
- Manual textures persist as viewer metadata: undefined = automatic, null = none, a
  key is sticky. Clearing restores the immutable draw default; automatic
  highlighting follows the live resolved key.
- Automatic texture runs follow ordered authored binding identities, not first
  occurrence of a key: a repeated key starts a new run when its default/conditional
  binding differs, and in-place semantic refresh recomputes boundaries first.

## Mesh editing UI

- Loose-part separation is transient: connected islands from exact positions or a
  bounded tolerance, keeping triangle ordinals and sharing the source's
  attributes/material/state, with no authored draw or metadata identity. Merge only
  selected siblings of one source in stable order. Clearing/disposal restores the
  source range and selection; only explicit Apply enters the staged mesh-edit path.
- Inspector and context menus share edit-action availability. Preview history keeps
  bounded triangle partitions, face selections and the selection mode, shares
  authored vertex buffers and ends on apply/cancel/reload. New edit controls follow
  the `edit_mesh` gate; hiding a feature never replaces backend validation or Export.
- Component MESHES headers place Apply/Cancel Mesh Changes between Edited and
  Undo/Redo; Ctrl+Z/Y use the same history and defer to text editors, dialogs and
  rig gestures. Apply/Cancel live on component headers, inspection and context
  menus, never individual mesh inspection. During face selection the active row
  exposes Apply/Cancel Selection via the shared handlers. Inspector edit actions are
  header icons before the visibility eye.
- Isolation is viewer visibility, independent of `edit_mesh`: it filters effective
  visibility through the selection, keeping manual/control choices; exit, reset and
  topology replacement clear it. Isolate Selected stays in the shared context menu,
  also while isolated.
- Face rectangles project each drawn triangle once and select through (no
  per-triangle raycast); click picking selects nearest or through all faces, keeping
  IB ordinals across nonzero ranges. During Separate by Selection, Ctrl+A selects
  every face of the active mesh/part via the shared history path. Face modes
  (Set/Extend/Subtract/Difference/Intersect, default Set) are icon buttons right of
  the left-panel tabs, shown only in Separate by Selection; Ctrl-click keeps toggle.

## UV inspection

- Read-only and independent of `edit_mesh`: reads live geometry/draw range and part
  ordinals only when expanded. Keep one current mesh wireframe and resolve
  highlights from selected face IDs; texture changes never rebuild it. The section
  follows Color; viewport expansion reparents the same canvases, keeps aspect and
  side panels and follows selection. Its X button or a second zoom click closes it;
  it does not intercept Escape. Model teardown releases it.
- Reuse the current diffuse publication and DDS preview across rebuilds. UV previews
  show RGB independently of alpha; menu previews keep transparency; both policies
  stay distinct in the cache. Size previews for the panel and upgrade only when more
  pixels are needed (up to 2048 longest edge in viewport mode), rendering from the
  ready model texture and keeping the larger result; never reload a ready DDS.

## Viewer state and layout

- Environment, outlines and render modes are viewer state, never staged INI or
  material reinterpretation. Preset floors composite in the background node (not
  geometry); cached IBL captures the floorless sky; ambient/hemisphere tints share
  the backdrop palette. Procedural floors only modulate that palette around its mean
  and fade detail by pixel footprint.
- Floor, grid and shadow receiver share one ground frame locked from the visible
  rest-pose base (never padded animation bounds) at fit/load, re-anchored only by
  quarter turns or Reset View. Selecting a game-rig clip seats its height once on
  the Avatar ground (y = 0 through the `mesh-space` root); stopping restores the
  rest base. Shadow frustums grow in model-scaled steps and snap to texels.
- Outlines are child inverted hulls sharing geometry; wireframe/debug suppression
  keeps the user's preference. Normal and selection hulls need slope-aware raster
  depth bias scaled to display pixel density.
- Tabs, panel collapse and library expansion live only in localStorage and never
  affect mod state, geometry, materials or Export.
- Runtime UI text goes through the locale catalog with placeholder-compatible
  translations and English fallback. Language is app config, never mod state; never
  localize resource, mesh, section or metadata identities.
- Reset/Turn/Tilt stay in the viewport toolbar. Apply auto-upright, game/base facing
  and manual rotation in that order, late-adopted meshes included. Reset keeps the
  base transform; removed meshes leave the baseline. Direct Assets use the backend's
  game metadata for orientation without transforming stored geometry.
- Docks float over the viewport; framing and projection use the whole canvas, and
  dock changes never shift, re-centre or refit the view.
