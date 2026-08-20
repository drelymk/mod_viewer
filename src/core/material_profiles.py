"""Game-and-texture-API material interpretations for packed map shaders.

Texture profiles decide how an image is transported and optionally derive a
stock normal map.  Material interpretations are a separate concern: they
describe which channels in an intact packed texture have a meaning for the
viewer shader.  Keeping the two tables separate prevents a transport recipe
from silently becoming a rendering assumption.
"""

from dataclasses import dataclass

from .material_kind import MATERIAL_KINDS


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
    material_kind: str = "unknown"
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
    shadow_threshold: float = 0.5
    shadow_softness: float = 0.08
    shadow_mask_strength: float = 0.5
    shadow_influence: float = 1.0

    def __post_init__(self):
        if self.material_kind not in MATERIAL_KINDS:
            raise ValueError(f"Unknown material kind: {self.material_kind}")

    def to_metadata(self):
        result = {
            "id": self.id,
            "game": self.game,
            "texture_api": self.texture_api,
            "material_kind": self.material_kind,
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
            "shadow_threshold": self.shadow_threshold,
            "shadow_softness": self.shadow_softness,
            "shadow_mask_strength": self.shadow_mask_strength,
            "shadow_influence": self.shadow_influence,
        }
        return result


def _base_profile_for(game, texture_api):
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


def _specialized_profile_for(game, texture_api, material_kind):
    """Return a validated kind-specific profile, when one exists.

    PR16 intentionally has no production specialized profiles yet.  Keeping
    this hook separate makes exact-kind precedence testable and gives later
    validated semantics one narrow insertion point.
    """
    del game, texture_api, material_kind
    return None


def _profile_for(game, texture_api, material_kind="unknown"):
    if material_kind != "unknown":
        specialized = _specialized_profile_for(
            game, texture_api, material_kind)
        if specialized is not None:
            return specialized
    return _base_profile_for(game, texture_api)


def material_profile_for(game=None, texture_api=None, material_kind="unknown"):
    """Resolve a conservative material interpretation from full detection.

    Accepts a ``GameDetection`` object or metadata dictionary as ``game`` for
    callers that already carry the complete detection, while retaining the
    explicit game/API/kind form for tests and small core integrations.
    """
    if hasattr(game, "game"):
        detection = game
        game, texture_api = detection.game, detection.texture_api
    elif isinstance(game, dict):
        game, texture_api = (game.get("game") or game.get("id"),
                             game.get("texture_api", texture_api))
    game = str(game or "unknown").lower()
    texture_api = str(texture_api or "unknown").lower()
    material_kind = str(material_kind or "unknown").lower()
    if material_kind not in MATERIAL_KINDS:
        material_kind = "unknown"
    profile = _profile_for(game, texture_api, material_kind)
    if profile is not None:
        return profile
    return MaterialInterpretation(
        id="none", game=game, texture_api=texture_api)
