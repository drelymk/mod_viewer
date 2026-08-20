"""Conservative per-mesh material-kind classification.

Material kinds are intentionally separate from material profiles.  A mesh can
be identified as a face while still using the base game profile until a
face-specific interpretation has been validated.
"""

from dataclasses import dataclass
import re


MATERIAL_KIND_UNKNOWN = "unknown"
MATERIAL_KIND_BODY = "body"
MATERIAL_KIND_FACE = "face"
MATERIAL_KIND_HAIR = "hair"
MATERIAL_KIND_EYE = "eye"
MATERIAL_KIND_WEAPON = "weapon"
MATERIAL_KIND_SPECIAL = "special"

MATERIAL_KINDS = frozenset({
    MATERIAL_KIND_UNKNOWN,
    MATERIAL_KIND_BODY,
    MATERIAL_KIND_FACE,
    MATERIAL_KIND_HAIR,
    MATERIAL_KIND_EYE,
    MATERIAL_KIND_WEAPON,
    MATERIAL_KIND_SPECIAL,
})


@dataclass(frozen=True)
class MaterialKindDetection:
    """One mesh-kind result, including whether it is safe for resolution."""

    kind: str = MATERIAL_KIND_UNKNOWN
    reliable: bool = False
    reason: str = ""

    def __post_init__(self):
        if self.kind not in MATERIAL_KINDS:
            raise ValueError(f"Unknown material kind: {self.kind}")


def _normalize_kind(value):
    value = str(value or "").strip().lower()
    return value if value in MATERIAL_KINDS else None


def _component_hint(value):
    tokens = re.findall(r"[a-z0-9]+", str(value or "").lower())
    candidates = [token for token in tokens if token in MATERIAL_KINDS - {"unknown"}]
    if not candidates:
        return None
    if len(set(candidates)) != 1:
        return MaterialKindDetection(
            reason="conflicting component-kind hints")
    return MaterialKindDetection(
        kind=candidates[0], reliable=False, reason="component-name hint")


def detect_material_kind(entry=None):
    """Return a conservative kind result for one mesh/draw entry.

    Only explicit structured evidence may be reliable.  Component labels are
    retained as weak diagnostics, but never select a specialized profile.
    Texture and resource filenames are deliberately not inspected.
    """
    if not isinstance(entry, dict):
        return MaterialKindDetection()

    evidence = entry.get("material_kind_evidence")
    if isinstance(evidence, dict):
        kind = _normalize_kind(evidence.get("kind"))
        if kind is not None:
            return MaterialKindDetection(
                kind=kind,
                reliable=evidence.get("reliable") is True,
                reason=str(evidence.get("reason") or "explicit material evidence"),
            )

    explicit = _normalize_kind(entry.get("material_kind"))
    if explicit is not None:
        return MaterialKindDetection(
            kind=explicit,
            reliable=entry.get("material_kind_reliable") is True,
            reason=str(entry.get("material_kind_reason")
                       or "explicit material kind"),
        )

    return (_component_hint(entry.get("component"))
            or _component_hint(entry.get("name"))
            or MaterialKindDetection())
