# Asset rig sidecars

An optional `Rig.json` belongs at the root of an original Asset, beside its
geometry-object folders. The Asset must already be indexed under an enabled
Asset Folder. The viewer reads the file when Weight/Rig loads, using the exact
Asset/range bindings retained during model loading. Normal model loading does
not read skeleton files.

The sidecar supplies an authored skeleton for manual posing and, with explicit
humanoid semantics, IK. Fitted controls remain the fallback and Physics remains
available. It does not reconstruct the game's physics simulation.

## Game-folder extraction

All indexed Assets expose the same `Extract Rig/Animation` dialog, and each
registered game has the same folder and index controls in Settings. Availability
comes from the backend registry: WWMI extraction and indexing work now; GIMI and
ZZMI show a translated availability message with execution disabled. Their game
folders can already be selected and saved.

`game_extract/backends.py` is the adapter registry; `wwmi.py` implements the
Wuthering Waves adapter using the existing pipeline, native proof, runtime and
index modules. A new adapter supplies folder validation, a client session, index
operations, capture fingerprints and package building. Register it under its
existing game kind to enable the shared controls. The bridge retains folder
grants, cancellation, revision checks and atomic installation for every adapter.
The backend rejects unsupported jobs even when called directly.

For WWMI, the native picker authorizes an installation folder or Paks directory with a
separate opaque grant; it does not authorize Mod or Asset browsing. The
prefilled character name is only a fallback hint: meshes are first found by
matching the Asset's per-component vertex and index counts against the game's
mesh sections, then proven by native geometry.
An individual archive is insufficient for discovering meshes and companion clips.

The bundled Mod Viewer helper calls CUE4Parse directly at the fixed revision
`cb909733773daea9015a67f6955d6f2ec24d3629`, retaining the parser previously used by
the extractor. It has no MCP server or general-purpose tool interface. Its
build uses checksum-pinned sources and locked dependencies, includes licenses
and the .NET runtime, and verifies bundled file hashes before extraction.
One private process mounts the selected installation read-only with
`GAME_WutheringWaves` and bundled WuWa AES keys for the whole job. The build
downloads the keys from a fixed revision, verifies their SHA-256 checksum and
includes them in the runtime manifest. Extraction works offline and requires
no installed .NET SDK. A game update may require a new Mod Viewer release with
updated keys or parser support; there are no runtime downloads or key updates.
No keys, native logs or private tool configuration are forwarded to the browser
or written into an Asset. Process arguments use an argument list, not a shell;
Windows processes run without opening a console window.
See [the helper build instructions](../native/game-extract/README.md) for its
supported operations, dependency pins and upstream limitations.

A name match only discovers candidates. Adoption requires one unique native mesh
whose component ranges, triangle-corner positions, captured UV channels and
positive skin influence pairs match the Asset. Each component-local palette is
resolved unambiguously; every captured vertex participates in the proof. The
native skeleton supplies ordered parents and local reference transforms. A
compatible existing rig retains its explicit humanoid semantics, profile
admissions and fingerprint. New rigs retain the normal fitted-control fallback;
the extractor does not infer control semantics from bone names.

Meshes may have different reference translations and rotations from their
declared animation skeleton. Extraction verifies that source skeleton
independently, requiring the same ordered names, parents and scales and Animation
retargeting mode. Short or empty BoneTree tables retain the native converter's
no-retargeting behavior for trailing bones; explicit other modes are rejected.
Authored local animation keys are preserved; omitted tracks fall back to the
mesh's reference transform after their source-reference keys are checked.
This does not admit different hierarchies or general animation retargeting.

Compatible body sequences are discovered under the verified character's Role
folder and must reference the mesh's skeleton. Facial curves, game events and
runtime cloth simulation are excluded. The supported game layout recognizes
exact `RibbonAnim/Rib_<clip>` companions for CommonAnim clips under the selected
skin. Skeleton, additive/reference metadata and endpoint-inclusive sample spacing
must agree. Complete companion clips start at time zero. A longer layer is trimmed
at the body's endpoint; a shorter layer holds its final pose. Body frame counts,
durations and authored keys stay unchanged, with no time stretching. The exact
source timing and held/trimmed frame counts are recorded in `reference_layer`.
The mask comes from authored layer tracks absent from the body sequence, and the
existing reference-layer converter independently rejects motion already present
in those body tracks. No hardcoded character names or bone ID thresholds are used.

