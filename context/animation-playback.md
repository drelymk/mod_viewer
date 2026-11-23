# Game-clip animation playback

Asset animation catalogs, clips, sequences, secondary motion, shared libraries
and props as the viewer plays them. How each game produces them is in the
`game-extract-*.md` files.

## Catalog and clip format

- Optional Asset `Animation/manifest.json` is version 1; clips support v1 TQ and
  v2 TQS sidecars bound to the normalized skeleton fingerprint. Binary clips are
  bounded, checksummed, frame-major local float32 tracks in Asset coordinates.
  Timing is endpoint-inclusive with normalized quaternion interpolation, the
  original hierarchy and animated-world x inverse-rest-world deltas.
- Optional native morphs append named frame-major weights to the clip binary with
  a contained, checksummed sparse-target sidecar. Extraction and library
  preservation validate checksums and finite samples/deltas. Playback does fresh
  bounded reads with format, skeleton, length and container/range checks; no
  repeated full scans, library-wide cache or file watcher.
- Catalogs and clips use opaque loaded-session IDs, contained paths and lazy blob
  transport; clear publications on replacement and reject stale loads. Only exact
  compatible authored rigs (matching mods included) expose the library.

## Mesh binding

- Keep captured VB/IB fingerprint checks during Weight access. Original Assets use
  their compact mapping. Compatible mods are assumed to have original faces: match
  by complete topology/UV identity, unique UV triangles or unambiguous rest
  positions within 1e-4, independent of bone count; keep the mod's rest shape,
  winding/V conversions and verified palette; reject ambiguous UV assignments
  (new UVs stay unmapped). One bounded reference decode per component; morph inputs
  only for WWMI/ZZMI. No matching during playback.
- Apply morphs before skinning, upload only weights and restore the baseline on
  release. Skinning is TSL with each mesh's exact palette and all influences,
  uploading bone transforms (not vertices) each frame. Affine normals use each
  delta's inverse transpose on GPU and in lazy CPU deformation; manual quaternion
  rotation stays supported. CPU geometry stays lazy; finalize bounds on
  pause/seek/release.
- Playback may use one uniquely identified verified skeleton's compatible mesh
  subset while the manual ModelRig keeps all-source admission. Apply transforms
  through each mesh's exact game-bone palette, never inferred aliases. Unsupported
  meshes (and their loose parts) are filtered during playback/pause/seek, keeping
  eyes, controls and isolation; Stop, failure, manual posing, topology changes and
  disposal release the filter and restore geometry. Competing skeletons stay
  unavailable. Discover the binding once per loaded weight/model revision,
  independently of surface graphs, reconciliation and humanoid fitting. Prepare
  visible meshes lazily and reuse transform maps/normal matrices; pause/seek may
  finalize hidden compatible meshes without changing visibility.
- Restored Asset Fill parts keep opaque captured-weight sessions and join
  compatible playback lazily, but stay out of editable Weight, Rig, Physics and
  persisted selections; fill removal revokes their sessions and publications.

## Lifecycle and UI

- Playback owns the pose and suspends Weight physics; manual posing/editing stops
  it. Stop restores baseline geometry; disposal cancels pending loads/frames.
  Switching clips freezes the displayed pose until the replacement is ready, then
  starts at zero, keeping only active and incoming clips; failures still release
  ownership and restore the baseline.
- Opening Weight/Rig loads weights and the library, then activates the manual rig
  as the final stage (same lifecycle with or without clips). Animation controls
  stay disabled until activation settles; stale loads never activate a replacement
  model; playback reuses the rig. The opt-in Auto load Weight/Rig preference runs
  activation after automatic Asset Fill and requested textures settle, then plays
  the first clip, detached from the model transition and cancelled on every source
  replacement.
- Rig clips share the viewport playback bar (clip selector, previous/next, Play/
  Pause, seeking) synchronized with the Weight/Rig panel; models without clips keep
  ordinary mod playback controls.
- Clips without facial curves may borrow the library's eye-closing shape for a
  deterministic idle blink: the backend alone names it (whole-face, else a
  left/right pair, never one eye) from the manifest's optional `idle_face` (a clip
  `morphs` document), else the first catalog clip that has one;
  playback time drives it; paused playback and clips with facial curves are never
  altered; failure leaves the clip playable. Genshin clips record captured
  `rest_weights`; the blink uses one renderer's `Eye_WinkB` pair and crossfades its
  captured channels to closed through per-shape `scales`.

## Secondary motion

- Game clips leave runtime spring chains at rest. Extraction records each clip's
  `keyed_bones` and the manifest's `runtime_bones` (bones keyed by at most 5% of
  the clips keying their nearest authored ancestor, kept only where they or runtime
  descendants are skinned). A clip's `secondary_bones` = runtime minus its keys;
  `secondary-motion.js` simulates exactly those, rewriting only their local
  rotations. Never select by name; libraries without this evidence get none.
- A bone aims at the child with the most simulated descendants; chain ends follow
  rigidly. Tails spring toward their clip pose under the simulated parent, damped
  relative to that moving target, feel only part of the anchor's travel and keep
  link length plus per-joint and whole-chain bend cones. Gravity and leg capsules
  come only from authored humanoid controls.
