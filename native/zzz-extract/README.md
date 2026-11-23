# ZZZ extraction reader

This helper implements the existing extraction job's private file/JSON protocol.
The shared Python client owns cancellation, process lifetime, request/response
limits and temporary output budgets. The bridge owns native picker grants,
revision checks and atomic installation with rollback. Extraction reads the
installed game offline; it never downloads keys, executables or updates.

Build with .NET SDK 10.0.100 or a compatible newer stable SDK and Windows x64 MinGW g++:

```text
python tools/build_zzz_extract.py --verify-format
```

The build pins original AnimeStudio commit
`76234e9f6f5a839504f41f9612719529fe2309a5` and verifies its source archive's
SHA-256. It compiles its managed Unity reader, the ZZZ V2 ACL decoder and the
Genshin Impact database ACL decoder (`AnimeStudio.ACL.DB`) from source.
SDK discovery is shared with the WuWa helper: PATH or an explicit `--dotnet`
override, with a prerequisite warning when the required SDK is missing. See the
[build prerequisites](../game-extract/README.md).
The decoder distributed with that revision crashes on supported ZZZ tracks;
rebuilding its unchanged native source avoids that stale binary. No Ultimate
fork, GUI, FBX exporter, YAML clip exporter, image/audio decoder or updater is
included. The managed reader retains its internal YAML types because native
object classes implement those interfaces; the helper never exports YAML.
Oodle uses the same pinned decoder archive as WuWa. Transitive managed packages
are locked in `locks/`; dependency changes require `--update-locks` and renewed
native geometry/animation verification. The helper bundles .NET 10.0.13.

Build-time adaptations remove unused image and unrelated game helpers, make
parser failures fatal, preserve duplicate internal CAB scopes, restrict external
resource reads to the current bundle, and permit exact bundle-offset reads.
Prop materials and textures are read without decoding: renderer reads list each
material's texture slots and pointers, and Texture2D reads return the stored image
bytes for the caller to wrap. ZZZ scans may also list every serialized file holding
a Material or Texture2D so those pointers can be located.
Renderer reads also report the prefab's default visibility (renderer enabled and every
GameObject up to the Animator active) and, for ZZZ, that Animator's GameObject name and
controller pointer; an Animator layout the reader cannot parse leaves the controller unknown.
Avatar reads stop after the skeleton, reference pose and path-hash table: optional
HumanDescription authoring metadata is unnecessary for skinning and varies across
native asset layouts. Mesh shapes filter unrelated outfit families before opening
their Avatars; shape-compatible candidates still require complete native proof.
Both legacy exporter mesh names and short Unity mesh names are discovery hints.
Geometry, path hashes and inverse binds establish identity. Equivalent Avatar
copies in one animation family use the existing reference-world tolerance;
differences in the skeleton or animation ownership remain rejected. Generated
clip paths are unique with Windows case-insensitive semantics.
ZZZ also indexes and reads AnimatorControllers with the same exported graph as Genshin (used only for authored sequences).
Locators include game-relative block path, bundle offset, internal file ordinal,
CAB and PathID. Never resolve an object by CAB basename or PathID alone.

## Genshin Impact mode

`--gi <game folder> --work <directory>` reads Genshin Impact `Blb` blocks with
the same pinned reader and protocol. Block paths must name a file one directory
below `GenshinImpact_Data/StreamingAssets/AssetBundles/blocks/` or
`GenshinImpact_Data/Persistent/AssetBundles/blocks/`; choosing between a
`Persistent` copy and its `StreamingAssets` original is the Python adapter's job.
Scans list meshes (with section shapes), Avatars, animation clips,
AnimatorControllers and SkinnedMeshRenderer routes with complete locators. The
GI index keeps only a renderer's path and explicit mesh/Avatar pointers (external
pointers spell CAB names in lower case); null-mesh renderers stay unrouted. Genshin
scan rows also name each renderer's material files, and file scans add one `Material`
row per material naming its texture files (null when the material cannot be read, so
unused materials never fail a scan); the Python index keeps only the file locations
character renderers lead to. Reads export:

