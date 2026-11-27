# WuWa (WWMI) game extraction

Shared rules: [game-extract.md](game-extract.md). Playback contracts:
[animation-playback.md](animation-playback.md).

## Reader and index

- Uses the bundled direct CUE4Parse helper at its pinned revision, exposing only
  package search/folders, mesh/property and animation export, and one narrow
  prop-texture read: one Texture2D's stored top mip from a quality-tier folder
  (`HD`/`UHD` beside `Paks`), mounted lazily as its own provider so it never changes
  package resolution, with no image decoding.
- The game index (`game_extract/game_index.py`, `game-wwmi-<hash>.json` in the
  asset-index folder) caches only SkeletalMesh section shapes under `Role/*` and
  `Prop/RoleProp`. Missing, stale or invalid indexes fall back to native searches;
  a truncated direct-folder search or unreadable mesh metadata fails the build.
  Broad scans include direct packages before recursing.
- Building is explicit (Settings confirm or index button) or automatic when
  extracting without a ready index; the Browse pick is saved to Settings; clearing
  the folder deletes the index.
- The build also writes shared animation libraries (`game_extract/shared_library.py`)
  into every enabled WWMI Asset Folder root before the index, so a failed or
  cancelled library build leaves the index stale for retry. Sets are
  `Role/<BodyType>/BaseAnim*`; complete non-additive clips group by declared source
  skeleton, whose BoneTree must use only Animation retargeting. A library is reused
  while its fingerprint (converter version, skeleton fingerprint, every package's
  metadata digest) holds, else rebuilt in staging and renamed in; libraries of
  vanished sets are retired and staging leftovers removed.

## Character proof

- Try the character name first (an unverifiable name search falls back to
  geometry). Geometry candidates are meshes whose section vertex/index counts cover
  the most captured components; require the unique best geometry/UV/positive-weight
  proof and report unproven components before adopting skeleton and palettes.
- Captured IB offsets may restart across geometry hashes: match each component
  against all native sections with the same index count and require one complete
  exact proof. Keep captured offsets in rig identities and the native offset only in
  extraction state; reject duplicate matching sections.
- Leftover components (props) come only from a companion mesh found by the verified
  character folder name across the full Character tree; never truncate candidates.
  Require unique bone names, the body's induced hierarchy and identical world
  reference transforms (within 1e-4); omitted ancestor chains may collapse to the
  nearest represented ancestor. Each component is proven uniquely by one mesh with
  its palette remapped to body bones.
- Different mesh/source reference transforms are admitted only after reading the
  mesh's declared Skeleton and proving the same ordered names, parents and scales
  with Animation retargeting. A short or empty BoneTree means no retargeting for
  trailing bones; other modes are unsupported. Validate PSA reference bones against
  the source skeleton; keep authored absolute-local keys; omitted tracks use the
  mesh reference only when exported keys match the source reference.

## Secondary layers

- Conversion needs an explicit authored bone mask, a verified shared skeleton and
  reference-pose local additive metadata (CUE4Parse bakes layers to absolute local
  transforms). Complete zero-based companions with equal endpoint-inclusive spacing
  (within Float32 rounding) may be aligned by trimming a longer layer or holding a
  shorter one's last pose; body frames, duration and other tracks stay byte-exact.
  Companion paths match case-insensitively like Unreal packages (`Rib_X_loop` for
  `X_Loop`); paths differing only in case are rejected as ambiguous.
  Never stretch time or pair by timing alone; copy only into unchanged
  reference-pose tracks and refuse existing base motion. Character names and numeric
  bone thresholds are never proof. Record timing, held/trimmed counts and rejected
  companions with reasons (each counted once in the skipped total).

## Sequences

- Authored sequences are single AnimMontage timelines: one slot, contiguous
  segments at rate 1, loop counts expanded, explicit section links only, a link back
  to the first section repeats (hard cuts). Combo order between montages is not
  inferred. Report in `sequences`, `skipped_sequences`, `sequence_error`.
- Preview-only name exception: `X_Start`/`X_Loop`/`X_End[n]` clips in one folder
  form name-ordered sequences (one per end, hard cuts, each clip once), flagged
  `by_name` (shown with a link icon and hint; an older name ending ` (by name)`
  reads as flagged), listed after authored timelines and counted as
  `name_sequences`. Names never pair, identify or admit clips; stems with several
  starts or loops are skipped.

## Props

- Export a prop only with evidence of both attachment and timing (blueprint and
  effect props below). The regular weapon is not exported: nothing read says when
  it is drawn, so do not add it from model descriptions, ConfigDB tables or picks.