- A held pose never moves by itself: gravity acts only as the anchor turns from its
  (re)start orientation, restarted chains settle before display, leg contact is a
  soft force against capsules no larger than each tail's bind-pose distance.
  Anchor jumps faster than a body can move reseed chains. Seeking, selecting and
  stopping reset; paused poses never step.

## Sequences and root motion

- Catalog `sequences` (2-64 steps of catalog clips with in-clip start/leave/fade
  seconds, `cyclic`, `restart`) validate all-or-nothing; an invalid sequence never
  hides clips. A crossfade never outlasts its incoming step and a cyclic restart
  precedes the head's leave (both validated and enforced). Sequences are not
  seekable.
- Selecting a sequence downloads its distinct clips sequentially and keeps them
  within a fixed byte budget until the selection changes (step changes must not
  wait on downloads); steps beyond the budget load one handoff ahead and hold the
  leaving pose. Stop, failure and reselection release the cache.
- Crossfades blend local transforms (shortest-path rotations). A fully cached
  sequence binds one merged facial source for its lifetime (targets merged by
  component and name, byte-identical) and crossfades facial weights; otherwise
  facial weights switch at the fade midpoint. A frame crossing a step's leave plays
  its remainder in the entering step.
- Root motion: an optional frames x 7 track after the morph weights, declared by
  `root_motion.bone` (the Avatar root whose local space holds it). Each step change
  carries travel so the entering clip starts at the leaving one's rigid transform,
  applied as an offset on that bone; crossfades blend each side with its own
  offset; steps without a shared space keep the offset. A single clip, and a first
  step whenever entered, starts at the origin facing forward. Seeking replays the
  carry from the origin through resident steps only, else starts the seeked step
  at the origin. Clips without a track keep their placement.

## Shared animation libraries (WWMI)

- Stored once per WWMI Asset Folder root in
  `SharedAnimation/<BodyType>-<Set>-<skeleton-package-hash>/`
  (`app/assets/shared_animations.py`), never inside an Asset; indexing ignores it.
  Each holds one `Role/<BodyType>/BaseAnim*` set on one source skeleton: manifest
  (skeleton, fingerprint, label, per-clip keyed bones) and v1 TQ clips in
  source-skeleton space, never duplicated per character.
- Listed after the Asset's own clips as `<label> / <clip>` for every rig that proves
  them, mapped onto the rig in memory. A source bone maps only when the rig has its
  name, its parent maps to the rig bone's parent (roots to roots) and reference
  rotation, translation and scale agree; name-only matches and everything below
  stay unmapped. Structural bones are the root plus keyed bones with a keyed child.
  A library applies only when the structural chain keyed by more than half its
  clips maps; a clip only when its own chain maps. Unmapped bones hold rest.
- Shared clips carry no morphs or root motion, may use the library blink and report
  only mapped keyed bones. Invalid libraries are skipped independently; discovery
  validates manifests without per-clip checks; loads keep bounded contained reads.
- Every compatible set is listed (which one a character uses is unreadable gameplay
  configuration); overlapping clip names stay distinct per set. Entries carry
  `shared: true` and appear in their own Shared group (playback picker also has a
  Shared filter); Previous/Next stay within it.
- Character extraction bakes the character's ribbon layers for shared clips:
  `<skin>/RibbonAnim*/Rib_<sequence>` pairs with every proven shared clip of that
  sequence name with identical frame count and duration (two matching layers pair
  with none). Store only tracks outside the clip's mapped joints under
  `Animation/SharedLayers/` (content-addressed TQ rows), listed as `shared_layers`
  with deduplicated `shared_layer_masks` and the library fingerprint; a layer
  applies only to that exact library build. The shared-clip secondary pool is
  `layer_bones` plus `runtime_bones` minus mapped joints; a clip's
  `secondary_bones` are that pool minus its mapped keys and baked layer. Asset clips
  keep `runtime_bones` minus keys.

## Props (all games)

- Props are listed in manifest `props` (`Animation/Props/`, content-addressed
  `prop-v1` blocks: f32 position/normal/UV, u16x4 joints, f32x4 weights, u32
  triangles); catalogs expose `animation_props`. Each prop load publishes fresh
  single-use geometry URLs after checksum/range checks (releasing previous ones);
  textures go through one publication per animation session. Prop failures never
  block clips.
- Playback skins props with each clip's bone deltas outside the editable rig and
  shows them only while a clip is applied (never at rest, in manual posing or after
  Stop), following the body mesh's model transform. Textured props use the body's
  game material profile and the shared registry under prop-scoped keys released on
  disposal. Windowed props (`visible` or track `start`/`end`) show only inside a
  window of the playing clip; props without windows (older libraries) show whenever
  a clip is applied. An abandoned or failed prop load disposes what it built.
- The Show props toggle (on by default; beside Stop in the playback dock, mirrored
  in the Weight/Rig animation section, shown only when the rig has props) shows or
  hides all props at once.
