# Asset extraction

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
The engine reconstructs supported stream-output skinning layouts and declared
direct position/normal/UV streams. Direct meshes retain captured coordinates
without fabricated blend data. Compute-only skinning and unfamiliar layouts may
still need support. Unsupported captures fail without replacing existing files. Partial
extractions still save the identified asset components. Counts and skipped resource
groups remain in `extraction_report.json`; those groups may be scene/UI geometry
or unsupported asset parts. Add/Update closes on success without a routine completion
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

Compact `hash.json` packages carry the `mod-viewer-compact` format marker so they
can also coexist with native WWMI assets in a WWMI Asset Folder. Native WWMI
metadata and geometry retain their existing import path.

When reconstructed expression data and recognized facial material evidence agree,
the extractor groups those components in `Face/` with their own `hash.json`.
Face geometry and face-only textures go there; shared textures stay at the asset
root. Loading the parent asset includes both metadata files. Component names,
numbers and vertical position do not determine this grouping. Unproven components
keep their existing location. A selection containing only facial components stays
at the root so it remains a loadable standalone asset.
GIMI grouping is automatic when one bilateral native Eyes reference and a matching
eye-loop surface establish a face family with shared diffuse/light payloads.
Additional recognized maps, such as NormalMap, and unclassified texture candidates
do not prevent grouping; duplicate passes still require matching recognized maps.
Related nearby direct meshes receive the face labels used by the viewer's existing
alignment. Equivalent captured facial passes are excluded by default while their
provenance remains in the report; differing UVs or ambiguous references stay separate.
Some GIMI face captures also contain a separate shell over the eye openings.
When its UV tags, contiguous index range, bounds and shared outer boundary prove
that separation, extraction keeps it as an optional `FaceEyeOverlay` component
and excludes it by default. The report retains its source range and streams.
Only explicit selection through the internal extractor API can export it; the
Add/Update dialog currently has no component or overlay selector. Facial vertices are unchanged.
Boundary matching allows a small shell offset within 0.01% of the face bounds'
diagonal, with one-to-one vertex and matching UV checks. The report records the
measured errors and tolerance; larger offsets and ambiguous matches stay included.
Other decodable direct geometry has unproven asset membership and is excluded
from default extraction. It remains in the analysis report and contributes to
partial/skipped counts. Supported direct candidates can also be explicitly
selected through the internal API; no new component-selection UI is provided.

Compact metadata names each component's VB file, each range's IB file and each
texture payload relative to its `hash.json`. These associations keep captured
resource versions separate when their hashes collide, including unknown texture
roles. Legacy assets retain unique-hash lookup and component-associated dump
matching; unresolved distinct same-hash geometry stays unbound.
Compact texture filenames include the asset name, component, role and native hash,
such as `Asset_001-Body-Diffuse-12345678.dds`. Distinct payloads sharing one hash
also retain a content suffix. Metadata references each file explicitly; identical
hash/payload pairs share one file named for their first owning component, including
across components and parts. Shared face/body textures remain at the asset root.
Distinct native hash aliases and differing payloads stay separately addressable;
identical bytes can additionally share storage through output-only hardlinks.

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
