# Asset capture extraction (Add/Update Asset)

Building Assets from 3DMigoto frame dumps and Manager captures. Exact texture
family signatures live in the classifier code and its tests; this file keeps the
rules they must follow.

## Transaction and output layout

- Extract only native-picker-authorized dumps into registered Asset Folders. No
  game-marker/type gate; unfamiliar layouts fail explicitly. Stage compact output,
  swap whole directories and rebuild the index under the config transaction;
  failures restore the previous Asset and index.
- A partial capture is a committed success, never an error or a completeness
  claim: ordinary success is silent, partial shows a nonblocking warning, failures
  stay inline in the open dialog. The Add/Update dialog has no component selector.
- Keep the provenance report inside the Asset with portable relative paths,
  replaced and rolled back with it; omit the capture root and redact local roots
  from persisted diagnostics (API destinations stay absolute).
- Windows: published directories and reports inherit the destination parent's ACL,
  not temp-directory ACLs. Skip ACL resets only when the volume has no persistent
  ACL support; query, permission or apply failures abort and keep the prior Asset.
- Viewer exports carry the `mod-viewer` marker, which never identifies a game.
  ZZMI/GIMI keep `hash.json`; WWMI uses native `Metadata.json`, `TextureUsage.json`
  and `Component N.fmt/.vb/.ib`.
- All games plan folders by position-buffer hash and make each object
  self-contained: its own metadata, geometry and texture copies, no parent/sibling
  references, cross-object hardlinks, root hash manifest or root textures. The
  parent indexes as one Asset. GIMI/ZZMI objects declare the `geometry-objects`
  layout and position hash and keep IB identity, draw ranges and distinct payloads;
  legacy flat/nested layouts stay readable. Buffer grouping assigns no Body/Face/
  Weapon semantics and excludes no supported geometry. Users may delete or disable
  object folders; siblings must still load.
- WWMI: each ATTRIBUTE draw range is its own component, globally numbered
  `Component001`... (component_name equals mesh label; no per-object restart or A/B
  parts; explicit internal names replace the prefix but keep numbering). Store only
  referenced vertices with local indices, all channels and 4/8 influences; keep
  exact source range, base and resource identity in provenance. `vb0_hash`
  identifies the position buffer separately from the captured IB hash. Geometry
  files stay independent; only textures share bytes within an object. Keep native
  winding on export and apply the viewer convention once on load. GIMI/ZZMI keep IB
  precedence regardless of the format marker.

## Capture inputs and budgets

- Directory and memory captures share source-size, mapped-capacity/count and
  cumulative decoded-work budgets, checked before mapping or allocation.
- Stream-output: 40-byte position/normal/tangent records and declared 24-byte
  position/normal records with matching blend/UV counts. Blend discovery uses the
  validated position stride; among byte-ratio candidates prefer a unique stream
  declaring BLENDINDICES in its own slot, else legacy size-only discovery; several
  declared candidates stay ambiguous. Never fabricate tangents or apply the 40-byte
  adapter to another layout.
- Direct unskinned meshes use declared position/normal and UV streams with
  matching complete payloads; UV draw-subset counts do not split identities;
  malformed headers stay rejected.
- Require a supported primary TEXCOORD0 with two or more channels (secondary
  channels never substitute; a primary in the position stream is valid). Missing
  textures alone never reject geometry. Nonfinite secondary TEXCOORD channels are
  dropped whole with diagnostics; nonfinite primary UVs or geometry reject. Never
  fabricate values or hide conflicting declarations.
- Split skinned ATTRIBUTE captures need validated position, packed
  tangent/normal, UV and 4/8-influence blend layouts with matching full counts.
  Aliases must share the four-influence stream's slot, formats and offsets. Draw
  subset counts do not split complete resources. Ignore shader-only deformation
  streams.

## Candidate selection

- Decodability does not prove membership: direct geometry is excluded unless
  selected, except GIMI direct facial candidates, all kept for manual cleanup
  (missing-material, closed-eye and overlay shells included). Never rank open-eye
  anchors, suppress facial passes or split away other eye overlays; candidates
  claim no character identity.
- Passes rebinding the same position/UV resource versions, payloads and range are
  one candidate keeping every draw's provenance, even if auxiliary declarations
  (TANGENT) differ; POSITION/NORMAL layout differences stay separate.
- A GIMI direct face whose TEXCOORD2.x > 0.05 vertices form a vertex-disjoint
  contiguous triangle run (all other TEXCOORD2 exactly zero) holds the alternate
  shell the face shader collapses: split it into its own excluded-by-default
  candidate in the same family and folder. Mixed or non-contiguous shells stay
  intact.
- Matching diffuse/light payloads group captured-matrix placement evidence only;
  others validate independently. Unresolved placement stays visible at captured
  coordinates and marks the extraction partial. Unproven non-GIMI candidates keep
  their report counts and explicit selection. Keep captured geometry, omit
  fabricated blend data, and never fall back through an unsupported shader writer
  or fabricate a neutral expression.
