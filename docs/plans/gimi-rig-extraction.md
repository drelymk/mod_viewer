# Plan: Genshin (GIMI) "Extract Rig/Animation"

Status: implemented on this branch (Phases 1-7). Scope: enable the existing shared `Extract Rig/Animation`
workflow for GIMI Assets. It produces a verified `Rig.json`, an animation library
and `RigExtraction.json` from an installed Genshin Impact client, following the
contracts in `docs/asset-rig.md` and `context.md`.

The ZZZ adapter (`src/app/assets/game_extract/zzz*.py`, `native/zzz-extract`) is
the template. Genshin differs in four ways, each covered by a phase below:

1. **Archives:** `Blb\x03` blocks live in two trees, and `Persistent` copies
   replace `StreamingAssets` copies.
2. **Clip decoding:** clips use `GIACLClip`, decoded with AnimeStudio's
   `AnimeStudio.ACL.DB` (ACL database streaming), not ZZZ V2.
3. **Body motion:** the main body is stored only as Unity Humanoid (Mecanim)
   muscle curves in clips shared per body type. A muscle→bone solver is required.
4. **Layering:** each character's AnimatorController pairs the shared body
   clip (motion set 0) with that character's hair/cloth "streamer" clip
   (motion set 1) per state.

## 0. Facts this plan relies on (verified against a local 7.2 install)

Verified with a scratch probe; keep this evidence and any real game data out of
the repository and PR text.

- **Install layout.** Game folder `.../GenshinImpact_Data/`. Blocks are under
  `StreamingAssets/AssetBundles/blocks/NN/*.blk` and
  `Persistent/AssetBundles/blocks/NN/*.blk`, all with magic `Blb\x03`. Most
  `Persistent` files share a relative path with a `StreamingAssets` file and are
  newer, full replacements. The effective set is the union keyed by path relative
  to `blocks/`, with `Persistent` winning.
- **Parser.** The pinned AnimeStudio commit `76234e9f…` with `GameType.GI` reads
  every block with no errors. A metadata scan of every block takes ~4 minutes.
  Unity version string: `2017.4.30f1` (miHoYo-modified). Muscle attribute indices
  need no version remapping.
- **Avatars.** Named `Avatar_<Body>_<Weapon>_<Internal>_ModelAvatar`, e.g.
  `Avatar_Male_Sword_Alhatham_ModelAvatar`. Internal names differ from GIMI
  folder names (`Alhatham` vs `Alhaitham`), so names are hints only. The Avatar
  uses the standard Unity layout: `new Avatar(reader)`, `m_TOS` path table,
  `m_Avatar.m_Human` fully populated.
- **Meshes.** The character's model bundle holds the Avatar and its meshes
  (e.g. `Body` with 3 submeshes, plus LODs). A GIMI Asset's components match one
  native multi-submesh mesh exactly by per-section vertex/index counts. Its single
  VB covers all sections, and its Blend IDs index the mesh's full renderer
  palette. Every palette bone hash resolves in the Avatar `m_TOS` and skeleton.
- **Captured weights.** GIMI vertex dumps carry `BLENDWEIGHT R32G32B32A32_FLOAT`
  and `BLENDINDICES R32G32B32A32_SINT`, the existing `gimi_f32_u32_4` encoding.
- **AnimatorController.** `AnimatorController` named exactly the Avatar family
  (Avatar name minus `_ModelAvatar`) lives in another block. Layer 0
  `Base Layer` is motion set 0 with a full Humanoid body mask. Layer 1
  `StreamerSyncLayer` is synced on the same states, motion set 1, override blend,
  weight 1, with a 157-entry skeleton mask. Other layers (Head, StandbyIK, Shake,
  Dive…) are runtime-driven. No state sets `m_IKOnFeet`. The per-character
  `_Override` controller lists 0 clips. Clip PPtrs reference external CAB names
  (lower-case `cab-…`) plus PathIDs.
- **Body clips.** Shared, e.g. `Ani_Avatar_Male_RunCycle`. They hold Animator
  curves (customType 8): attribute 0–6 Motion T/Q, 7–13 Root T/Q, 14–41 four IK
  goals (LFoot, RFoot, LHand, RHand; T xyz + Q xyzw each), 42–96 the 55 body
  DoFs, 97–136 the 40 finger DoFs, 137–199 the 21×3 TDoF. They also hold a few
  Transform curves for non-human bones. Curves are split across ACL, streamed,
  dense and constant storage.
- **Streamer clips.** Per character, e.g. `Ani_Avatar_Male_Sword_Alhatham_RunCycle`:
  Transform curves for ~79 secondary/helper bones plus Motion T/Q only. Attack
  and skill states often use per-character clips carrying both muscle and
  Transform curves.
