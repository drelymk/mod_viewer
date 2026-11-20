# Plan: Genshin authored animation sequences

Status: implemented on this branch. Refinements made during implementation are
marked "(implemented)" in section 2b. Scope: export the Genshin AnimatorController's authored
transitions and play the deterministic chains they define (e.g. the
normal-attack combo) as one previewed sequence with the authored crossfades.
Locomotion (Walk/Run/Sprint and the `*To*` states) has no authored edges: game
code drives it, so it is out of scope.

## 0. Facts this plan relies on (scratch probe of two 7.2 controllers)

Keep probe output and real game data out of the repository and PR text.

- The pinned AnimeStudio reader already parses `TransitionConstant`,
  `ConditionConstant`, Any State transitions, `SelectorStateConstant`
  entry/exit selectors, state `m_Loop`/`m_TagID`/`m_FullPathID` and parameter
  names (`m_TOS`) for `GameType.GI`, with no format changes.
- Transition `m_DestinationState` < state count is a state index. A value of
  30000 + i is selector `i` of the same machine. Selector paths do not resolve
  through `m_TOS`.
- Condition modes: 1 If, 2 IfNot, 3 Greater, 4 Less, 6 Equals, 7 NotEqual.
- Combo edges look like `DoSkill If & SkillID == 1 & NormalizedTime > a
  [& NormalizedTime < b]` with `has_exit_time = false`. `NormalizedTime` is a
  game-driven parameter and is treated as the source state's normalized time
  (an assumption; it is named alongside `#NormalizedTime`).
- Auto chains are unconditional edges with `has_exit_time = true`
  (e.g. `ExtraAttack -> ExtraAttack_AS`, `Standby2ClimbB -> ClimbIdle`).
- Any State edges into the combo carry the entry signature
  (`DoSkill If & SkillID == 1 & &CanUseSkill If & ComboCount == n`).

## 1. Native export (`native/zzz-extract/GiExport.cs`)

