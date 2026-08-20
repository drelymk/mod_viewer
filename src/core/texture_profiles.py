"""Conservative texture recipes for detected game profiles."""

from dataclasses import dataclass


TEXTURE_ROLES = (
    "diffuse", "normal_map", "normal_data", "light_map", "material_map")


@dataclass(frozen=True)
class TextureProfile:
    """Viewer-safe interpretation of authored texture roles."""

    name: str
    normal_y_sign: int
    bind_normal_map: bool
    bind_light_map: bool = False
    bind_material_map: bool = False
    retain_normal_data: bool = False

    @property
    def game(self):
        """Alias useful to callers that treat a profile as game metadata."""
        return self.name

    def recipe_for(self, role=None):
        role = role if role in TEXTURE_ROLES else "diffuse"
        if role == "normal_map" and self.bind_normal_map:
            return "normal_xy_reconstruct"
        if role == "normal_data":
            return "passthrough"
        return "passthrough"


_PROFILES = {
    "genshin": TextureProfile("genshin", -1, True),
    "zzz": TextureProfile("zzz", -1, True),
    "wuwa": TextureProfile("wuwa", -1, True, retain_normal_data=True),
    # SRMI/HSR texture semantics are intentionally conservative until the
    # runtime's packed normal/material layout is modeled explicitly.
    "hsr": TextureProfile("hsr", 1, False),
    # Unknown mods retain authored keys for inspection; the renderer only
    # applies a semantic packed-map response when a separate material profile
    # justifies it.
    "unknown": TextureProfile("unknown", 1, False),
}


def texture_profile_for(game=None):
    """Return a profile for a game id, detection object or metadata dict."""
    if hasattr(game, "game"):
        game = game.game
    elif isinstance(game, dict):
        game = game.get("id") or game.get("game")
    key = str(game or "unknown").lower()
    return _PROFILES.get(key, _PROFILES["unknown"])