- **Solver accuracy.** The prototype Mecanim solver (section 4) reproduces:
  - the Avatar's stored rest body orientation exactly;
  - hand-to-hand distances within 0.7 mm;
  - hand IK goals within 3–10 mm on Alhaitham clips;
  - `+Mirror` clip pairs as exact reflections within 0.19 mm.

  Feet show a uniform, character-dependent offset (20–50 mm) against shared-clip
  goals. That is retargeting from the clip's authoring rig, not a solver error,
  and in game no foot IK corrects it.

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Extend the existing `native/zzz-extract` helper with a GI mode (`--gi <root> --work <work>`) instead of a third helper. Bundle kind stays `zzz-extract`. | Same pinned parser, curation and Oodle; avoids a second reader build and packaging kind. Renaming the helper is out of scope. |
| D2 | Add the `AnimeStudio.ACL.DB` decoder, rebuilt from the pinned source with MinGW, as the ZZZ V2 decoder is. | Runtime must not ship upstream prebuilt binaries; matches the ZZZ policy. |
| D3 | Only `GenshinImpact_Data` is supported. `YuanShen_Data` (CN client) is rejected with a clear message. | Only the global layout is verified. |
| D4 | Animation entries are **controller states**, not clips. Each state yields one clip combining motion set 0 (Humanoid body) and motion set 1 (streamer), paired by the state, at synced normalized time. | Mirrors in-game composition; pairing comes from the controller, never from names. |
| D5 | Clips are **in place**: the body frame is placed at `Motion⁻¹ · Root`. Root motion is not exported. | The viewer plays clips in place; root motion would drag the model off its Asset origin. |
| D6 | v1 omits: runtime-mirrored states (`m_Mirror` or a mirror parameter), multi-leaf blend trees whose motion sets disagree in leaf count, TDoF channels, twist distribution (`m_ArmTwist`…), IK passes, Head/StandbyIK/Shake/Dive layers, facial morphs. Each skip or omission is reported, not silent. | Keeps v1 verifiable; each item is an isolated follow-up. |
| D7 | Rig version 2, clip version 2 (`frame-major-tqs-f32le`), same as ZZZ. | Existing loaders and the WebGPU path already support them. |
| D8 | The solver runs in Python (`numpy`) in the adapter, vectorized over frames. | No native math to pin; testable with synthetic data. |

Per the global workflow, **Phases 1 and 4 are design-heavy (native reader
adaptation, Mecanim math): implement them directly**, not through the
implementer agent. The other phases are mechanical enough to delegate with
this plan.

## 2. Phase 1: native helper GI mode

Files: `native/zzz-extract/Program.cs`, new `native/zzz-extract/GiExport.cs`,
`native/zzz-extract/README.md`, `tools/build_zzz_extract.py`,
`src/app/assets/game_extract/zzz_runtime.py` (runtime manifest constants only).

1. **Argument parsing.** Accept `--zzz <root> --work <work>` (unchanged) or
   `--gi <root> --work <work>`. Select `GameManager.GetGame(GameType.GI)` for
   `--gi`. Everything else (request loop, response files, error shape, console
   suppression) stays shared.
2. **`Block()`.** Make the allowed prefixes game-specific:
   - GI: `GenshinImpact_Data/StreamingAssets/AssetBundles/blocks/` and
     `GenshinImpact_Data/Persistent/AssetBundles/blocks/`;
   - allow exactly one extra directory level (`NN/…blk`);
   - keep the existing reparse-point and `..` checks.
3. **`scan` / `scan_batch` (GI).** Rows for `Mesh` (name + `shape` as now),
   `Avatar`, `AnimationClip`, `AnimatorController` (name only), all with the
   full locator (`block, offset, cab, file_ordinal, path_id`). No
   `SkinnedMeshRenderer` rows in GI v1.
4. **`read` Mesh (GI).**
   - Reuse `ExportMesh`.
   - Throw `InvalidDataException` when `m_Skin.Count != m_VertexCount`. That
     covers `m_PackSkinDataToUV2UV3` meshes and other unsupported layouts.
