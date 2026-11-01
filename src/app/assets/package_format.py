"""Identity of viewer-generated Asset metadata, independent of game layout."""

FORMAT = "mod-viewer"
WWMI_METADATA = "Metadata.json"


def is_viewer_metadata(value):
    """Accept earlier viewer exports as well as the current format marker."""
    return isinstance(value, dict) and value.get("format") in (
        FORMAT, "mod-viewer-compact")
