# Lossless staged editing

## Edit session and Export

- `core.ini.parser` is read-only analysis. Every INI edit goes through
  `IniDocument` and the shared `app.session.edit` session. Untouched round trips
  keep BOM, mixed line terminators and a missing final newline byte-for-byte.
- Report ambiguous/malformed structure through `structure_errors()` instead of
  guessing rewrites. Single-section accessors pick the first duplicate; anything
  needing all duplicates iterates the document.
- Reload, diagnostics, Toggle CRUD and Record consume staged documents; reopening
  the current mod never overwrites them from disk. `peek()` exposes live staged
  state; edits use begin/commit/rollback, atomic across INIs.
- INI and index-buffer edits share the staged session and reload through its text
  and byte overrides. INIs are written only at Export: each dirty document once,
  with a timestamped `.BAK` excluded from discovery; failures stay pending. A
  confirmed mod switch or restart discards the session (no standalone Discard).
  Viewer metadata and confirmed texture saves are separate writes.
- Stage dependent INI and IB changes atomically. Export revalidates the IB source
  hash, writes a collision-safe sibling backup, verifies an equal-length temporary
  file and replaces the buffer before its INIs. A failed IB blocks its INIs; a
  committed IB stays tracked until all dependent INIs export, so retry never
  applies the byte edit twice.

## GLB snapshot export

- Captures detached visible geometry and current bindings without committing or
  clearing staged edits; destination only from the native save picker, atomically
  replaced. Archives and direct Assets keep their export restrictions.
- Weights: lazily reuse authored Weight data without activating Rig/Physics; keep
  exact Blend-source identity, compact vertex mapping and all eight influences;
  merge duplicate IDs and normalize only the detached copy. Neutral joints carry
  numbered Blender groups and claim no game hierarchy or bind pose; zero-weight
  geometry gets an explicit Unweighted placeholder. Fail if advertised weights
  cannot load. Game rigs, animation and shader reconstruction are deferred.
- Export only loader-supported attributes (never Weight heatmap colors) and only
  Blender-ready images, keeping bound role keys as metadata. Cache image loads
  (success and failure) per source URL for one export and release DDS canvases
  after the writer copies them. Packing stays byte-equivalent to the pinned
  exporter without whole-file Blob reads; base64 uses bounded byte arrays.
- Transfer in bounded chunks tied to one opaque native-picked save session.
  Cancel/errors remove the temporary file; Finish validates full length and the
  GLB document before atomic replacement. Browser paths are ignored.

## Toggles, menus and Record

- Namespaced globals are cross-INI and read-only; Toggle CRUD targets only plain
  variables declared in that INI. Existing unwired utility keys stay hidden; newly
  added unwired keys show only while session-pending and block Export.
- Toggle identity is its `[Key...]` section plus its complete variable tuple,
  never one variable; duplicate tuples resolve to their last cycle position.
  `[KeyModViewerPresent]` is one aligned mod-wide cycle with atomic fan-out;
  incomplete/misaligned state stays visible but unapplied.
- Deleting a toggle removes its local variables throughout that INI (declarations,
  resets, assignments in other keys, PRESENT captures). Other keys keep their
  remaining tuples; remove keys left with only binding metadata. Open affected
  branches keeping unrelated predicates and source order; deduplicate newly
  co-scoped draws only with no intervening state-changing commands or gates.
- MENU TOGGLE deletion is one atomic mod-wide transaction: rediscover clickable
  controls from current documents, remove their variables, exclusive UI helpers
  and image references, flatten model gates, keep shape sliders and shared
  consumers. Never delete image files or write INIs before Export.
- Deletion removes headers it empties (including no-op calls to helpers it
  empties) but keeps pre-existing empty placeholders, comments, surviving line
  endings and at least one blank line between sections.
- Record regenerates only provably safe single-variable if/elif chains; refuse
  ambiguous or partially observed cases, reparse the result and roll back on
  failed verification. Toggle controls use assignable `.onclick` so Record can
  replace and restore exactly one handler.

## Mesh edits

- Persisting separated parts needs a writable authored draw with one canonical
  identity and exact provenance, and a complete, non-overlapping partition of
  every authored triangle in a 16/32-bit IB. Reject synthetic/stale draws, partial
  overlap with another draw, duplicate submissions and cross-component requests.
  Reorder only that draw's raw triangle records and rewrite every authored
  `drawindexed` source in one transaction.
- `drawindexed = auto` edits need an exact literal replacement IB with explicit
  R16_UINT/R32_UINT format and complete triangles. A shared auto with one direct
  authored source may migrate into its complete same-INI/hash family: append
  edited ranges and sibling autos at each component's end, keeping the shared
  draw's phase and full if/elif/else path and existing commands in place.
  Execution order, owner commands, helper calls, metadata and conditional or
  repeated IB bindings do not gate migration (repeated bindings use the final
  applicable IB). Reject components sharing a buffer, ambiguous sources,
  incomplete families and overlapping buffer edits. Identities come from the
  canonical draw scan and resource analysis; the editor never interprets command
  lists. Local auto edits replace the exact instruction in place. Loading never
  migrates INIs.

## Import Mod (GIMI variant consolidation)

- Explicit and session-only. Converted INIs and buffers belong to the edit session
  and survive reloads; never write source files during import, and block mod Export
  for imported sessions until converted-buffer export exists.
- Positions, UVs, colors, weights and tangent handedness stay byte-exact. Only
  finite near-unit normal/tangent export noise within 1e-6 of a fixed
  representative is canonicalized; never chain tolerance matches or cross
  normal-decoder fallbacks. Bound approximate work and fall back to exact sharing.
- Align streams by canonical resource roles and ordered binding implication
  (including shared/unconditional streams and reordered selectors). Keep variant
  guards and material state, disabled components disabled and helper-bearing
  selector chains unchanged; update only proven Blend processing draws; open only
  pure disjoint equality selectors with compatible layouts.
- Mixed outfits partition into complete zero-tolerance loose parts, keeping each
  part's triangle order, winding and multiplicity. Share ranges only when complete
  consolidated records match; a position-only match never proves render
  equivalence or authorizes welding. MESHES lists imported meshes directly under
  their Component; loose parts stay internal, with no numbered Part groups or
  per-part helper identities, and comparison uses whole parts, not draw fragments. Keep whole draws when splitting
  would change normal reconstruction or exceed the triangle-work/draw budgets.