5. **`read` Avatar (GI)**, in `GiExport.ReadAvatar`. Use `new Avatar(reader)`.
   - Export `bones` with the same shape as ZZZ (`path` from `m_TOS`, `hash`,
     `parent`, TRS from `m_AvatarSkeletonPose`). Reject a missing TOS path,
     duplicate hash or count mismatch.
   - Add `human`:
     - `skeleton.parent`, `skeleton.axes_id`;
     - `axes` as `{pre_q, post_q, sgn, min, max, length, type}`, with Vector4
       min/max/sgn reduced to xyz;
     - `pose` as TRS;
     - `bone_index` (exactly 25), `left_hand`/`right_hand` (15 each or empty);
     - `mass` (25), `scale`, `arm_twist`, `forearm_twist`, `upperleg_twist`,
       `leg_twist`, `has_tdof`;
     - `skeleton_index` (`m_HumanSkeletonIndexArray`, human node → Avatar node),
       `root` (`m_Human.m_RootX`).
   - Throw if any axes `type != 1` (only ZYRoll is supported) or any index is out
     of range.
6. **`read` AnimationClip (GI).**
   - Require `m_MuscleClip.m_Clip.m_ACLClip is GIACLClip` (an unset ACL clip is
     allowed: streamed/dense/constant only).
   - Decode with a new `NativeAclDb.Decode(GIACLClip)` P/Invoking
     `AnimeStudio.ACL.DB` `DecompressTracks(data, db, streamer, ref Samples)` /
     `Dispose`:
     - 16-byte-aligned copies sized `length + 16`. Do **not** copy upstream's
       `+8` allocation, which can overrun after alignment.
     - The `streamer` pointer is computed with upstream's
       `TryGetDatabaseBulkOffset` rule (magic `0xAC11DB01`, inline-bulk flag,
       bounds check).
     - Bound `Count`/`Frames` as `NativeAcl` does.
   - Emit the same JSON shape as `ExportClip` (start, stop, file, times,
     acl_count, bindings, streamed, dense, constant) plus `motion_set_hint`
     (none) and `mirror` (`m_MuscleClip.m_Mirror`).
   - Bindings keep `custom` (customType) and `type`, so the Python side can
     separate Animator muscle curves (`type == "Animator" && custom == 8`).
7. **`read` AnimatorController (GI)**, in `GiExport.ReadController`.
   - Export `layers` as `{name (via m_TOS), state_machine, motion_set,
     blend_mode, weight, synced_affects_timing, skeleton_mask: [{path_hash,
     weight}]}`.
   - For the Base Layer state machine, export `states` as `{name (via m_TOS),
     speed, mirror, mirror_param, ik_on_feet, motions: [per motion set: null |
     {leaves: [{cab, path_id, node_index, threshold}], blend_type}]}`.
   - Resolve each leaf with `m_AnimationClips[clipID]`. `cab` is the PPtr's
     external file name, or the controller's own CAB when `m_FileID == 0`.
   - Bound states ≤ 4096, leaves ≤ 64 per tree, clips ≤ 8192.
8. **Self-test.** Extend `--self-test` to load `AnimeStudio.ACL.DB.dll` and
   resolve `DecompressTracks`/`Dispose`. Also instantiate
   `GameManager.GetGame(GameType.GI)`.
9. **Build** (`tools/build_zzz_extract.py`).
   - Compile `AnimeStudio.ACL/AnimeStudio.ACL.DB/dllmain.cpp` with the same
     g++ flags as ZZZ V2 (`-std=c++20 -O2 -shared -msse4.1 -I. -DNDEBUG
     -D_AS_DLL -static …`). Add any export define the header requires, checked
     against `dllmain.h`.
   - If MinGW cannot compile it unchanged, stop and report. Do not patch
     decoder math.
   - Copy its `acl`/`rtm` LICENSE files into `licenses/`.
   - Bump `VERSION` to `zzz-4-<commit12>`. Keep ZZZ `INDEX_VERSION` unchanged:
     ZZZ scan output does not change.
   - Add the new `.cs` files to the `dotnet format --include` list.
10. **Runtime manifest.**
    - In `zzz_runtime.py`, add `AnimeStudio.ACL.DB.dll` to the `required` set and
      update `VERSION`.
    - Update `docs/THIRD_PARTY_NOTICES.md` for the ACL.DB decoder (ACL/RTM MIT).

Invariants:
- The helper never exports YAML, never reads outside the selected root, and
  restricts external resource reads to the current bundle.
- Locators always carry block + offset + CAB + ordinal + PathID.

Checks:
```
python tools/build_zzz_extract.py --verify-format
```
```
.venv-test\Scripts\python.exe -m pytest -q src/tests/build/test_helper_bundle.py src/tests/app/assets/test_zzz_extraction.py
```
(Confirms the ZZZ mode is unchanged.)

## 3. Phase 2: GI runtime, archives and index

New files:
- `src/app/assets/game_extract/gi_runtime.py`
- `src/app/assets/game_extract/gi_index.py`

