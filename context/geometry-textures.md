# Geometry and texture loading

## Geometry

- Decode conservatively by supported format; reject malformed, unknown or tiny
  buffers safely. New recognition must not erase previously supported geometry
  when it fails; unexplained geometry loss is a regression, so validate against
  representative compatibility cases.
- Static geometry may use bounded NumPy batches that keep exact packed bytes,
  source vertex IDs, triangle order and winding; prepared animations, sparse
  shapes, unsupported layouts and malformed data take the scalar path. Read
  through the shared ModSource/BufferStore and resolver; never weld by position or
  let array views outlive their load-owned bytes.
- Initialize OpenBLAS with one worker before lazy NumPy imports (decoders use no
  BLAS; idle worker stacks inflate memory commitment).
- Keep authored normals; reconstruct only as fallback and never weld for
  smoothing. Normalize winding independently of normal direction. Every consumer
  uses `decode_normals`; large inputs use bounded batches with scalar-equivalent
  Float64 math, canonical Float32 bytes and unchanged whole-stream validity checks.
  Missing animation elements read as zero. Shared strided views validate layout
  only; attribute validation stays in each decoder.
- Deduplicate normalized effective buffer/material state. Classify each new draw
  field explicitly as render identity, visibility or provenance, never
  reflectively. Keep numeric VB slots and the untouched-vs-explicit-null
  distinction; VB changes need no IB change.
- One `DrawCall` representation (with its external mapping contract).
  `build_mesh_result` always owns a binary blob and transport uses offset/length
  references only. Direct `load_mod` publishes geometry; callers passing a blob
  own publication.
- Every generated mesh (direct Assets and Asset Fill included) carries a canonical
  `MeshIdentity` keeping root, type and part provenance. Legacy mesh keys are read
  only at the metadata compatibility boundary and only when ownership is
  unambiguous.
- Geometry comes only from authored `drawindexed`. Literal and immutable-variable
  ranges resolve directly; `drawindexed = auto` uses the full replacement IB of the
  geometry-hash family. An IB binding alone creates no draw.
- Apply shapes only with complete recognized layouts; other slider-like variables
  stay controls and shape targets never leak between position buffers.
- A WWMI sparse key whose value variable has no slider and is written only by
  literal TextureOverride assignments is a derived shape: its writes become
  ordered state rules (runtime-only guards are draw evidence; their else branches
  and mixed guards are skipped) and never a menu, Present or animation control.
  Without `cs-t6` the sparse base is the INI's single file-backed `vb0`. Draws omit
  sparse targets that move none of their vertices.
- Select conditional component buffers only when the draw guard proves the
  binding. Resolve supported IB branches at the authored draw, keeping an
  untouched fallback, direct/null bindings and baked animation. Runtime guards are
  matching evidence, not visibility controls. IB branches carry matching direct VB
  snapshots; an unproven IB stays unresolved. A Blend the guard cannot prove
  follows the guards that bound the draw's rendered Position, then IB, never the
  binder's last-listed alternative.

## Texture identity and roles

- Resource identity is mod-relative, never basename-only. Texture keys are
  `role::mod-relative-path` for `diffuse`, `normal_map`, `normal_data`,
  `light_map`, `material_map`, `emission_map`. Diffuse is sRGB, other roles are
  data. Legacy path-only keys normalize with the caller's known role.
- Candidate discovery offers choices without inferring semantic bindings; texture
  processing is independent of game/material interpretation. Mod loading and
  semantic refresh never decode pixels to infer roles; use explicit mod semantics
  or labelled Asset metadata and leave unknown roles unresolved. Pixel fallback
  belongs only to Asset extraction.
- Original Asset defaults apply where no mod binding applies. An active mod
  diffuse suppresses original Asset maps but keeps mod-authored maps. Authored
  nulls clear the mod assignment, letting labelled Asset defaults through the
  role-rule path, identically for semantic resources and native slots.

## Candidate pools

- One lightweight inventory of all DDS/PNG/JPG/JPEG images through `ModSource`
  (undeclared files, subfolders, compressed mods). Asset images come only from
  exact matched Asset directories, never the whole library.
- Hash evidence, component-local `ps-tN` resources and filenames form one
  canonical source-identity pool per component. Resolve each INI's references and
  replacement index independently before aggregating. Generic filenames use the
  original component name and prefer a unique most-specific match.
- WWMI: `Components-N t=...` names (DDS/PNG/JPEG, multi-ordinal) are the only
  membership evidence; hash substrings and explicit references cannot add another
  component's candidates, and ordinals/hash evidence belong to each exact draw's
  metadata object (siblings never share). Asset Export may assign an owned image
  to a role in that component/range's metadata; only those authored records bypass
  filename membership. Native TextureUsage slots stay candidate evidence; a
  labelled diffuse hash may establish its component's slot for unambiguous
  bindings; conflicting untracked routes stay manual.
- A texture-map (non-diffuse) hash that several of the mod's exact Asset components
  carry (native hashes can collapse across components) cannot attribute a hash
  replacement alone: it then applies only to the component its file name proves
  (existing filename association); a file naming no component keeps plain hash
  behavior. Diffuse hashes keep plain hash behavior.
- Filename associations give choices, never roles. Selection (semantic, slot,
  conditional) runs once per draw in authored order, keeping no-mod-diffuse Asset
  eligibility and game-specific normal transport. Asset defaults pick unique files
  from the same pool by metadata-proven roles and ranges, cached per pool.
- Viewer-generated packages use their explicit metadata-relative files and
  geometry hash; invalid explicit references never fall back to filename guesses.
- The picker consumes the pool; saved/manual choices belong to metadata
  hydration. Published pools omit rejected sources and unavailable auxiliary maps
  without decoding; detached analysis keeps identities when no publisher exists.
  Native manual picks validate before changing pools or selections.

## Texture publication and DDS

- Never eagerly decode/render in pools or backend loading. Publish supported files
  as lazy URLs serving original bytes; never encode PNG copies or embed converted
  data. Detached analysis needs a publisher for image URLs.
- Native DDS uses validated eligibility: BC formats, DX10 RGBA8/BGRA8/BGRX8 UNORM
  and sRGB, legacy 32-bit RGBA/BGRA/RGBX/BGRX; X channels are opaque alpha.
  Model and menu DDS must be structurally valid and at most 8192 per side;
  compressed base dimensions must be multiples of four (smaller mips may be
  partial blocks). No file-byte cap.
- All DDS use one vertical UV transform with upload `flipY` off (r185's
  uncompressed upload flip mishandles mips); keep it stable across lazy load and
  reload. Model samplers repeat authored UV tiles; UV inspection repeats the image
  without changing coordinates.
- Menu DDS previews load only when visible and render through the shared WebGPU
  renderer into canvases; dispose temporary GPU resources and ignore stale work on
  rebuild. Share at most eight completed previews and 16 MiB of RGBA by URL,
  resolution and aspect policy; invalidate changed sources, clear on model
  replacement, and compare replacement identity so same-URL republications never
  show stale images. Preserve WebGPU readback row alignment.
- Borrow ready model textures without disposing them. Release parsed DDS levels
  once Three.js reports upload; a later upload refetches (never reuses released
  levels) and must not override a newer reload. Evict disposed DDS textures.
