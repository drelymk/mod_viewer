# Asset extraction

While an Asset is open, assign textures in the mesh panel, then click **Export**
or **Export → Save Asset Textures** to save the current assignments. Export
enables when an assignment changes; the Edited indicator clears after a successful
save. Each Asset component has one active texture per role. Assigning another
texture replaces that role for every mesh in the component; clearing it sets
only that role to None. Automatic uses the latest assignment too.
Export saves only changed component roles, preserving unrelated roles and
components, including unavailable textures. Model switching waits until saving
finishes. Export permanently updates the texture records
in the Asset's existing `hash.json` or `Metadata.json` and refreshes its index.
Those records are used when the Asset is reopened and when textures are assigned
automatically to matching mods.
No separate assignment JSON is created. Choose images from
inside the open Asset folder. This action is available only for an open Asset;
mod Export keeps its existing behavior.

Right-click an Asset Folder and choose **Add asset**.
Enter a name, choose the frame-dump folder with Browse, and click OK. The new
asset is indexed automatically. Right-click an indexed asset and choose
**Update asset** to replace all of its files; its name is prefilled and editable.
Extraction and indexing finish before the old files are removed. Failed updates
restore the previous asset and index.
The installed asset keeps `extraction_report.json` with source provenance and
the final asset location; successful updates and renames replace that report
along with the geometry.

Extraction is available for all registered Asset Folder types. It reads captured
resource history and validated stream layouts without requiring a game marker.
All three games use position-buffer hash folders with object-local geometry,
textures and metadata. GIMI/ZZMI use `hash.json`; WWMI retains native metadata
and binary component formats. The viewer indexes the parent as one
Asset. Components using the same position-buffer hash stay together; IB hashes
and draw ranges retain their original identities. Shared textures are copied
within each object, without references or hardlinks across object folders.
These are geometry groups, not automatic Body/Face/Weapon labels; all supported
parts remain included. Existing flat and nested facial Assets remain readable.
The engine reconstructs supported stream-output skinning layouts and declared
direct position/normal/UV streams. Stream-output inputs support the existing
40-byte position/normal/tangent layout and a declared 24-byte position/normal
layout with matching blend and UV payloads; missing tangents are not fabricated.
Expression reversal remains limited to its recognized 40-byte layout.
It also accepts split skinned `ATTRIBUTE`
layouts with declared float positions, packed tangent/normals, half-float UVs
and four or eight blend influences. All streams must have matching complete
binary payloads; draw-subset header counts do not discard the remaining vertices.
Direct meshes retain captured coordinates without fabricated blend data.
Compute-only skinning and unfamiliar layouts may
still need support. Unsupported captures fail without replacing existing files. Partial
extractions still save the identified asset components. Counts and skipped resource
groups remain in `extraction_report.json`; unresolved/unsupported groups and
unproven direct candidates have separate counts. Add/Update closes on success without a routine completion
message. Partial results show a warning because some asset parts may be missing,
including after an update. Failures, including duplicate asset names, stay visible in the dialog.
The dump-field description identifies the FrameAnalysis folder to select.
Unknown texture roles remain available without
inventing material bindings. Recognized ZZMI material signatures retain their
existing role interpretation when the capture supplies ZZMI evidence.
Recognized GIMI material signatures label diffuse maps automatically using the
capture marker, color pass, bound resource hashes and neighboring formats/sizes.
Recognized material passes take priority over unrelated effects with more textures.
After these reliable signatures, an extraction-only pixel fallback can classify
remaining unknown maps. It preserves confirmed roles, excludes contaminated and
auxiliary resources, and caches small sampled features by texture payload.
Float, HDR and typeless resources remain unclassified by this fallback because
their captured display pixels cannot establish supported material-map encodings.
Unique strong color evidence can identify a diffuse; vector-like normal pixels
also need matching diffuse layout before an automatic normal-map assignment.
Ambiguous LightMap/MaterialMap suggestions and similarity scores are saved in
`extraction_report.json` without binding those roles. Unsupported images remain
available as unclassified candidates. Normal Asset loading stays lazy.
For GIMI, the fallback distinguishes centered R/G normal data from packed
LightMap channels in the primary material family. It excludes unused atlas
padding, including uniform colored borders, and allows normal B to carry detail instead of normal Z. It does
not infer MaterialMap for GIMI. Complete GIMI material triplets take priority over
larger inherited resource sets; confirmed roles still take priority over pixels.
GIMI exports preserve unclassified textures as selectable candidates, including
when a diffuse is recognized. Unknown roles never become automatic material
bindings. Resources with auxiliary or unusable evidence remain in the report;
debug exports can include their original payloads under `assets/`.
RGB pixel inspection resizes independently of alpha so near-zero data alpha does
not distort the analyzed colors. Export preserves the original texture bytes.