1. **`gi_runtime.py`.**
   - `DATA = "GenshinImpact_Data"`.
   - `BLOCK_DIRS = (DATA + "/StreamingAssets/AssetBundles/blocks",
     DATA + "/Persistent/AssetBundles/blocks")`.
   - `INDEX_VERSION = "gi-1-" + zzz_runtime.PARSER_COMMIT[:12]`.
   - `validate_folder(folder)`:
     - same absolute/resolve rule as ZZZ;
     - requires `BLOCK_DIRS[0]`;
     - if `YuanShen_Data` exists and `GenshinImpact_Data` does not, raise
       "Only the global Genshin Impact client layout is supported.";
     - then calls `archives(root)`.
   - `archives(root)`:
     - walk both trees one level deep (`*/*.blk`), with ZZZ's link/containment
       checks;
     - key by the path relative to its `blocks` dir, casefolded;
     - a `Persistent` entry replaces the `StreamingAssets` one;
     - return the sorted effective list; raise when empty.
   - `Client(runtime.Client)`: same as the ZZZ `Client`, but `command()` uses
     `--gi`, and `ensure_runtime` is reused from `zzz_runtime`.
2. **`gi_index.py`.** Copy the structure of `zzz_index.py`, minus renderer bone-path
   pooling:
   - `FORMAT = 1`, file `game-gimi-<hash>.json`;
   - `valid_rows` accepts types `Mesh`, `Avatar`, `AnimationClip`,
     `AnimatorController`;
   - `fingerprint` uses `gi_runtime.archives`;
   - the `scan`/`build`/`ensure`/`status`/`remove` semantics are identical,
     including "installation changed during indexing";
   - add `by_cab(rows)` returning `{cab.casefold(): [rows]}` for PPtr
     resolution. Duplicate CAB names in different blocks make that CAB
     ambiguous: never pick one.

Invariants:
- The index is an accelerator only; every adoption is proven by native reads.
- Failures abort extraction visibly.
- Persistent-over-StreamingAssets precedence is part of the fingerprint
  (the effective list).

Tests: new `src/tests/app/assets/test_gi_extraction.py` (scenario-style; temp
folders with fake `.blk` files):
- folder validation, including the CN-layout rejection and link rejection;
- effective set with `Persistent` overriding, and fingerprint change on
  override;
- index build, reuse and staleness, and failed build keeping the old cache;
- `by_cab` ambiguity.

```
.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_gi_extraction.py
```

## 4. Phase 3: Mecanim Humanoid solver (implement directly)

New file: `src/app/assets/game_extract/mecanim.py`. Inputs come from the Phase 1
Avatar `human` block and per-frame Animator channels. Quaternions are
`(x, y, z, w)`; everything stays in Unity/Avatar space. Vectorize over frames:
arrays shaped `(F, …)`.

Exact rules, verified in the prototype:
- **Human bone order (25).** Hips, LUpperLeg, RUpperLeg, LLowerLeg, RLowerLeg,
  LFoot, RFoot, Spine, Chest, UpperChest, Neck, Head, LShoulder, RShoulder,
  LUpperArm, RUpperArm, LLowerArm, RLowerArm, LHand, RHand, LToes, RToes, LEye,
  REye, Jaw.
- **Body DoF (x=twist, y, z) per bone**, as offsets into muscles (attribute 42 + n):
  - Spine (2,1,0); Chest (5,4,3); UpperChest (8,7,6); Neck (11,10,9);
    Head (14,13,12);
  - LEye (−,16,15); REye (−,18,17); Jaw (−,20,19);
  - LUpperLeg (23,22,21); LLowerLeg (25,−,24); **LFoot (−,27,26)**;
    LToes (−,−,28);
  - right leg +8 (29–36);
  - LShoulder (−,38,37); LUpperArm (41,40,39); LLowerArm (43,−,42);
    LHand (−,45,44);
  - right arm +9 (46–54).
- **Fingers** (attribute 97 + 20·hand + 4·finger + k; thumb, index, middle,
  ring, little; hand bones in proximal/intermediate/distal order):
  - proximal = (−, Spread k=1, 1Stretched k=0);
  - intermediate = (−, −, 2Stretched k=2);
  - distal = (−, −, 3Stretched k=3).
- **Muscle → angle:** `a = m * (max if m >= 0 else -min)` per axis (radians,
  from the Avatar limits). Then `h = tan(a * sgn / 2)`.
- **ZYRoll:** `q = normalize(hx, hy + hx*hz, hz − hx*hy, 1)`.
- **Local rotation:** `local = preQ * q * conj(postQ)`. Absent DoFs use `m = 0`.
  Human nodes without axes, and all local translations, keep `human.pose`.
- **Bone point:** `p(bone) = t + R·postQ·(L,0,0)`. The axis convention is
  verified: +x after postQ points at the child, with length `L` (`axes.length`).
