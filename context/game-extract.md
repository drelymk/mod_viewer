# Game-folder extraction (shared rules)

Rules every game backend follows. Per-game proof lives in
[game-extract-wuwa.md](game-extract-wuwa.md),
[game-extract-zzz.md](game-extract-zzz.md) and
[game-extract-gimi.md](game-extract-gimi.md).

## Runtime and trust

- Extraction is an explicit action on an indexed Asset, kept off ordinary load and
  index paths. Native game-picker grants are opaque tokens independent of
  Mod/Asset folder access, carry the game kind and never cross games.
- Pin and verify the native runtime and run it with argument arrays and hidden
  consoles. Helpers are built from pinned parser revisions with locked build
  dependencies and verified runtime file hashes; never download executable code,
  keys or updates at runtime or silently pick a newer parser. WuWa AES keys are
  checksum-pinned at build time and verified with the runtime manifest; a game
  update may need a new viewer build.
- Expose only the narrow commands each backend needs. One mounted provider per job;
  export only into private staging and terminate the helper before staging cleanup
  on completion, failure or cancellation. Monitor the work directory before
  commands, while waiting and on completion; exceeding per-file or cumulative
  output budgets terminates through the failure path (polling may overshoot; it is
  not a quota).
- Never publish private configuration, keys or native logs.

## Settings and backends

- Settings > Game directory stores native-picker folders per tool (`gameFolders` in
  config.json); Settings and extraction share one UI for every registered game,
  driven by `game_extract/backends.py` capabilities. Unregistered games keep folder
  selection but cannot start jobs. Extraction prefills only its Asset's game
  folder, re-validated and re-granted by the backend.
- Adapters own validation, native clients, indexing and package building; the
  shared bridge owns authorization, jobs and atomic installation.

## Proof principles

- Names only discover candidates; proof comes from captured geometry, UVs, positive
  weights, inverse binds, hierarchies and reference transforms. Ambiguity is
  reported, never guessed. The few intentional name-based exceptions are called
  out in each game's file and are preview-only.
- Prove whole native submesh triangle multisets (repeated triangles included).
  Equal positive weights may leave single bones ambiguous: propagate uniquely
  proven targets through the full one-to-one palette, require every mapping forced
  and recheck all influence pairs; never choose between alternatives or
  conflicting aliases.
- Bound intermediate Float64 curve sampling before allocating, independent of clip
  size. Unknown transform paths reject a clip; omitted non-transform channels stay
  explicit in metadata/reports.
- Report skipped items with reasons (`skipped_props`, `skipped_sequences`,
  `*_error` fields); a failed optional stage (props, sequences, facial curves,
  layers) never blocks clips.

## Installation

- Stage all sidecars, then revalidate registry, geometry and sidecar revisions
  under the config transaction. Install Rig.json, Animation and the report
  together with rollback, keeping recovery data if rollback fails. Support
  cancellation before installation. Preserve compatible libraries, explicit rig
  semantics, clip identities and user files.
- Clip filenames reserve case-folded IDs before adding source hashes so distinct
  native names cannot collide on Windows.
- Game indexes are accelerators keyed by runtime/index version plus every archive's
  name, size and mtime; native reads still prove each object. Building never
  publishes partial data.
