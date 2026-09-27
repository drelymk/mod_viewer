"""Feature flags for optional UI actions.

Source runs enable every action. Frozen builds use build-time values, defaulting
missing flags to enabled. These flags hide UI actions; backend APIs remain
available.
"""

from . import paths

_DEFAULTS = {
    "export": True,
    "modify_toggle": True,
    "open_disabled_mod": True,
    "edit_mesh": True,
}


def get_features():
    """Returns the resolved optional feature flags.

    Always all-True when not frozen. When frozen, a missing baked module or
    a missing constant both fall back to True -- a broken build should never
    silently hide a feature nobody deliberately disabled.
    """
    if not paths.is_frozen():
        return dict(_DEFAULTS)

    try:
        from . import _baked_features as baked
    except ImportError:
        return dict(_DEFAULTS)

    return {
        "export": bool(getattr(baked, "EXPORT", True)),
        "modify_toggle": bool(getattr(baked, "MODIFY_TOGGLE", True)),
        "open_disabled_mod": bool(getattr(baked, "OPEN_DISABLED_MOD", True)),
        "edit_mesh": bool(getattr(baked, "EDIT_MESH", True)),
    }