- **Body centre of mass:** `Σ mass_i · (t_i + R_i·postQ_i·(L_i/2,0,0)) / Σ mass`
  over mapped bones with mass > 0.
- **Body orientation:**
  - `up = normalize(mid(LUpperArm,RUpperArm) − mid(LUpperLeg,RUpperLeg))`;
  - `right = (RUpperLeg−LUpperLeg) + (RUpperArm−LUpperArm)`;
  - `z = normalize(cross(right, up))`; `x = cross(up, z)`; `R = [x up z]`.
  - Up stays exact; orthogonalizing `right` instead was measured wrong by up to 15°.
- **Placement.** FK the human skeleton with the hips at their reference local
  transform, compute the body frame `B0`, then rigidly move every human global so
  that the body frame equals `B = (Tb·scale, Qb)`.
  - In place (D5): `B = Motion⁻¹ · Root` (Motion attributes 0–6, Root 7–13).
  - Hips local = (Avatar parent global)⁻¹ · hips global.
- **Self-check metric** (reported, and a gate only on gross failure):
  - hand IK goal = `Root + RootQ·(goalT·scale)` vs the solved hand joint;
  - foot goal vs the foot sole `p(Foot)`.
  - Record max hand and foot error / `scale` per clip. Reject a clip whose hand
    error exceeds `0.1·scale`.

Public API (suggested):
- `HumanSolver(human_json)`, which validates ranges and shapes;
- `solve(channels: dict[int, np.ndarray(F)]) -> (local_q: (F,N,4),
  hips_t: (F,3), hips_q: (F,4), metrics)`.

Tests: new `src/tests/app/assets/test_mecanim.py`, synthetic only:
1. ZYRoll equals swing(y,z)·twist(x) for random inputs.
2. Zero muscles give `preQ·conj(postQ)`; ±1 reach the limit angles.
3. A synthetic symmetric humanoid with mirrored axes (sgn) gives reflected
   poses under L/R-swapped, sign-adjusted channels.
4. The body frame of the solved pose equals the requested Root (and
   `Motion⁻¹·Root` in place) to 1e-6.
5. The vectorized path equals a per-frame scalar reference.
6. Rejects malformed `human` (bad lengths, axes type ≠ 1, out-of-range indices).

```
.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_mecanim.py
```

## 5. Phase 4: capture, discovery and rig proof

New file: `src/app/assets/game_extract/gi_pipeline.py`.

Changed files:
- `src/app/assets/game_extract/zzz_native.py`: parameterize `capture`;
- `src/app/assets/loader/hash_asset.py:789`;
- `src/app/assets/game_extract/zzz_animation.py`: extract sampling.

1. **Captured skinning.** `hash_asset.py:789`: build `capture_dump_skinning(...)`
   for `asset_type in ("ZZMI", "GIMI")`.
2. **Capture.** Turn `zzz_native.capture(root, record)` into
   `capture(root, record, asset_type="ZZMI")`, passed through to
   `load_hash_asset`. Error text uses the game label.