- Face-outline signatures (tiny R16 in both depth slots with full-size BC6H
  inputs) are excluded only after exact triangle-position subset proof against a
  supported color component, keeping their ranges for explicit selection.

## Texture roles

- Deduplication keeps every captured pixel-texture binding per source draw in the
  report (slot, shader, native hash, resource version, payload). Export/picker
  membership keeps the chosen pass's images even with unknown roles; other passes
  of the same exact range add candidates only when their uncontaminated snapshot
  hash matches the bound resource, staying unclassified with separate provenance.
  Unverified snapshots are report/debug-only.
- Pass choice: recognized material passes outrank effects and inherited passes
  with more resources; bound-resource count is not evidence, and unresolved ties
  use the first captured color pass.
- Diffuse/material-family recognition (GIMI, ZZMI, WWMI) needs capture evidence, a
  color pass, bound shader/resource identities and the complete family's
  format/size/shared-resource signature (e.g. GIMI material triplets, layered eye
  families, WWMI hair/face/eye/unlit families). A complete family outranks simpler
  or inherited ones; partial, distinct or contaminated members cannot prove it.
  Classify with the full binding set while legacy triplet ranking keeps its
  first-three-slot scope; replaced ramp snapshots prove nothing. Never infer GIMI
  MaterialMap, never relabel sRGB eye layers as LightMap/MaterialMap, never infer
  other WWMI roles or drop unknown candidates; ambiguous or non-primary masks stay
  report suggestions.
- Pixel fallback runs only in extraction, on unclassified, uncontaminated,
  non-auxiliary color/data encodings (float, HDR and typeless never qualify);
  confirmed roles win. Classify RGB independently of alpha (alpha may be data) and
  cache features per payload. Unique strong color evidence or normal-vector pixels
  with matching diffuse layout may assign a role; GIMI may tell NormalMap XY from
  packed LightMap via active pixels and the primary family (atlas padding and B
  detail are not normal Z). Unclassified GIMI textures stay selectable but unbound.
  Only real auxiliary/unusable evidence excludes resources from normal output.

## Texture files

- Copies keep original bytes, channels, encoding and the metadata's exact
  filename and proven role.
- GIMI/ZZMI compact names keep asset, component, role and native hash. A shared
  hash/payload pair is one file named for its first owner; each consumer keeps
  its own role and reference; distinct hashes or payloads stay separate. Extra
  sharing may use output-only hardlinks, always within one object folder.
- WWMI names are `Components-0-1 t=hash.ext` listing exactly the referencing
  ordinals; one file per hash/payload per object, with an object-local content
  suffix for same-hash collisions.
- Hashless snapshots recover identity only from that exact snapshot's logged dedup
  alias; explicit filename hashes win; never inherit an earlier slot's hash or
  search sibling captures. Keep export identity validation strict.

## GIMI face placement

- New extraction places faces only from captured object matrices and a verified
  native skinning palette: inverse(head skinning) x inverse(body world) x face
  world. Require the supported head-local axis convention and one consistent
  transform across matching bones, related native objects and every facial draw;
  never hardcode bone IDs, CB slots or offsets, or infer placement from eyes.
- Validate object/inverse blocks and replay every written SO vertex from source
  positions, weights and palette first (SO allocation tails are capacity, not
  vertices, and need not be stride-aligned). Only candidates contributing matches
  are native evidence dependencies; validate shared sources once per extraction
  with no cross-request cache. Missing optional snapshots never discard later valid
  draws. Record exact resource versions, snapshot hashes and replay evidence;
  reject changed evidence.
- Families with consistent verified placement need no eye-hole topology and keep
  companion parts; eye topology is the fallback without placement.
- Bake proven placement into exported facial positions, normals and float tangents
  once (UVs/indices unchanged) and copy the captured position bytes under the
  object's `source_geometry`, referenced from placement metadata. Native skinned
  facial geometry is already in body rest space and stays unchanged. Every facial
  candidate uses an ordinary position-hash folder (no Face/ container, no renamed
  hashes).
- Metadata records coordinate space and placement status. New exports never use
  loader fitting; missing/conflicting placement keeps captured coordinates, marks
  partial and warns on load.

## Manager captures

- `src/extract_asset.py` is the local headless entry for Manager's completed
  shared-memory captures. Explicit native Manager Asset Folder configuration
  authorizes root registration; browser APIs keep the native-picker boundary.
  Validate named-section capabilities, containment, identities and sizes; open
  sections read-only and never stage raw dumps. Match member paths and aliases
  case-insensitively, keeping stored spelling and rejecting case collisions.
- Directory and memory sources share inventory, analysis, reconstruction, export
  and the Asset replacement/index transaction. Same-name updates need an indexed
  Asset; unrelated folders stay untouched; only the final Asset and its staging
  touch the filesystem.
- Viewer and headless workers also share the Windows per-config process mutex,
  held across registry/index commits and rollback; never kill a worker inside it.
