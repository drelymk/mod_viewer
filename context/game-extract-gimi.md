# Genshin (GIMI) game extraction

Shared rules: [game-extract.md](game-extract.md); GIMI follows the ZZZ proof in
[game-extract-zzz.md](game-extract-zzz.md) except where noted. GIMI mod palette
proof is in [weight-rig.md](weight-rig.md).

## Reader and index

- The same pinned reader in `--gi` mode with the database ACL decoder rebuilt from
  the same unmodified source. Accept only the global `GenshinImpact_Data` layout:
  blocks are `StreamingAssets` plus `Persistent` one directory below
  `AssetBundles/blocks`, a `Persistent` copy replacing the same relative path.
- Index Mesh, Avatar, AnimationClip, AnimatorController, SkinnedMeshRenderer and
  Follow rows as `game-gimi-<hash>.json` with ZZZ reuse/staleness rules. Renderer
  rows keep only path and explicit mesh/Avatar pointers (null-mesh renderers stay
  unrouted). The scan covers every block (unknown row types fail the build) but
  `gi_index.limit` keeps: every Avatar, meshes in `Avatar_…` family bundles' CABs,
  renderers drawing them, family controllers and their clip CABs, and a file table
  limited to the material CABs those renderers name and texture CABs those
  materials name. Kept rows keep every CAB copy.
- Skill objects (`SkillObj_<token>_…` Avatars, token = the family name's last part)
  keep their bundle's meshes, renderers and `Ani_SkillObj_<token>_` clip CABs, plus
  `Follow` rows (index `gi-5`): effect prefab roots with a
  `MoleMole.MonoEffectPluginFollow` component (by in-file MonoScript class), the
  point name in its first serialized field (null unless a length-prefixed printable
  name), its enabled flag, and each Animator below with explicit Avatar/controller
  pointers (either may be null, not both) and local transforms from the root's child
  to the Animator. Keep only rows whose Animators have a kept skill-object Avatar or
  controller. Resolve pointers only by case-insensitive CAB + PathID; a CAB in
  several blocks is ambiguous.

## Mesh proof

- A character's meshes share its Avatar's bundle (generic names are not evidence)
  and need the complete ZZZ proof; unproven name matches fall back to geometry and
  never swallow cancellation. Meshes bind in the Avatar's default pose (the rig
  reference); the Humanoid-normalized pose never proves inverse binds. GI placement
  tolerates 1e-3 inverse-bind noise. GI submeshes overlap vertex ranges, so index
  shapes narrow by index count only.
- Weightless captures (face parts) are attachments of a fully weighted native mesh
  and may show an expression: every captured position must equal native rest plus
  one least-squares combination of single-frame blend shapes (weights 0..1 with
  0.05 slack) within 1e-4, after one shared translation no longer than the GI
  tolerance. Vertices match by UV and explained position (seam duplicates resolved
  through shared triangles); captured triangles must be a sub-multiset of one native
  section; the match must share the skinned palette's placement. Rig.json's
  `attachment` holds per-vertex native weights and slots in captured order plus the
  solved expression; it adds no skin binding. Asset Weight/Rig publishes them
  (namespace `gimi_attachment`) only for parts whose fingerprints admit the palette.
- Facial curves route through renderer rows under the proven Avatar to skinned or
  attachment targets. Clamp per clip policy, then subtract the captured expression
  from animated channels only; unanimated channels keep the captured weight.
  Failures leave body motion unaffected (`facial_error`).

## Body motion

- Playable body motion exists only as Humanoid muscle curves, converted with the
  Mecanim solver (ZYRoll axes and limits, segment-midpoint centre of mass, up-exact
  body orientation) in place relative to Motion; reject a clip whose hands are more
  than 0.1 human scale from its own IK goals.
- Each Base Layer state is one clip: its body clip plus the unique synced override
  layer's clip at the body's normalized time, writing only non-human bones in that
  layer's mask. Pair only through controller states; a plain state's name wins over
  a blend-tree leaf sharing its clips. Skip mirrored states, mismatched blend trees
  and empty body clips with reasons; report additive synced layers as unapplied;
  count unbound, Humanoid-owned, masked, translation-DoF and non-transform channels.

## Controller sequences (Genshin and ZZZ)