In `ReadController`, keep every existing field unchanged and add the
following, for state machine 0 only (the base layer's machine):

- Per state: `loop` (`m_Loop`), `tag` (`Name(m_TagID)`),
  `path` (`Name(m_FullPathID)`), `transitions` (an array of `Transition(t)`).
- Top level: `default_state`, `any_state` (an array of `Transition(t)`) and
  `selectors` (each `{ entry: m_isEntry, transitions: [{ dest: m_Destination,
  conditions }] }`).
- `Transition(t)` = `{ dest, duration, offset, exit_time, has_exit_time,
  fixed_duration, conditions }`. Each condition is
  `{ param: Name(m_EventID), mode, threshold }`. A null `param` is allowed: an
  unnamed parameter never matches a signature.
- Bounds: add `MaxTransitions = 65536` across all arrays combined and
  `MaxConditions = 64` per transition. Exceeding either throws
  `InvalidDataException`.

Bump `zzz_runtime.VERSION` from `zzz-5-` to `zzz-6-` so an installed helper
without transitions is rebuilt. Do not change `INDEX_VERSION` in either game,
because the index stores no controller contents. Update
`native/zzz-extract/README.md` (Genshin mode) to list the exported graph
fields.

## 2. Sequence derivation (`src/app/assets/game_extract/gi_animation.py`)

### 2a. State indices on pairs

`state_pairs` records the indices of the plain states (single-leaf blend
trees) that map to each pair, in a new `state_indices` list. A plain state
that shares an existing pair appends its index. Blend-tree leaves never
contribute. `pair_source` and the provenance stay unchanged; `state_indices`
is stripped from the provenance dicts the same way `plain` is.

### 2b. Pure `sequences(controller, clips)` → list

`clips` maps a state index to `(clip_id, duration)` for every converted plain
state. The function is pure and deterministic:

1. **Signature.** A transition's signature is the frozenset of
   `(param, mode, threshold)` for its conditions whose `param` is not
   `NormalizedTime`. Its window is the largest `NormalizedTime` Greater
   threshold (default 0) and the smallest Less threshold (default none).
2. **Heads**, in this order:
   - (a) Any State transitions, in authored order. The head signature is the
     transition's signature.
   - (b) Entry-selector transitions, in authored order. The head signature is
     the transition's signature.
   - (c) Converted states with no incoming state, Any State or selector edge,
     in index order. The head signature is empty.
   - (d) (implemented) Destinations of state-to-state edges, under that edge's
     signature, so chains such as `ExtraAttack -> ExtraAttack_AS` are found.

   Only destinations in `clips` qualify.
3. **Continuation from state S** (with the head signature H). The candidates
   are S's transitions whose destination is in `clips` and that satisfy one
   of these:
   - Auto: no conditions, `has_exit_time`, and `0 < exit_time <= 1`. The
     leave point is `exit_time`.
   - Combo: `not has_exit_time`, a signature that is non-empty and a subset
     of H, and a window lower bound `a` with `0 <= a < 1`. The leave point is
     `a` (the earliest legal input, i.e. continuous input).

   Pick the candidate with the smallest leave point; ties go to the earliest
   authored transition. (implemented) A candidate must leave after the
   current step's start, rather than dropping the whole chain later.
4. **Walk** from the head, appending steps until there is no candidate, or
   the destination is already in the chain, or there are 64 steps. A
   destination already in the chain closes a cycle only when it is the head;
   otherwise stop before it. Keep chains of 2 or more steps.
5. **Step timing**, in seconds of that step's clip:
   - `start` = the incoming edge's `offset × duration`, or 0 for the head.
   - `leave` = `leave_point × duration`.
   - `fade` = `duration_field` if `fixed_duration`, else
     `duration_field × source duration`, clamped to
     `[0, min(source duration − leave, next duration − next start)]`.

   Require `start < leave`, or drop the chain. The last step's next step is
   the head:
   - If the cycle closed, the last step's `leave` and `fade` come from the
     closing edge, and the head's restart `start` comes from that edge's
     offset. Store it as the sequence's `restart` and use it on cycles after
     the first.
   - Otherwise `leave` = the clip duration, `fade` = 0 and `restart` = 0.
6. **Dedupe** chains with the same ordered clip-id cycle up to rotation. The
   first in head order wins. (implemented) Process longer chains first and
   drop a path that is a contiguous run of an accepted chain (wrapping for
   cycles). A cycle is only covered by an equal cycle. Output keeps head order.
   Collect at most 1024 candidates.
7. **Identity and name.**
   - `id = "seq-" + sha256(json([clip ids, timing rounded to 1e-6]))[:24]`,
     which matches `_ID` and cannot collide with clip ids (no clip id starts
     with `seq-`; assert it).
   - `name` = `"<group>: <first> – <last>"`, where `<group>` is the
     second-to-last dot segment of the head state's `path`, falling back to
     the head name. Truncate to 128 characters.
8. Return at most 256 sequences:
   `[{id, name, cyclic, restart, steps: [{clip, start, leave, fade}]}]`.

Missing graph keys (an older helper) return `[]`.

### 2c. Pipeline (`gi_pipeline.build`)

- After the conversion loop, build `clips` from the written clip keys
  (`entries[key]["duration"]`) for each pair's `state_indices`. Pairs that
  were skipped or deduplicated by digest contribute nothing.
- Write `sequences` into `Animation/manifest.json`, which stays at version 1
  (an optional key). Add `sequences=len(...)` to `RigExtraction.json`.
- ZZZ and WuWa manifests are unchanged.

## 3. Catalog (`src/app/assets/animations.py`, `AnimationSessions.catalog`)

- Validate the optional `sequences` key:
  - It is a list of at most 256 dicts, and ids match `_ID`, are unique and
    are not clip ids.
  - Names are 1 to 128 characters, `cyclic` is a bool, and `restart` is
    finite with `0 <= restart`.
  - There are 2 to 64 steps. Each `clip` is in `entries`. All numbers are
    finite. `0 <= start < leave <= duration` of that clip,
    `0 <= fade <= duration − leave`, and `restart <` the head clip's duration.
- On any violation, drop **all** sequences and keep the clips.
- Return `"animation_sequences": [{id, name, cyclic, restart, steps}]` beside
  `"animations"`, and `[]` when absent (implemented: the bridges spread the
  catalog into the web rig, so the key arrives as `rig.animation_sequences`). `load(token, clip_id)` is unchanged:
  the web loads step clips by clip id.
- Check that `asset_preview.py` and `mod_preview.py` pass the catalog through
  unchanged (they spread it), and add the key wherever they rebuild it.

## 4. Web runtime (`src/web/js/weight-rig/rig-animation.js`)

- **Split sampling.** Add `sampleLocalPose(rig, clip, values, time, out)`,
  which writes T(3) R(4) S(3) per bone into `out` (a Float32Array of
  bones × 10, using the rig scale for 7-wide layouts). Add
  `composePose(rig, locals)`, which returns the existing `Map` of delta
  matrices. `sampleAnimation` becomes
  `composePose(rig, sampleLocalPose(...))` with identical results; keep its
  export and the existing tests.
- **Blending.** Add `blendLocalPose(a, b, weight, out)`: lerp T and S, and
  slerp R along the shortest path (negate b when dot < 0), then normalize.
  Reuse two module-level scratch arrays sized per rig, with no per-frame
  allocation beyond what `sampleAnimation` already does.
- **Sequence mode in `createRigAnimation`:**
  - The snapshot adds `sequences` (from `getRig()?.animation_sequences ||
    []`), `step` and `stepCount`. `selected` may be a sequence id.
    `duration`/`time` report the current step's clip.
  - The clip download and validation in `select` moves into one internal
    `loadEntry(rig, clipId, generation)` that returns `{clip, values, blink}`
    or throws. Both modes use it, and the stale-generation checks stay
    identical.
  - Selecting a sequence loads step 0 then step 1 (reusing the same object
    when the clip ids match). It then starts at `steps[0].start` with
    `prepare()`, `suspendPhysics(true)` and playing.
  - Tick:
    1. Advance the active time.
    2. When it reaches `leave` and the incoming step is loaded, enter the
       fade: the outgoing clip continues from `leave`, the incoming clip
       starts at its `start`, and the weight is `elapsed / fade`.
    3. When the fade completes (or immediately when `fade == 0`), drop the
       outgoing clip and begin loading the step after next. Hold at most
       active + incoming.
    4. If the incoming step is not loaded at `leave`, hold the outgoing pose
       at `leave` until it is.
    5. After the last step, wrap to step 0, and use `restart` instead of
       `steps[0].start` when `cyclic`.
  - Morphs come from the outgoing clip until the fade's midpoint, then from
    the incoming clip (their sparse targets differ). The idle blink uses the
    first loaded clip's blink.
  - `seek` is a no-op in sequence mode. `stop`, failures, `select` of another
    id and disposal keep the existing release semantics, and a failed step
    load fails the whole sequence like a failed clip.
- **`weight-rig-core.js`:** no change needed (implemented): the catalog's
  `animation_sequences` already arrives on the rig object.

## 5. UI

- **`fillRigClipSelect(select, clips, sequences = [])`:** after the clips,
  append an `<optgroup label=t('weightRig.animationSequences')>` with
  sequences sorted by the same collator. Return the combined ordered list
  (clips, then sequences) so previous/next cycles through both.
- **`ui/animation-playback.js` and `panels/weight-rig-panel.js`:** pass
  `state.sequences`. Re-fill when `state.sequences` changes identity. In
  sequence mode, disable the slider and set the counter to
  `t('weightRig.animationSequenceStep', {step, count})` followed by the
  existing time text.
- **i18n:** add `weightRig.animationSequences` and
  `weightRig.animationSequenceStep` to all six locales (en, es, ja, ko, ru,
  zh-CN).
- `model-flow.js` auto-play still selects `clips[0]` (never a sequence).

## 6. Docs

- **`context.md`:**
  - Genshin section: transitions export, the head/continuation/timing rules,
    and that locomotion is code-driven and not sequenced.
  - Manifest invariant: optional `sequences`, all-or-nothing validation.
  - Playback invariant: sequence mode holds only active + incoming, has no
    seek, and switches morphs at the fade midpoint.
- **`docs/asset-rig.md`:** a short "Sequences" note in the playback section.

## 7. Tests (focused; follow AGENTS.md test design)

- `src/tests/app/assets/test_gi_extraction.py`:
  - A parametrized table for `gi_animation.sequences` on small synthetic
    graphs:
    - a 3-state combo cycle with an Any State head, plus distractor edges
      (a different signature, and a non-subset `Run` edge);
    - an auto chain from a no-incoming state;
    - fixed vs normalized fade, and offset → start;
    - a cycle closing on a non-head (stops);
    - rotation dedupe;
    - an unconverted destination (chain cut);
    - `start >= leave` (dropped);
    - a missing graph (`[]`).
  - Extend `animated_fixture` with a second converted body clip, two plain
    states with combo transitions and an Any State entry, then assert the
    built manifest's `sequences` and the report count in the existing build
    scenario test (no new build test).
- `src/tests/app/assets/test_animations.py`: extend the catalog lifecycle
  test with a valid sequence, plus a parametrized table of invalid sequences.
  Each invalid case keeps the clips and drops all sequences.
- `src/tests/web/test_rig_animation.py`: one scenario test.
  1. Select a 2-step cyclic sequence.
  2. Assert both loads, the start offset and the fade weight at the
     midpoint (blended joint position).
  3. Assert the morph switch at the midpoint, the release of the outgoing
     clip, the wrap with `restart`, and that a stale select during a step
     load is ignored.
  4. Assert `fillRigClipSelect` with an optgroup.

  This is a browser test, run locally only and not added to CI.

## 8. Verification

1. `python tools/build_zzz_extract.py --verify-format`.
2. Read one real controller with the rebuilt helper and print
   `gi_animation.sequences`. Expect the 4-step normal-attack cycle and
   `ExtraAttack -> ExtraAttack_AS`. Keep the output local.
3. `.venv-test\Scripts\python.exe -m pytest -q src/tests/app/assets/test_gi_extraction.py src/tests/app/assets/test_animations.py src/tests/web/test_rig_animation.py`.
4. `python tools/quality_audit.py` before committing.
5. Two verification passes against this plan (AGENTS.md).

## Workspace

This checkout is in use by another session on `claude/gimi-mod-rig-binding`
with uncommitted edits to `rig.py` and `test_gi_extraction.py`. Implement in a
separate worktree on a new branch from `origin/main`
(`claude/gi-animation-sequences`), then open a PR targeting `main`.
