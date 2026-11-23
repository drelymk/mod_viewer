# Asset loading, metadata and Asset Fill

How indexed Assets load, save texture choices and fill mods. Creating Assets
from frame dumps is in [asset-capture.md](asset-capture.md).

## Index and previews

- Index Assets from metadata only. Heavy geometry/textures load only for explicit
  indexed-Asset loading, the missing-original-parts action, or the saved opt-in to
  restore missing parts after a successful mod load. Read geometry-object metadata
  once per index build (fresh each build); warn about discarded objects and keep
  valid siblings. Skip folders with a case-insensitive `DISABLED` prefix.
- Direct Assets never synthesize INIs/mods or write `.mod_viewer.json`. Previews
  group meshes by exact hash.json/Metadata.json source. Source-header Remove stages
  the whole owning folder (and descendant sections of legacy root manifests) until
  Export, which renames only preview-authorized folders with `DISABLED_` and
  updates the index atomically. Failed removals stay pending; switching sources
  discards them.
- Asset UVs are viewer-space Float32 with V flipped exactly once. GIMI/ZZMI ranges
  resolve by parsed IB header identity; WWMI `Component N.fmt/.vb/.ib` supplies
  local geometry while metadata offsets stay provenance.
- GIMI/ZZMI geometry dumps need authored hashes, never same-label fallbacks.
  Compact metadata gives exact metadata-relative VB, per-range IB and texture files
  so same-hash payloads stay distinct. Legacy collisions prefer the declaring
  directory/component family; distinct unresolved payloads stay unbound. Parse
  same-hash IB candidates independently; missing counts use the IB header count.
- GIMI/ZZMI missing texture records may use only a unique range-matched IB dump
  family; authored hashes win, ambiguity stays unbound. Immediate-component
  `hash.json` records keep their own provenance and the selected Asset root.
- WWMI metadata components recover independently. Native candidates use
  `Components-N...` ordinals within the exact metadata object; viewer exports also
  keep explicit references and proven roles. Numeric TextureUsage slots never set
  roles.
- Report recoverable part failures as warnings; fail only when no renderable part
  survives.

## Saving texture choices

- Choices stay pending until Asset-only Export updates `texture_hashes` in the
  existing `hash.json`/`Metadata.json` at the loader-proven component and range.
  There is no separate assignment JSON. Store metadata-relative image references
  and explicit role clears; keep captured native hashes for the same file, never
  copy a role's old hash to a new file and never fabricate hashes.
- Only manual diffuse overrides and manually edited roles on independent
  texture-run owners are edits; inherited rendered changes, unavailable textures
  and rendered nulls never author metadata. Preserve unassigned images, geometry,
  image bytes and unrelated metadata.
- Commit all metadata and the rebuilt index under one config transaction,
  restoring exact previous bytes on failure. Only the current backend-issued
  preview session may save, with published/native-picked keys; reject stale
  revisions, role mismatches and images outside the Asset. Mods and archives
  cannot use this path. Block model transitions while a save runs.
- Reopen, Asset Fill and automatic mod defaults read those records lazily,
  including explicit paths without a native hash; a per-role range marker flags
  deliberate selections without weakening WWMI filename membership. Owned
  references may be deeper than the discovery inventory. A successful save updates
  live Automatic defaults and keeps newer pending choices. Ambiguity yields
  candidates, never bindings.

## Asset Fill

- An authored component/range is handled when its component identity is in the
  mod, staged documents included. Geometry overrides and component `handling =
  skip` prevent restoration. Proven texture-only hooks (supported pixel-slot checks
  and the external SkinTexture adapter, also via local command lists and in
  implicit-check INIs) do not claim geometry; use canonical scan evidence, and
  local helper definitions and unknown external calls take precedence.
- Bare hash-only overrides cover every range of that hash; auxiliary buffer/compute
  hashes never establish component identity. Once one Asset is uniquely selected,
  explicit skips also cover its matching ranges alongside auxiliary VB bindings,
  but those hashes cannot select a different Asset.
- Filling needs one unique Asset, keeps component/range provenance and is
  session-only (removed on reload/switch). Asset fill in mods is preview-only;
  direct Asset previews never fill.
- `autoRestoreMissingAssetParts` defaults off; when on, each successful load/reload
  runs the same fill transaction after the mod is displayed, outside the mod
  transition. Guard delayed reads by originating path and fill epoch; stale
  responses roll back without touching the new scene. Automatic restoration shows
  no dialogs or status messages; manual restoration explains errors.

## Legacy GIMI face alignment

- Legacy head-local faces without coordinate-space metadata may use one rigid
  geometry-derived alignment from native full-body Eyes plus a Face/FaceEye anchor.
  Missing standalone Eyes leaves geometry unaligned with a warning (re-extract for
  captured-matrix placement). Transform positions/normals only, keep UVs/winding,
  leave native Eyes untouched, use no character offsets. Selective loads may read
  alignment dependencies without emitting them. Eye-surface refinement may use a
  bounded spatial index but must equal exhaustive search.