The generated library keeps version 1 JSON/binary tracks. Existing compatible
catalog clips and other Animation-directory files are retained; filename
collisions with user clips receive distinct game clip IDs. Legacy clips can be
upgraded only when their data matches the newly verified body or composed clip.
`RigExtraction.json` records game package identities, import counts and
`skipped_details` with source identities, owning body clips and rejection reasons.
Known companions rejected before conversion count toward `skipped` and
`skipped_layers`; each source is counted once, including metadata/export failures.

Extraction jobs report progress through opaque IDs and support cancellation
before installation. All outputs are staged on the Asset's volume. Immediately
before committing, the backend revalidates the indexed Asset, geometry hashes
and sidecar revisions under the shared config transaction. All three sidecars
are installed with rollback; failed rollback retains recovery data and reports
its location. A successful save reloads that Asset if it remains the current
source. The metadata-only Asset index and original capture files are unchanged.

## Version 1 contract

- `format`: `mod-viewer-rig`.
- `version`: integer `1`.
- `coordinate_space`: `asset`. Transforms use the same units and axes as the
  original captured Asset geometry, before viewport orientation transforms.
- `bones`: an ordered list of `name`, `parent`, `translation` and `rotation`.
  Parent indices must precede their children; roots use `-1`. Translation is
  three numbers and quaternion rotation uses `[x,y,z,w]`. Names are unique.
  Optional three-number `scale` defaults to `[1,1,1]`. Positive uniform reference
  scales are supported; each axis must be between `0.000001` and `1000000`,
  within `0.00001` relative to the first axis. Preserve these scales in parent
  composition and inverse bind transforms. Nonuniform, zero and negative scales
  are rejected. Animation tracks retain reference scale; animated scale is not
  represented by the version 1 translation/quaternion format.
- `components`: original capture identities and verified local palettes. Each
  entry contains `geometry_hash`, `component_ordinal`, `first_index`,
  `index_count`, `vertex_sha256`, `index_sha256` and `bone_map`. Fingerprints
  cover the corresponding original `Component N.vb` and `.ib` bytes. The map
  translates component-local palette indices to entries in `bones`.
- `skin_bindings`: independently verified mod Blend-buffer profiles. Each entry
  contains `sha256`, `encoding` and `bone_map`. Its map translates the raw Blend
  indices to entries in `bones`. Supported encodings are `wwmi_u8_4` and
  `wwmi_u8_8`. The profile hash covers the complete Blend buffer. Changes to that
  buffer require renewed verification. Merely staying inside a palette's
  numeric range is insufficient evidence.
- Optional `wwmi_merged_palette`: reusable stock WWMI layout, as a list of
  `geometry_hash`, `component_ordinal` and `vg_offset` records. Declare every
  captured component for each included geometry hash, including components a
  mod does not draw. Counts and joint mappings come from the corresponding
  verified `components` records. Offsets must form a contiguous palette fitting
  1024 shared entries; ordinary R8 draws address up to 256, while verified proxy
  programs project the larger palette to compact R8 draw palettes. This declaration is established once for the Asset, rather
  than separately for every mod's Blend buffer.
- Optional `humanoid_controls`: all 16 control keys mapped to distinct indices
  in `bones`: `pelvis`, `chest`, `neck`, `head`, and `left`/`right` followed by
  `Shoulder`, `Elbow`, `Hand`, `Hip`, `Knee`, `Foot`. Torso, arm and leg chains
  must follow skeleton ancestry; helper bones between controls are allowed.
  Shoulder means the upper-arm pivot, Hand the wrist, and Foot the ankle.
  Supply independently verified semantics; runtime never guesses from names.
  Game extraction is the one exception: it writes this mapping only when the
  skeleton has the exact `Bip001` biped joint names and the hierarchy check passes,
  and it preserves an existing mapping.

The component maps document original geometry, while the skin binding specifies
the namespace actually used by a mod. For example, a merged skeleton can expose
several components' palettes to every draw. Equal numeric IDs from different
skin sources do not establish joint identity without verified palette provenance.

