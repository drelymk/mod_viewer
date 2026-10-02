"""Validated, explicitly changed global environment and tool preferences."""

from . import config


PREFERENCES_KEY = "viewerPreferences"
BOOLEAN_KEYS = {
    "wireframe", "outlines", "bloom", "glossy", "toonShading", "grid",
    "smoothShading", "navigationGizmo",
}
CHOICES = {
    "environment": ("default", "studio", "indoor", "outdoor"),
    "textureMode": ("all", "diffuse-normal", "diffuse", "none"),
}
RANGES = {"ambientOcclusion": (0, 1), "keyLightIntensity": (0, 1.5)}


def _validated_preferences(value):
    if not isinstance(value, dict):
        raise ValueError("Viewer preferences must be an object.")
    validated = {}
    for key, setting in value.items():
        if key in BOOLEAN_KEYS:
            valid = isinstance(setting, bool)
        elif key in CHOICES:
            valid = isinstance(setting, str) and setting in CHOICES[key]
        elif key in RANGES:
            low, high = RANGES[key]
            valid = (not isinstance(setting, bool)
                     and isinstance(setting, (int, float))
                     and low <= setting <= high)
        else:
            raise ValueError(f"Unknown viewer preference: {key}.")
        if not valid:
            raise ValueError(f"Invalid viewer preference: {key}.")
        validated[key] = setting
    return validated


def load_preferences(config_file=None):
    """Load only explicit settings; reading defaults never creates config."""
    value = config.read_config(config_file).get(PREFERENCES_KEY, {})
    return _validated_preferences(value)


@config.transaction()
def save_preferences(changes, config_file=None):
    """Merge a validated patch without losing other config or tool settings."""
    changes = _validated_preferences(changes)
    value = config.read_config(config_file)
    preferences = _validated_preferences(value.get(PREFERENCES_KEY, {}))
    preferences.update(changes)
    if changes:
        value[PREFERENCES_KEY] = preferences
        config.write_config(value, config_file)
    return preferences
