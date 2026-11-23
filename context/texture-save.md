# Texture color preview and Save to Texture

- Color adjustments are per-mesh diffuse previews stored by metadata identity in
  `.mod_viewer.json` (unrelated data preserved, neutral entries omitted); disabled
  for Asset/no-diffuse meshes. Reset Color clears only the preview.
- CPU save and GPU preview share normalization and order: optional target tint,
  then hue, saturation, brightness (0-4, nonlinear slider centred at 100%),
  contrast and RGB channels in editor-sRGB, alpha preserved. Convert only at
  shader boundaries (picker hex is already sRGB). Update stable material nodes
  without recreating textures.
- Save to Texture has its own confirmation and immediately writes one mod-owned
  BC7 UNORM/sRGB DDS, independent of INI Export. Include hidden changed meshes
  sharing it, flush preview metadata first, renew review when targets change and
  block dismissal/duplicate submission while saving.
- The backend authorizes the mod, validates canonical texture/mesh/metadata IDs
  and the full role snapshot, and derives UV coverage from authored geometry;
  browser paths/UVs are never authority. Reject Asset/stale/non-BC7 sources,
  unknown coverage, conflicting overlaps, and any DDS also used in an auxiliary
  role, inactive variants included.
- Recolor only covered BC7 blocks, preserving layout, headers, alpha and other
  blocks. One intent pads across valid pixels of a partial block; multiple intents
  keep per-pixel targets; weighted intent propagates through mips; keep the source
  block when refitting worsens RGB error.
- Validate temporary layout and source hashes, write a collision-safe timestamped
  sibling backup, then replace atomically; abort stale writes. Once replaced the
  save is committed even if cleanup fails; never invite a second application.
- After commit clear only saved preview metadata and reload affected keys in
  place; async completion rechecks current mod/target first. Metadata reset or
  refresh failure stays a committed save with a visible warning and backup-based
  recovery (Reset Color is not recovery).
