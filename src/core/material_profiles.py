"""Game-and-texture-API material interpretations for packed map shaders.

Texture profiles decide how an image is transported and optionally derive a
stock normal map.  Material interpretations are a separate concern: they
describe which channels in an intact packed texture have a meaning for the
viewer shader.  Keeping the two tables separate prevents a transport recipe
from silently becoming a rendering assumption.
"""

from dataclasses import dataclass


_SOURCES = frozenset(("normal_data", "light_map", "material_map"))
_CHANNELS = frozenset("rgba")


@dataclass(frozen=True)
class ChannelRef:
    """One semantic channel in one role-aware packed texture."""

    source: str
    channel: str
    invert: bool = False

    def __post_init__(self):
        if self.source not in _SOURCES:
            raise ValueError(f"Unknown packed material source: {self.source}")
        if self.channel not in _CHANNELS:
            raise ValueError(f"Unknown packed material channel: {self.channel}")

    def to_metadata(self):
        return {
            "source": self.source,
            "channel": self.channel,
            "invert": self.invert,
        }


@dataclass(frozen=True)
class MaterialInterpretation:
    """Semantic packed-map channels understood by the frontend adapter."""

    id: str
    game: str
    texture_api: str
    normal_xy: tuple[str, str] | None = None
    shadow_mask: ChannelRef | None = None
    material_id: ChannelRef | None = None
    metalness: ChannelRef | None = None
    gloss: ChannelRef | None = None
    specular: ChannelRef | None = None
    ao: ChannelRef | None = None
    # Genshin's R response is deliberately capped until the later LightMap
    # highlight-threshold and material-ID work. The source channel is
    # classification data on some face materials, not a literal full-range
    # metalness map.
    metalness_scale: float = 1.0
    specular_scale: float = 1.0
    # An optional influence blend keeps a mask from replacing the stock
    # response outright. This is useful for Genshin's classification-like R
    # channel; ZZZ's authored specular mask remains a direct response.
    specular_influence: float | None = None

    def to_metadata(self):
        result = {
            "id": self.id,
            "game": self.game,
            "texture_api": self.texture_api,
            "normal_xy": list(self.normal_xy) if self.normal_xy else None,
            "shadow_mask": (self.shadow_mask.to_metadata()
                            if self.shadow_mask else None),
            "material_id": (self.material_id.to_metadata()
                            if self.material_id else None),
            "metalness": (self.metalness.to_metadata()
                          if self.metalness else None),
            "gloss": self.gloss.to_metadata() if self.gloss else None,
            "specular": (self.specular.to_metadata()
                          if self.specular else None),
            "ao": self.ao.to_metadata() if self.ao else None,
            "metalness_scale": self.metalness_scale,
            "specular_scale": self.specular_scale,
            "specular_influence": self.specular_influence,
        }
        return result


def _profile_for(game, texture_api):
    if game == "zzz" and texture_api in ("zzmi", "rabbitfx"):
        return MaterialInterpretation(
            id=f"zzz:{texture_api}", game=game, texture_api=texture_api,
            material_id=ChannelRef("material_map", "r"),
            metalness=ChannelRef("material_map", "g"),
            specular=ChannelRef("material_map", "b"),
        )
    if game == "genshin" and texture_api in ("gimi", "rabbitfx"):
        return MaterialInterpretation(
            id=f"genshin:{texture_api}", game=game,
            texture_api=texture_api,
            # G is the validated first toon-shadow input. The frontend uses a
            # viewer approximation; B remains reserved for the later
            # highlight-threshold response, and A for IDs.
            shadow_mask=ChannelRef("light_map", "g"),
            metalness=ChannelRef("light_map", "r"),
            # R is the first-pass specular mask. B controls the highlight
            # threshold and remains reserved for that later phase.
            specular=ChannelRef("light_map", "r"),
            metalness_scale=0.08,
            specular_scale=1.0,
            specular_influence=0.15,
        )
    if game == "wuwa":
        # RabbitFX/WuWa's packed normal/material layout is retained for the
        # shader adapter, but Z/W semantics remain unguessed in this PR.
        return MaterialInterpretation(
            id=f"wuwa:{texture_api}", game=game, texture_api=texture_api,
            normal_xy=("r", "g"),
        )
    return None


def material_profile_for(game=None, texture_api=None):
    """Resolve a conservative material interpretation from full detection.

    Accepts a ``GameDetection`` object or metadata dictionary as ``game`` for
    callers that already carry the complete detection, while retaining the
    explicit two-axis form for tests and small core integrations.
    """
    if hasattr(game, "game"):
        detection = game
        game, texture_api = detection.game, detection.texture_api
    elif isinstance(game, dict):
        game, texture_api = (game.get("game") or game.get("id"),
                             game.get("texture_api", texture_api))
    game = str(game or "unknown").lower()
    texture_api = str(texture_api or "unknown").lower()
    profile = _profile_for(game, texture_api)
    if profile is not None:
        return profile
    return MaterialInterpretation(
        id="none", game=game, texture_api=texture_api)
