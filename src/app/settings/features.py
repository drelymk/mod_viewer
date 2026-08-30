"""Build-time feature flags: which optional UI actions a *built* exe exposes.

The three flags (Export / Modify_Toggle / Open_Disabled_Mod) are configured via
features.ini at the repo root, a build-time-only input never bundled into the
exe. build.py reads
it and bakes the resolved booleans into app/settings/_baked_features.py, a generated
module compiled into the exe like any other app code, then deletes it again
after PyInstaller has run — so there's no plain config file for an end user
to flip back on.

Only consulted when running as a built exe (paths.is_frozen()); a source
checkout always shows every feature. This only ever hides a button in the UI
— the backend it would have called is untouched and still reachable.
"""

from . import paths

_DEFAULTS = {
    "export": True,
    "modify_toggle": True,
    "open_disabled_mod": True,
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
    }