For generic loading, the parser must recognize the stock WWMI skeleton-merger
template within the mod's own INI, including legacy frame-state and newer
merge-status variants. It checks the native CB inputs, RW outputs,
merged-buffer publication, runtime CB replacement, direct Blend binding and full
component offset/count layout. Direct component draws can have ordinary texture
bindings, toggle guards and the supported RabbitFX pixel helper. Extra vertex
slots do not change palette identity. Local overrides of external helpers,
modified scaling, unknown calls in the draw scope, unproven Blend bindings,
different layouts and custom remappers cannot establish this proof.
The proof is retained with the loaded skinning manifest, without reading INIs
again during Weight access. Every captured component used by the shared palette
must still pass its VB/IB fingerprints, including undrawn components.

Verified merged R8 IDs are already shared palette indices. The decoder preserves
them directly: `vg_offset` places matrices in the merged buffer and is not added
to those vertex IDs. Their source key carries `namespace=wwmi_merged` and a
verified layout domain, separating them from legacy offset-decoded selections and
other skeleton layouts. The lazy preview translates saved
legacy selections once for exact loaded sources; saving persists the new keys.
Unproven older streams keep their existing decoding behavior.

For stock remapped draws, the adapter follows the paired BlendRemapper and
SkeletonRemapper program, including proxy bindings, publication and cleanup.
It reads full R16 vertex-group IDs and checks that the reverse map selects a valid
compact slot and the forward map returns the same shared ID. Original weight
bytes are retained. Supported programs use four/eight influences, the stock
512-entry default or explicit 512/1024-entry maps, and R16/R32 map buffers.
Missing, inconsistent or truncated tables produce explicit skinning errors.
These tables are read only during Weight access through the shared resource
sandbox. A verified proxy bypasses raw-R8 fingerprint profiles and uses the full
Asset palette declaration.

The backend adds source-scoped
joint aliases and one shared normalized skeleton to the lazy skinning response.
The frontend adopts the authored graph only when all participating sources have
complete compatible bindings. Otherwise it retains the existing inferred graph.
The version is limited to 4096 bones and a 2 MiB sidecar.

## Conversion

The converter accepts the verified source-skeleton/component binding produced by
the extraction comparison. Its `skeleton.referenceSkeleton` carries
`FinalRefBoneInfo` and `FinalRefBonePose`; its component records carry the original
capture range, geometry fingerprints and `boneMap` correspondences. This proof
data must be established before conversion; the tool does not guess mappings
from bone numbers, proximity or character names.

```powershell
python tools/convert_asset_rig.py verified-binding.json Rig.json --skin-bindings verified-skin-bindings.json
```

The optional skin-bindings input is a JSON list with the profile fields above.
Without a skin profile or reusable merged-palette declaration, the sidecar stores
the reference skeleton but cannot authorize a mod binding. Keep game files, real model sidecars and corpus-specific profiles outside
the repository. Use synthetic data for committed regression tests.

Add `--wwmi-merged-palette verified-layout.json` for the reusable layout above.
Compatible stock WWMI mods then use the same Asset rig automatically, even when
their vertex counts, weights, textures or draw subsets differ. No per-mod
fingerprint or sidecar modification is needed. Other Assets still need their own
verified skeleton and palette. Explicit skin profiles remain available for
independently verified pipelines outside the stock adapter.

Add `--humanoid-controls verified-controls.json` to include the optional semantic
mapping above. These controls use the game skeleton's exact rest positions and
segment lengths. Direct mapped joints establish deformation boundaries; helper
and twist bones inherit through the authored hierarchy. Saved control edits still
take precedence. IK clamps unreachable targets, retains its preferred bend after
full extension, and preserves rest roll when a limb crosses the forward axis.
Missing semantics retain fitted controls. Degenerate control segments also fall
back to fitting without discarding an otherwise usable manual skeleton.

After installing the sidecar, reload the mod and reopen Weight/Rig. The Selected
Joint list displays the authored bone names. Reset Pose restores the original
baseline geometry. Missing files, changed reference fingerprints, malformed
transforms, disabled Asset roots, unproven mod palettes and unsupported skin
namespaces leave normal Weight inspection and inferred posing available.

Saved Main Rig joint mappings include the authored skeleton fingerprint. Moving
between authored and inferred graphs discards incompatible numeric joint IDs
while keeping valid semantic control positions eligible for rebinding.
Authored control mappings from builder 4 remain eligible when their skeleton
fingerprint matches; older inferred numeric mappings rebind from saved semantic
positions. Authored pose presets can resolve unambiguous legacy source signatures.
The fingerprint covers the normalized skeleton and humanoid semantics, excluding
the component/profile/layout admission lists. Adding another verified mod profile
does not invalidate existing joint mappings for the same skeleton. Each distinct
Blend buffer outside the supported generic path still needs its own verified
`skin_bindings` entry; a matching character or geometry hash alone does not
establish its palette namespace.