- Controller reads export the base state machine graph (transitions, Any State,
  entry/exit selectors, named conditions). Sequences chain converted plain states
  only, from Any State targets, entry selectors, unreached states, then other edge
  targets. Selectors route by the first transition whose whole input holds; an
  unnamed input is never guessed past.
- A chain follows automatic exit-time edges or edges whose non-window conditions are
  a subset of the entry input, leaving at exit time (the later of exit time and
  window when the edge repeats the input) or the earliest window. Input edges need
  an exit time or window. Unity's default interruption applies: edges wait for the
  incoming transition, and a cycle closes only on its head if the closing transition
  completes before the head leaves. Speed other than 1 never joins. Windows are
  `NormalizedTime` (Genshin) or `FrameCount` (ZZZ). Runs inside a longer chain are
  dropped. Locomotion is game-code driven and never sequenced.

## Props

- `gi_props.py` reuses ZZZ's attachment proof and `prop-v1` layout over meshes in
  the verified Avatar's bundle (GI tolerance). Timing comes from prefabs: renderers
  route only through explicit mesh pointer, a verified Avatar and a path under it,
  and every routed prefab must draw the prop by default (renderer enabled, chain to
  the Animator active). Props any prefab hides wait for gameplay code and are
  skipped, as are unrouted or unproven ones. Weapons live outside the bundle and are
  not exported. Names never decide. Textures follow ZZZ's byte-exact slot learning
  against the Asset's resolved role textures (a payload two roles share proves
  neither); off-bundle pointers resolve through the limited file table.

## Skill objects (`gi_skill_objects.py`, preview-only name exception)

- Separate models with their own skeleton and clips, spawned and timed by encrypted
  gameplay configuration that is never read. Objects moving by root motion are
  skipped. Geometry keeps its own skin (at most 256 bones, LODs skipped); tracks are
  `prop-track-v2` (per frame and bone, f32 3x4 asset-space affine delta for the whole
  clip) with `socket` null and `track_layout`. Textures use the character's learned
  slots through the object's renderers. Report `skipped_props` (`kind`
  `skill_object`) and `skill_object_error`.
- Clip pairing: `Ani_SkillObj_<token>_<object>_<suffix>` pairs with the only body
  clip named `…_<token>_<suffix>` of the same length (within 1 ms); never choose
  between shared names. Otherwise names compare ignoring case, underscores and a
  `Bone` marker with its number, still needing one body clip, equal length and no
  other clip of the object claiming it.
- Controller runs: the object's own controllers (`Ani_SkillObj_<token>_<object>[_…]`,
  kept by the index) chain states the game plays without input. From any plain
  speed-1 state, each state's only unconditioned
  exit-time transition leads on until none remains. A run is named by its first
  state, controller-name-after-object plus that state, its first clip's name after
  the object's, and (from the entry state) the controller name after the object's
  alone. It pairs only with the one character clip whose length it fills and whose
  words a name has exactly (ignoring case, separators, `Bone` marker) or, failing
  that, whose first word a name starts with and whose other words it keeps in order;
  its clips then pair only through it. Length alone never pairs. A run no single
  clip fits may play through the leading steps of one derived character sequence
  when each step ends (within one 60 Hz frame) where a run state hands over or ends,
  the run fills the last step, and a name relates to the first step's clip;
  self-pairing clips keep their own track and multi-fit runs stay unpaired. Runs
  play states from exit time and offset, crossfade for the transition duration and
  hold a non-looping last state's end (up to one frame early); a looping last state
  must start within the clip.
- Placement from Follow prefabs holding the object's Animator (by Avatar, or by
  playing one of its controllers): the Follow point (empty = character) plus the
  prefab's transforms below its root, the Animator transform replacing the Avatar
  root rest unless animated. Prefabs whose Animator plays the pair's own controller
  decide first and must agree; else agreeing prefabs; else the most specific
  `Eff_<family>_<label>[_<object>][_SkillObj]` named for the clip (label letter/number
  runs begin the clip suffix's), else generic `Eff_<family>_<object>`. Disagreement or
  an unproven point, flag or Animator skips the clip with a reason. A point is the
  only character Avatar bone with that last name, or outside the Avatar the paired
  body/streamer clip's own `Root/<point>` track (streamer only inside its mask);
  the mount follows position and rotation only (collapsed scale skips). Tracks are
  baked per converted clip. Objects no Follow prefab holds sit at the character
  origin facing the same way, placed and timed by their own curves (scale included).