Viewer exports carry the `mod-viewer` format marker. ZZMI/GIMI retain `hash.json`;
WWMI uses native `Metadata.json`, `TextureUsage.json` and `Component N.fmt/.vb/.ib`.
The metadata filename and structure identify the layout, independently of the
shared format marker. Earlier ZZMI/GIMI viewer format markers remain readable.
For WWMI captures, `vb0_hash` records the position-buffer hash used for asset
matching separately from the captured IB hash. The original resource identities
and ranges stay in the report. Native binary indices retain source winding;
loading applies the viewer's existing WWMI convention once. Source buffers and captured
positions remain unchanged. Shader-only expression/deformation streams are not
reconstructed. Unknown texture roles remain selectable candidates.
Each split WWMI draw range becomes its own component within its source object
folder. Each native binary component retains only its referenced vertices,
preserving all vertex channels and four/eight influences with local indices.
Each object folder is named by its native position hash, matching the native
WWMI multi-object layout, and contains its own component metadata, geometry and
texture copies. There is no root metadata or root texture dependency, and
payloads are not hardlinked across objects. The index recognizes the child
metadata and loads the parent as one asset. A copied object folder also works
as a standalone asset. Descriptive object names supplied through the internal
rename API label its components without changing the native folder identity.
If an object's metadata is invalid, indexing retains usable sibling objects and
reports the discarded metadata through index warnings.
WWMI texture names follow `Components-0-1 t=12345678.dds`: the ordinals list
exactly the components that reference that payload. Each native hash/payload
pair is exported once per object, and distinct contents with the same native
hash receive a content-hash suffix. PNG and JPEG captures keep their encoding
and extension. Candidate membership uses these native names; matching a texture
hash substring never associates another WWMI component's images. Exact component
metadata references retain proven material roles for automatic assignment.
In WWMI captures, complete bound format/size families can identify a
diffuse at different slots and take priority over pixel inference and unrelated
effects. Other maps remain unbound unless the existing fallback establishes a
role. No universal diffuse slot or color-format-only rule is assumed.
Complete WWMI families outrank simpler texture pairs even when the simpler pass
has more bound resources. Hair signatures allow compressed or uncompressed
rectangular auxiliary masks and a full- or half-resolution vector atlas. Face
signatures accept BC1 or BC3 masks with a verified shared-data pair. A separate
layered family requires a bound constant, scaled mask/color atlases and a repeated
packed mask; inherited copies on another color-led pass cannot establish that family.
Eye signatures distinguish the larger iris atlas
from an sRGB-encoded data map, before pixel fallback runs.
Color-led hair families use equal color/vector atlases and a short, wide lookup
texture, taking priority over inherited eye inputs on the same draw range.
Compact main families retain a half-size mask and quarter-size trailing color
input. Layered eye families also validate a half-size packed atlas and the
leading mask/data scales. Unlit atlas families require a constant or shared-mask
prefix, scaled small color inputs and a repeated native-hash input. Their
screen-space resources remain unclassified.
Face masks may match the shared packed-data atlas in size only when a double-size
color atlas and trailing short, wide lookup complete the family. This variant
takes priority over competing color-led hair passes on the same geometry range.
The compact-main signature requires each square input to be at least 128 pixels
wide, including its quarter-size trailing input. Lookup-led signatures require
a lookup width of at least 128 pixels, height between 2 and 127 pixels, and width
at least four times its height. Smaller layouts remain unclassified by these
signatures; scale relationships alone do not establish their roles.

Layered GIMI eye materials identify their base iris diffuse through the complete
bound family: scaled reflection/effect atlases and an exact repeated constant
resource/version/payload. Other eye layers remain selectable candidates. This
family takes priority over inherited primary bindings; replaced ramp snapshots
are not used to establish it.