- meshes whose skin covers every vertex (packed or partial skins are rejected);
- the Avatar skeleton with `m_TOS` paths and the model's default pose, which is
  the pose Genshin meshes are bound in (the Avatar skeleton pose is Humanoid
  normalized and does not prove inverse binds), plus its Humanoid definition (human
  skeleton, ZYRoll axes and limits, bone/hand indices, masses, scale, twist
  settings, human-to-Avatar node map and root). Other axes types are rejected;
- clips decoded by the rebuilt database ACL decoder, in the ZZZ clip shape plus
  the muscle clip's mirror flag and the blend-shape clamp policy. The decoder
  reports failure only through empty or inconsistent output, so frame/curve
  counts and finite values are checked;
- controller layers (names, motion sets, blend modes, weights, skeleton masks)
  and Base Layer states with each motion set's blend-tree leaves as
  `(cab, path_id)` clip pointers. Pairing and conversion happen in Python.
- AnimatorController reads also export the base state machine's authored graph:
  per-state loop flag, tag, path and transitions, Any State transitions and entry/exit
  selectors. Each transition carries its destination (a state index, or 30000 +
  selector index), duration, offset, exit time, has-exit-time and fixed-duration
  flags and named conditions (mode, threshold). Arrays are bounded.
- SkinnedMeshRenderer reads list the renderer's material slots, as in ZZZ, plus
  `visible`: whether the prefab draws it by default (the renderer's enabled flag,
  kept by a build-time reader adaptation, and the active flag of its GameObject and
  every ancestor up to the Animator's, each GameObject consumed exactly; Genshin omits
  its trailing alignment). Null when that layout is not proven. Material and Texture2D
  reads match ZZZ's.

GCC rejects one uninstantiated ACL template body that MSVC accepts, so the
database decoder is built with `-Wno-template-body`; its source is unmodified.

The index caches metadata only. Captured triangles, positions, UVs and positive
float weights prove mesh correspondence and an unambiguous palette. Avatar path
hashes and all used inverse binds prove the hierarchy and mesh-to-Avatar basis.
Avatar constants may include or omit the root-motion flag. Accept only one
bounded layout with valid UTF-8 paths, matching CRCs and every skeleton hash;
duplicate, truncated or ambiguous tables fail closed. Optional authoring data
does not participate in extraction.
A synthetic root preserves that basis without rewriting captured coordinates.
Version 2 sidecars retain sparse palettes, nonuniform reference scale and sampled
TQS tracks. Unknown transform bindings reject a clip; unsupported non-transform
channels are counted explicitly in clip metadata and the extraction report.
Facial extraction retains single-frame blend shapes and their authored full
weights. Renderer paths and Animator Avatar references come from explicit Unity
pointers. Streamed null-mesh renderers require a geometry-proven mesh with
the same complete ordered bone paths and any nonempty default-weight count; names cannot establish a
route. Several such proven copies (for example a `SeparateMesh_` export) share the
route only when their facial targets on the captured parts agree within float
export noise of one representative. Complete triangle multisets preserve duplicate counts during geometry proof.
Each converted facial clip includes a playable GLB with a checked sparse runtime
view. The viewer loads that GLB lazily with the clip, applies facial deltas before
skinning, and restores the original geometry when playback ends. Unsupported
in-between targets remain omitted. Curve sampling has an independent memory budget.
On the first clip read, the helper reads only the contained game's PlayerSettings
blend-shape clamp policy from `globalgamemanagers`. Extraction records and honors
that policy instead of assuming signed or clamped playback. Engines before Unity
2018.3 (Genshin's 2017.4) have no such setting and always clamp, which the
serialized engine version establishes. Unknown policy is not guessed. Clip-only
runtime revisions keep the separate metadata-index version, so this conversion
correction does not require rebuilding a ready format-3 index.
Unchanged mod Blend streams may use an exact verified fingerprint. Other weight
layouts keep the existing inferred rig until independently proven.

Generated binaries and licenses live in the ignored assets directory. Runtime
verification hashes every manifest entry before starting a job. `--self-test`
checks the bundled native ABI; synthetic Python and browser contracts exercise
binding, conversion, saved-file validation and scale-aware deformation. Real
game files are never committed as fixtures.

The app distribution shares identical helper files in `assets/extractors.zip`;
see the [portable packaging contract](../game-extract/README.md). Unpacking is lazy,
verified and confined to a private temporary directory, including on read-only
application installations. Source native development keeps the normal publish
directory.