- Textures try HD then UHD. A material parameter takes a role only when its top mip
  is byte-identical to a captured texture of that role on a proven body section,
  or, for a role no parameter matches exactly, a same-sized texture decodes within
  8 levels per channel (game updates re-encode slightly). A parameter proving two
  roles stays unproven; several may prove one role. A role whose proven parameters
  name different textures is left out. Write the top mip unchanged as a single-mip
  DX10 DDS; V flips like every mesh. Report `prop_error`, `skipped_props`,
  `texture_error`. A prop id covers geometry and animation fields.
- Blueprint props: a construction-script component (`SCS_Node` under
  `CharacterMesh0` with an `AttachToName` socket, mesh from the most derived
  template, weapon cases excluded, no relative offset beyond 1e-3) that the
  character's notifies show while the prop's own animation plays. Geometry keeps its
  own skin (at most 256 bones) at the socket's rest world. Name exception (the game
  pairs these in animation blueprints): the prop sequence in the prop mesh's folder
  on its skeleton named after the clip exactly or with `_<suffix>` (longest clip name
  wins), same frame interval, no longer than the clip; it pairs with every same-named
  clip it fits, and a clip paired twice is not guessed. Bake `prop-track-v1` tracks
  (per frame and bone, asset-space translation + quaternion relative to the rigid
  socket rest). Prop sequences keep their PSA scale keys (the game hides props by
  scaling a bone to 0 and resizes them); a key is relative to the bone's rest scale (a
  key of 1 draws the built size, as frame dumps show for a rest scale other than 1).
  A prop whose sequences scale any bone off its rest scale bakes every track as
  `prop-track-v2` affine rows instead, for blueprint and effect props alike. Manifest
  props carry `socket`, `bone_count`, `tracks` (clip id -> file, sha256, frame_count,
  duration, window `start`/`end`) and, when affine, `track_layout`.
- Serving checks every track and publishes one fresh single-use blob of the
  session's clips per load; a prop lacking a track for any session clip is left
  out. Playback applies the socket's clip transform over the track sampled at clip
  time minus window start, holding the last frame (crossfades hand over halfway).
- Effect props are meshes a clip's own effect notify spawns, reproduced as the
  game's effect scripts run them (`AnimNotifyStateEffect`, `EffectModelGroupSpec`,
  `EffectModelSkeletalMeshSpec`, `EffectSpec`, `EffectLifeTime`). `pipeline.sequence`
  keeps each `AnimNotifyStateEffect_C` as facts (asset, `SocketName`, `Attached`,
  `FasterStop`, times, settings). Groups (`EffectModelGroup_C`) list models with
  `Value` delays; a notify may name one `EffectModelSkeletalMesh_C` directly (only
  its own lifetime applies), which names `SkeletalMeshRef` and the once-played
  `AnimationRef`. Mesh, animation (possibly another
  folder on an identical-bone skeleton), placement and timing all come from game
  data; nothing pairs by name. An animation an effect plays belongs to that effect,
  so blueprint pairing never claims it; if unreproducible the prop is absent.
- Effect placement: attached effects follow their socket (a unique rig bone);
  `Attached` defaults on. Unattached ones are placed once at the socket's clip-pose
  world at spawn (`socket` null). Socketless ones sit at the mesh origin, accepted
  only while the clip's root bones rest at spawn. Notify `Location`/`Rotation`/`Scale`
  and group/model transforms (`Constant`, or axis curves only with `bUseCurve`; Rotation
  axes are Roll, Pitch, Yaw; curve time counts from that level's start) compose socket
  x notify x group x model; offsets after the mesh animation's last frame hold its value.
- Effect timing: spawn at the notify's absolute start; the mesh plays after its
  group delay; each level has a `StartTime`/`LoopTime`/`EndTime` lifetime that ends
  after their sum without a loop, or with one lives until the notify ends and then
  plays out unless `FasterStop` (default on) stops it. Hide when either level ends
  and never after the clip (`ContinuousLoop` only concerns particle groups).
  Settings are classified by `wuwa_effect_settings`: handled, ignored, or blocking
  unless at a neutral value. Unknown settings never drop a mesh; the prop's report
  entry lists them as `unverified_settings` (report only). Blocking ones (`NotRealAttach`,
  `AttachLocationOnly`, `AttachLocationTime`, tag gates `PlayNeedTags`, `HideFrames`,
  looping mesh animations) go to `skipped_props` (`kind` `effect`), never approximated.
  The animation must convert
  onto the mesh's own bones. One mesh at one placement is one prop; the n-th spawn
  is the n-th instance. Tracks carry the clip window and hold their last frame.