There is no special `Face/` output folder. Facial geometry follows the same
hash-folder and texture-ownership rules as body, hair and weapon geometry.
GIMI face recognition requires a unique connected face surface with two comparable
open eye boundaries inside its enclosing contour, plus shared diffuse/light
payloads on related geometry. Legacy split eye rings can instead use a bilateral
native Eyes reference and matching eye-loop fit.
Additional recognized maps, such as NormalMap, and unclassified texture candidates
do not prevent grouping; duplicate passes still require matching recognized maps.
Small additional internal openings do not prevent recognition when their diagonal
is at most half the smaller eye opening's diagonal. Their geometry remains intact.
When several supported facial anchors compete, extraction retains one best-effort
face, preferring greater connected-surface coverage and fewer small openings, with
a stable content/resource tie break. Competing candidates and unproven attachments
stay excluded. The report records this ambiguity and the result is marked partial;
captured-matrix placement must still pass its existing checks.
Related nearby direct meshes receive facial labels independently of placement.
Equivalent captured facial passes are excluded by default while their
provenance remains in the report; differing UVs or ambiguous references stay separate.
Some GIMI face captures also contain a separate shell over the eye openings.
When its UV tags, contiguous index range, bounds and shared outer boundary prove
that separation, extraction keeps it as an optional `FaceEyeOverlay` component
and excludes it by default. The report retains its source range and streams.
The shell may use matching primary UV channels or a separately mapped UV2/UV3
pair. The latter also requires additional closed boundaries in the retained
surface; both layouts require matching primary UVs along the shared outer rim.
Only explicit selection through the internal extractor API can export it; the
Add/Update dialog currently has no component or overlay selector. Facial vertices are unchanged.
Boundary matching allows a small shell offset within 0.01% of the face bounds'
diagonal, with one-to-one vertex and matching UV checks. The report records the
measured errors and tolerance; larger offsets and ambiguous matches stay included.
Other decodable direct geometry has unproven asset membership and is excluded
from default extraction. It remains in the analysis report and contributes only
to `unproven_candidates`, without triggering a partial warning or increasing
`skipped`. Unresolved or unsupported geometry still triggers the warning; it
does not establish that the missing geometry belongs to the asset. A non-partial
result is not a completeness guarantee. Supported direct candidates can also be
explicitly selected through the internal API; no new component-selection UI is
provided. A proven open-eye face can establish its family without native Eyes.
Extraction derives placement from captured face/body object matrices and a native
bone palette verified by replaying the captured stream-output positions. One
consistent transform must satisfy the supported head-local axis convention across
the facial draws and matching used bones. Bone IDs and constant-buffer slots may
vary. Missing or conflicting evidence retains captured coordinates and reports a
partial extraction; it never falls back to fitting the new face to eye geometry.
Proven placement is baked into exported facial positions and directions, with
matrix, resource versions, source hashes and replay evidence retained in the report.
Placement dependencies include native candidates that contribute a matching
transform; unrelated native candidates do not invalidate extraction.
Original position-buffer bytes are retained under the object's `source_geometry`
directory and referenced by the placement metadata. UVs, indices and capture files
remain unchanged. Native skinned facial
parts already use body rest coordinates and receive no extra transform.
Hash metadata records the coordinate space and placement status, preventing loader
realignment, including standalone face selections. Legacy exports without this
metadata retain the existing geometry-derived compatibility alignment when a
standalone native Eyes component is available. Re-extract experimental exports
that depended on embedded eye geometry to obtain captured-matrix placement.

ZZMI/GIMI compact metadata names each component's VB file, each range's IB file and each
texture payload relative to its `hash.json`. These associations keep captured
resource versions separate when their hashes collide, including unknown texture
roles. Legacy assets retain unique-hash lookup and component-associated dump
matching; unresolved distinct same-hash geometry stays unbound.
GIMI/ZZMI compact texture filenames include the asset name, component, role and native hash,
such as `Asset_001-Body-Diffuse-12345678.dds`. Distinct payloads sharing one hash
also retain a content suffix. Metadata references each file explicitly; identical
hash/payload pairs share one file named for their first owning component within
the same object, including across its components and parts. Another object's
copy stays in that object's folder.
Distinct native hash aliases and differing payloads stay separately addressable;
identical bytes can additionally share storage through output-only hardlinks
within one object.

WWMI component metadata also retains explicit `texture_hashes` file/role records;
`TextureUsage.json` contains captured `Component N` / `ps-tN` hash/shader evidence.
Mod previews read exact component texture references. Proven roles supply original
Asset defaults when the mod has no applicable binding; unknown roles remain
selectable candidates. Native WWMI `TextureUsage.json` retains its candidate-only
meaning, without assigning roles from numeric slots.

The dependency-free engine in `src/app/assets/extractor` derives from XXMI
Extract 0.3.1, GPL-3.0-or-later. Its standalone GUI, CLI, packaging and Windows
drop handling are omitted. The viewer integration adapts destination handling,
generic capture acceptance and compact package identification.

Upstream research attribution retained from XXMI Extract:

- Petrascyll/gui_collect (GPL-3.0), revision
  `9faf229835a98f30f516c34bc92472fcbf843b24`, informed stream assembly,
  compatibility output and morph reversal.
- leotorrez/ZZMI-Package (GPL-3.0), revision
  `1ecd24dbc85f96239eaadeb7c4e0788aef7fae4f`, informed conservative material
  signatures in `profiles/zzz.py`.

Neither project is a runtime dependency. The repository's GPL license applies.

GIMI diffuse signature research uses the public
[ORFix material layouts](https://github.com/SilentNightSound/GIMI-Package/blob/main/GIMI/Core/GIMI/Libraries/ORFix.ini).
Their varying layouts are why slot numbers alone cannot establish map roles.
GIMI packed-channel fallback also references the independent
[HoyoToon Genshin shader](https://github.com/Hoyotoon/HoyoToon/blob/main/Shaders/Genshin%20Impact/Includes/HoyoToonGenshin-program.hlsl),
which separates normal XY from B detail and reads material regions from LightMap.
