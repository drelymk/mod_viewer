# Mod animation reconstruction

Covers INI-driven animation in mods: baked clocks, GIMI compute animation and
WWMI sparse shape animation. Game-clip playback lives in
[animation-playback.md](animation-playback.md).

- These are conservative reconstructions, not a general INI or shader
  interpreter. Emit typed, ordered programs only for verified condition syntax,
  shader adapters, resource layouts, strides and file sizes; reject unsupported
  or ambiguous chains without weakening guards or hiding valid static geometry.
- Discovery and resource state stay per INI. Nested GIMI children may inherit
  validated parent bindings and rebind the same verified base; replacing the base
  or resetting the output UAV is unsupported. Partial shape targets animate only
  vertices present in base, target and dispatch; omit passes with no drawn
  vertices. WWMI sparse tracks must match the narrow Present/two-pass shader
  contract and exact shape-buffer layout. An authored plain slider with an external
  phase stays a slider; synthetic sinusoidal or program-assigned phases are tracks.

## Baked tracks

- Position-switching tracks keep Direct3D zero reads for position/normal beyond a
  short VB (first frame included); keep topology and other frames rather than
  rejecting the track. Unsupported layouts and non-finite data stay rejected.
- Detection compares published positions and decoded normals on drawn vertices
  only, in backend frame preparation; unused fields, unreferenced vertices and
  unpublished normals never activate playback.
- Joined buffer animations form one ordered sequence per authored draw. Resolve
  inherited bindings under the same guard; frame clauses never become visibility
  controls. Deduplicate whole sequences keeping occurrences and source guards.
- Joined frames share one union vertex mapping across attributes with compatible
  index counts and valid referenced UVs (unused UV records may be absent).
  Unsupported families keep one canonical draw. Playback and release restore every
  animated attribute from the canonical baseline.
- Draw-range frames may change topology and UVs at a fixed index count; share
  vertex slots relative to each frame's source origin without unioning every
  disjoint copy into every frame.
- Prove immutable topology/UV state from sources, layouts, ranges and contents
  before position-only packing; keep preparation memory bounded across frames.

## Programs and runtime

- Execute assignments, resets, conditions and dispatches in authored order.
  Expressions compile through the shared condition syntax tree; if/elif/else
  branches are chosen once on entry, including child calls. Controls are external
  inputs and state rules the shared derived dependency model. Tracks of one
  program share state but keep independent outputs; visibility and control changes
  wake tracks instead of rebuilding meshes.
- Deform stable position/normal buffers from an immutable baseline. Sparse WWMI
  output overlays shape/rest state; disabled passes freeze without erasing others.
  Rig/Physics suspends animation ownership; release restores geometry and bounds.
  Advance time at render cadence but apply compute geometry at most 60 Hz. Use TSL
  compute on shared GPU vertex buffers for shapes and dual-quaternion skinning; CPU
  tools evaluate the last applied pose lazily with no per-frame readback. Declare
  shared outputs as storage-capable attributes before any draw.
- Visible active animation autoplays. Pause freezes runtime time without changing
  INI controls; control changes still evaluate with zero dt and paused playback
  schedules only explicit updates. Seeking reuses decoded baked frames only for one
  active synchronized clock and respects Rig/Physics ownership.
- One Play/Pause button: in the timeline for seekable baked animation, otherwise
  in the viewport toolbar; hidden without visible, unsuspended active animation.
  Decide from clock/track metadata, never by scanning frame buffers. Visibility
  changes wake the existing runtime; tracks on one clock keep its phase.
