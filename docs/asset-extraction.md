# Asset extraction

Right-click an Asset Folder and choose **Add asset**.
Enter a name, choose the frame-dump folder with Browse, and click OK. The new
asset is indexed automatically. Right-click an indexed asset and choose
**Update asset** to replace all of its files; its name is prefilled and editable.
Extraction and indexing finish before the old files are removed. Failed updates
restore the previous asset and index.

Extraction is available for all registered Asset Folder types. It reads captured
resource history and validated stream layouts without requiring a game marker.
The initial engine reconstructs supported stream-output skinning layouts;
compute-only skinning, static attachments and unfamiliar layouts may still need
support. Unsupported captures fail without replacing existing files. Partial
extractions still save the supported components. The Assets panel reports the
saved component count and a warning with the number of skipped resource groups;
those groups may be scene/UI geometry or unsupported asset parts. Only a failed
operation uses the error display. Unknown texture roles remain available without
inventing material bindings. Recognized ZZMI material signatures retain their
existing role interpretation when the capture supplies ZZMI evidence.

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