3. **Discovery** (`gi_pipeline.verify_candidates`).
   - Candidate Avatars: rows with `_family()` (reuse from `zzz_pipeline`).
   - The name query narrows as in ZZZ (same `_search_name`).
   - **Mesh hint = co-location:** meshes in the same `block` + bundle `offset`
     as the Avatar row (replaces ZZZ's `_mesh_family_hint`).
   - Exclude `_LOD[1-9]` names.
   - The shape fallback scores Avatars by captured `(vertices, index_count)`
     shapes of their co-located meshes.
   - Then reuse `zzz_native.mesh_placement`, `match_component_candidates` and
     the ZZZ model-wide placement/ambiguity logic unchanged. Factor the shared
     part of `zzz_pipeline.verify_candidates` into a function taking a
     `mesh_rows_for(avatar_row)` callback instead of copying it.
   - Avatar JSON for GI carries `human`. Exclude it from the equivalence key so
     that float noise there does not split equivalent copies. Equivalence stays
     the hierarchy/world/palette proof.
4. **Rig.** Reuse `zzz_pipeline._rig` (mesh-space root + Avatar bones + Biped
   `humanoid_controls` mapping), skin profiles from captured
   `blend_sha256` with `encoding="gimi_f32_u32_4"`, and the previous-rig
   preservation code, factored as above.
5. **Sampling refactor.** Move the channel sampling part of
   `zzz_animation.convert` (everything up to producing `sampled[frames, total]`
   with quaternion-aware ACL/dense sampling, streamed Hermite and constants)
   into `sample_channels(metadata, work, targets) -> sampled`. ZZZ `convert`
   calls it with no behaviour change.

Tests:
- extend `test_gi_extraction.py` with fake-client scenarios mirroring the ZZZ
  tests:
  - co-located mesh discovery;
  - an unrelated variant bundle not read;
  - proof rejection on wrong weights;
  - ambiguous equivalent copies;
  - a lower level-of-detail mesh excluded;
- the ZZZ suite must stay green.

```
.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_gi_extraction.py src/tests/app/assets/test_zzz_extraction.py src/tests/app/assets/test_loader.py
```

Findings while implementing (real install):
- Genshin meshes are bound in the Avatar's `m_DefaultPose`; `m_AvatarSkeletonPose`
  is the Humanoid-normalized pose and disagrees with the inverse binds by metres.
  The GI helper therefore exports the default pose as the rig reference. Humanoid
  solver output is absolute local rotation, so it is unaffected.
- Shared body-type eye bones carry ~2.4e-4 inverse-bind float noise, just above
  ZZZ's 2e-4 agreement. GI placement uses 1e-3 (sub-millimetre); ZZZ keeps 2e-4.
- Asset names can be substrings of a different character's internal name; a
  name match that cannot be proven falls back to geometry discovery (still
  fully proven). Cancellation is never swallowed by that fallback.

## 6. Phase 5: controller pairing and layered clip conversion

New file: `src/app/assets/game_extract/gi_animation.py`. Wire it into
`gi_pipeline.build`.

1. **Controller discovery.**
   - Exactly one `AnimatorController` row named `family` (the verified Avatar
     name minus `_ModelAvatar`). Zero or several is an error ("animation
     controller is missing/ambiguous").
   - Read it.
   - Base layer = the `layers` entry with motion set 0 and state machine 0.
   - Streamer layer = a synced layer (same state machine) with motion set 1 and
     override blend. At most one; none is allowed (body only).
2. **Per state.**
   - Skip with a recorded reason when:
     - `mirror` is true or `mirror_param` ≠ 0;
     - motion set 0 is missing;
     - leaf counts of sets 0 and 1 differ;
     - any leaf PPtr does not resolve to exactly one indexed `AnimationClip`
       row via `(cab.casefold(), path_id)` through `gi_index.by_cab`.
   - For multi-leaf blend trees, emit one entry per leaf pair (`node_index`
     order). Name it `<state>` for one leaf, `<state> <n>` otherwise.
   - Bound states and clip reads as ZZZ bounds clips (≤ 4096); report the counts.
3. **Conversion** (`convert_state(body_meta, streamer_meta, work, rig, avatar, solver, mask)`).
   - **Frames:** 60 Hz endpoint-inclusive over the body clip
     `[start, stop]`, as ZZZ does. The streamer is sampled at the same
     normalized times, `start_s + u·(stop_s − start_s)` (synced-layer
     semantics). Record both source durations.
   - **Body:** sample all channels with `sample_channels`.
     - Animator curves with custom 8 go to the solver.
     - Missing Root/Motion channels: reject.
     - Missing muscles default to 0. Note: default mid-range (0) is Unity's
       behaviour for absent muscle curves; document it.
     - TDoF (≥137) and unknown Animator attributes count toward
       `omitted_non_transform_channels`.
     - Transform curves on non-human Avatar bones apply as local TRS
       (unknown hash → reject, as ZZZ does).
     - Transform curves on human-mapped bones are ignored and counted (the
       Humanoid owns them).
   - **Streamer:** only Transform curves.
     - Apply only to bones in the streamer layer's skeleton mask with weight > 0
       and not human-mapped. Ignore and count others.
     - Animator Motion curves in streamer clips are ignored.
   - **Output.**
     - Rig bone order is `[mesh-space root] + Avatar bones`.
     - Human-mapped Avatar bones get solver locals; the hips get the in-place
       placement.
     - All other bones keep the reference pose unless written by
       body/streamer curves.
     - Write version-2 TQS binary and metadata exactly as ZZZ does, plus
       `layers` metadata:
       `{state, body_source, streamer_source, body_duration,
       streamer_duration, mask_bones, humanoid_goal_error: {hand, foot}}`.
   - Clip IDs and reuse go through `clip_id`/`reusable_clip_key`, keyed by
     state name + body/streamer source identities.
4. **Report.** `RigExtraction.json` with `game="gi"`, `controller` source,
   `states`, `imported`, `skipped`, `skipped_details` (state, reason),
   `animations` with provenance and goal metrics. Zero imported clips is an error
   that preserves existing files (ZZZ wording).

Tests (synthetic fake client in `test_gi_extraction.py`):
- a state pair producing body + streamer bones at synced normalized time with
  different durations;
- a mirror state skipped;
- a leaf-count mismatch skipped;
- an unresolved/ambiguous CAB skipped;
- a missing controller is an error;
- the human-bone Transform curve ignored and counted;
- an unknown Transform hash rejects the clip;
- the goal-error gate rejects a corrupted Root;
- a reimport keeps clip IDs;
- ZZZ files untouched.

```
.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_gi_extraction.py src/tests/app/assets/test_mecanim.py src/tests/app/assets/test_animations.py
```

Findings while implementing (real install):
- Newer controllers put an additive `AdjustLayer` on motion set 1 and the
  streamer on motion set 2. The streamer is the unique synced override layer,
  read from its own motion set; additive synced layers are reported as
  `unapplied_layers`, not converted.
- Shared body clips bind 3 bones that some Avatars lack. Unity ignores unbound
  curves, so they are counted as `unbound`, not rejected (deviation from the
  "unknown Transform hash rejects" bullet above).
- Dense rotation keys are ~0.1% off unit length; GI sampling accepts 1% before
  normalizing (ZZZ keeps 0.1%).
- When states share a clip pair, a plain state's name wins over a blend-tree
  leaf label. Empty body clips are skipped with their own reason; an empty
  streamer clip leaves the body motion unchanged.

## 7. Phase 6: mod-side palette proof and rig dispatch

1. **New `src/core/ini/gimi_skeleton.py`:**
   `stock_gimi_palette_sources(sections, resources, section_info) -> (blend_hashes, safe_sections)`.
   - Candidate sections are `TextureOverride*` sections with a
     `geometry_match_at_end` hash whose binding history binds **slot 1** to a
     resource. Their call closure must contain `handling = skip` and a
     non-indexed `draw = <n>, 0` (GIMI's skinning pass).
   - The resource must be file-backed, stride 32, `type = buffer` or untyped,
     never assigned or referenced elsewhere (same `written`/`referenced` rule
     as `zzz_skeleton`), and unremapped.
   - The closure must be safe as in `zzz_skeleton.safe_calls`: no shader
     bindings, no vertex-buffer rebinding in helper phases, and only known
     helpers.
   - Extract the shared safe-closure walk from `zzz_skeleton.py` into one
     function used by both. Do not duplicate it.
   - Allowed helpers add GIMI's stock pixel helpers
     `commandlist\global\orfix\orfix`, `commandlist\global\orfix\nnfix` and
     `commandlist\gimi\settextures`, next to the existing set. Document them as
     the GIMI equivalents of `commandlist\zzmi\settextures`.
   - Returns `{blend_file: native_hashes}`.
2. **`draw_groups.py`** (near lines 211 and 612).
   - Compute the GIMI proof.
   - After the ZZZ attachment, when a draw's `skinning_source` is
     `gimi_f32_u32_4`, is not ZZZ-attached, has no offset/remap/vg, has its file
     in the GIMI map, and its section is in the GIMI-safe set, set
     `native_palette_hashes` from the GIMI map.
   - The existing `legacy_component` resolution already finds GIMI Blend streams.
     Do not change it.
3. **`rig.py:413`.** Dispatch `binding.asset_type in ("ZZMI", "GIMI")` to
   `_resolve_zzz_rig`.
   - Its stream proof compares native hashes with the Asset's hash.json
     `blend_vb`, which GIMI hash.json provides.
   - `merged_layout` stays ZZMI-only: guard it with `asset_type == "ZZMI"`.
4. **`mod_preview.py:408`.** Include enabled `GIMI` roots in `enabled_roots`.
5. **`mesh_builder.py:227`.** Leave GIMI out (no morph binding in v1).

Invariants (context.md "Weight and secondary motion"):
- Retain the proof in the loaded skinning source; never reread INIs during
  Weight access.
- An equal numeric ID without proven palette provenance never admits a rig.
- Unchanged mods may use the exact Blend fingerprint profile.

Tests:
- `src/tests/core/ini/test_gimi_skeleton.py`: a synthetic GIMI INI
  (Position/Blend/Texcoord/IB overrides, swapvar conditionals, ORFix calls)
  proves the Blend. It rejects:
  - a written/referenced Blend;
  - a shader binding in the closure;
  - a missing `handling = skip` / `draw`;
  - a stride ≠ 32;
  - an unknown helper.
- `src/tests/app/assets/test_rig.py`: a GIMI binding with the proven native hash
  admits the stream palette; an unproven source returns None; ZZMI behaviour is
  unchanged.

```
.venv-test\Scripts\python.exe -m pytest -q src/tests/core/ini/test_gimi_skeleton.py src/tests/core/ini/test_zzz_merged_skeleton.py src/tests/core/ini/test_draw_groups.py src/tests/app/assets/test_rig.py
```

Findings while implementing (real install):
- Multi-variant mods issue `draw = $var, 0`; the skin-pass pattern accepts a
  literal or (namespaced) variable count.
- GIMI hash.json ranges have no counts, so exact bindings carry only the first
  index; the shared resolver matches a unique captured component by
  `(geometry_hash, first_index)` when the binding has no count.
- Mods using third-party helpers (e.g. TexFX) or bone IDs outside the captured
  palette stay on inferred rigging (fail closed).

## 8. Phase 7: enable, document, CI

1. **New `src/app/assets/game_extract/gi.py`** mirroring `zzz.py`:
   - `requires_index = True`;
   - `validate_folder = gi_runtime.validate_folder`;
   - index ops from `gi_index`;
   - `capture = partial(zzz_native.capture, asset_type="GIMI")`;
   - `build = gi_pipeline.build`;
   - `open_client` with `gi_runtime.Client`.
2. **`backends.py`:** `_BACKENDS = {"WWMI": wwmi, "ZZMI": zzz, "GIMI": gi}`. The UI
   is capability-driven; no web change is expected. Confirm `en.js` strings are
   game-neutral.
3. **Docs.**
   - `docs/asset-rig.md`: GIMI availability, the effective-block rule, the
     controller-state/Humanoid/streamer composition, in-place policy and v1
     omissions (D6).
   - `native/zzz-extract/README.md`: GI mode and the ACL.DB build.
   - `context.md`: add GI invariants next to the ZZZ bullet:
     - controller pairing only;
     - Persistent precedence;
     - Humanoid solver rules and the self-check gate;
     - no name-based pairing;
     - mod proof via the slot-1 skinning pass.
4. **CI.** The existing `zzz-extract` job builds the helper. Ensure MinGW
   compiles ACL.DB there. Keep game data and browser tests out of CI
   (AGENTS.md).
5. Run `python tools/quality_audit.py` (required before every commit).

Focused end-to-end checks:
```
.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_game_extraction.py src/tests/app/bridge/test_api.py src/tests/web/test_rig_extraction.py
```
(`test_rig_extraction.py` is a browser test: run it locally only.)

## 9. Local acceptance on a real install (not committed, not in PR text)

1. Build the helper, then in Settings build the GIMI index (~4–5 min; the
   effective block count matches `archives()`).
2. Extract a playable character Asset whose GIMI component counts match one
   native mesh, e.g. a male sword character. Expected:
   - all components proven;
   - a Rig with a mesh-space root plus ~168 Avatar bones and Biped
     `humanoid_controls`;
   - clips for most of the ~176 Base Layer states;
   - skipped states only with D6 reasons.
3. In Weight/Rig, play `Standby`, `RunCycle`/`Run`, a climb state and an attack.
   The body moves (not only hair), feet stay under the body in place, and
   hair/cloth follow the streamer layer.
4. Report metrics:
   - hand goal error ≤ ~0.03·scale on locomotion;
   - no clip rejected by the gate on a stock character.
5. Load a stock GIMI mod for the same character. It adopts the rig through the
   slot-1 proof and plays the same library. A mod with a remapped Blend stays on
   inferred rigging.
6. The ZZZ and WWMI extraction regression flows remain unchanged.

## 10. Risks and open questions

- **ACL.DB under MinGW.** It is untested. If it fails, decide between
  MSVC-in-CI and a reviewed source patch; do not ship upstream's prebuilt DLL.
- **Twist distribution** (`m_ArmTwist` 1, `m_ForeArmTwist` 0.35, …) is not
  applied. Joint positions and hand orientation matched the stored goals, so the
  impact is limited to limb roll. Revisit if skin twisting looks wrong in step 3.
- **Absent muscle default.** Treating absent muscle curves as 0 needs a check on
  a clip that omits some. The prototype clips carried all 55.
- **In-place placement (D5) is not yet prototyped.** The prototype placed the body
  with Root only. Before Phase 5 lands, confirm on locomotion clips that
  `Motion⁻¹·Root` keeps the hips above the feet with no horizontal drift and
  unchanged height. If Motion has an unexpected convention, fall back to
  subtracting the first frame's horizontal Root translation and yaw, and
  document that.
- **External GIMI helpers** (`ORFix`/`NNFix`) are assumed pixel-only, like ZZZ's
  `SetTextures`. Mods with custom vertex work stay unproven, which is the safe
  failure.
- **Blend trees.** Some states use parameter-driven trees; v1 exports per-leaf
  clips, not blended results.
- **Game updates.** A new client may change block encryption or class layouts.
  That needs a new pinned parser and a viewer release. No runtime updates.