## Direct Asset weights and animation

Direct WWMI Asset previews retain captured BLENDINDICES/BLENDWEIGHTS layouts
and the exact compact vertex order. Weight/Rig reads these streams lazily and
verifies the matching component palette against `Rig.json` and its VB/IB hashes.
Asset posing is temporary; selection and rig-preset saving remain mod features.

Put an optional `Animation/manifest.json` inside the Asset:

```json
{
  "format": "mod-viewer-animations",
  "version": 1,
  "skeleton_id": "<normalized Rig.json fingerprint>",
  "clips": [
    {"id": "clip01", "name": "Clip 01", "file": "Clip01.json", "duration": 1.0}
  ]
}
```

Each clip JSON uses `format: "mod-viewer-animation"`, `version: 1`,
`coordinate_space: "asset"`, `layout: "frame-major-tq-f32le"`, the same
`skeleton_id`, `frame_count`, `bone_count`, `duration`, `data_file`, and the
binary's `sha256`. Binary records contain seven little-endian float32 values:
local translation XYZ followed by normalized quaternion XYZW, in frame-major
then Rig.json bone order. Reference scales come from the rig. Playback interpolates
translation and rotation across endpoint-inclusive samples over the source duration.
No curve, morph, event, or game physics tracks are included.

Opening Weight/Rig loads weights and the animation catalog first, then builds
the rig automatically. Animation controls become available when activation
finishes; no separate Load Rig action is needed. Clip data loads only after selection,
through contained Asset paths and opaque session/clip IDs. Stale sessions,
different skeletons, invalid transforms, truncated data and checksum failures
cannot publish clips. Geometry uses the existing localhost blob transport.
The Animation section offers selection, Play/Pause, Stop / Rest pose and scrubbing.
Playback loops and temporarily suspends Weight physics. Stop restores the exact
baseline; manual posing or rig editing stops animation first. Compatible mods
automatically use the same animation library as their verified Asset rig.

For CUE4Parse ActorX samples with a checked sample manifest:

```powershell
python tools/convert_asset_animations.py Rig.json extracted-samples Animation
```

The sample manifest lists each clip's `name`, `duration_seconds` and
`psa_sha256`. Conversion verifies bone names, parents, reference transforms,
complete keys, timing and finite near-unit rotations. It reverses CUE4Parse's
ActorX key axis/root-quaternion conversion, normalizes rotations and preserves
source duration. Other PSA writers may use different coordinate conventions;
this converter targets that exporter explicitly.

### Authored secondary layers

Body sequences may leave hair and clothing at their reference transforms while
separate additive sequences contain the secondary motion. These tracks must be
included during conversion; enabling viewer physics does not recover them.

The converter accepts an optional explicit layer manifest:

```powershell
python tools/convert_asset_animations.py Rig.json extracted-samples Animation --layers secondary/layers.json
```

```json
{
  "layers": [
    {
      "clip": "Clip01",
      "file": "Layer01.psa",
      "metadata_file": "Layer01.metadata.json",
      "sha256": "<source PSA checksum>",
      "bone_ids": [4, 5, 6]
    }
  ]
}
```

Layer files must stay inside the layer-manifest directory. The original exporter
metadata must declare `AAT_LocalSpaceBase`, `ABPT_RefPose`, matching timing and
the selected bone IDs in `TrackToSkeletonMapTable`. The layer's reference
skeleton must independently pass the same names, parents and transform checks.
[CUE4Parse expands these additive clips onto their reference pose](https://github.com/FabianFG/CUE4Parse/blob/master/CUE4Parse-Conversion/Legacy/AnimConverter.cs),
so the converter copies their absolute local transforms only into explicitly
selected bones that remain at their reference pose throughout the body clip.
It refuses to overwrite existing base motion. All other tracks stay byte-exact;
frame counts and source durations must match. Do not pair clips by similar names
alone or stretch a secondary clip to fit a different body sequence.

The resulting clip keeps the ordinary viewer format and records the selected
layer checksum and bone mask in optional `reference_layer` metadata. Playback
needs no special character logic. This includes authored secondary motion;
facial curves and runtime cloth/collision simulation remain outside this format.
