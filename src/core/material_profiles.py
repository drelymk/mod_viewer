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
    # These are diagnostic-only views of packed WuWa normal data.  They are
    # deliberately separate from metalness/gloss/specular so the packed B/A
    # channels cannot become stock PBR inputs by accident.
    normal_data_b: ChannelRef | None = None
    normal_data_a: ChannelRef | None = None
    shadow_mask: ChannelRef | None = None
    material_id: ChannelRef | None = None
    material_id_decoder: str | None = None
    metalness: ChannelRef | None = None
    gloss: ChannelRef | None = None
    specular: ChannelRef | None = None
    specular_area: ChannelRef | None = None
    ao: ChannelRef | None = None
    # Genshin's R response remains deliberately bounded. The source channel
    # is classification data on some face materials, not a literal full-range
    # metalness map.
    metalness_scale: float = 1.0
    specular_scale: float = 1.0
    # An optional influence blend keeps a mask from replacing the stock
    # response outright. This is useful for Genshin's classification-like R
    # channel; ZZZ's authored specular mask remains a direct response.
    specular_influence: float | None = None
    toon_specular_shininess: float = 10.0
    toon_specular_threshold_bias: float = 1.015
    toon_specular_softness: float = 0.0
    toon_specular_metal_cutoff: float | None = None
    shadow_threshold: float = 0.5
    shadow_softness: float = 0.08
    shadow_mask_strength: float = 0.5
    shadow_influence: float = 1.0
    direct_shadow_model: str | None = None
    wuwa_shadow_process: float = 0.55
    wuwa_shadow_front_offset: float = 0.4
    wuwa_shadow_width: float = 0.01
    wuwa_shadow_influence: float = 1.0

    def __post_init__(self):
        if self.material_kind not in MATERIAL_KINDS:
            raise ValueError(f"Unknown material kind: {self.material_kind}")
        if self.direct_shadow_model not in (None, "genshin_toon", "wuwa_base"):
            raise ValueError(
                f"Unknown direct shadow model: {self.direct_shadow_model}")

    def to_metadata(self):
        result = {
            "id": self.id,
            "game": self.game,
            "texture_api": self.texture_api,
            "material_kind": self.material_kind,
            "normal_xy": list(self.normal_xy) if self.normal_xy else None,
            "normal_data_b": (self.normal_data_b.to_metadata()
                              if self.normal_data_b else None),
            "normal_data_a": (self.normal_data_a.to_metadata()
                              if self.normal_data_a else None),
            "shadow_mask": (self.shadow_mask.to_metadata()
                            if self.shadow_mask else None),
            "material_id": (self.material_id.to_metadata()
                            if self.material_id else None),
            "material_id_decoder": self.material_id_decoder,
            "metalness": (self.metalness.to_metadata()
                          if self.metalness else None),
            "gloss": self.gloss.to_metadata() if self.gloss else None,
            "specular": (self.specular.to_metadata()
                          if self.specular else None),
            "specular_area": (self.specular_area.to_metadata()
                              if self.specular_area else None),
            "ao": self.ao.to_metadata() if self.ao else None,
            "metalness_scale": self.metalness_scale,
            "specular_scale": self.specular_scale,
            "specular_influence": self.specular_influence,
            "toon_specular_shininess": self.toon_specular_shininess,
            "toon_specular_threshold_bias": self.toon_specular_threshold_bias,
            "toon_specular_softness": self.toon_specular_softness,
            "toon_specular_metal_cutoff": self.toon_specular_metal_cutoff,
            "shadow_threshold": self.shadow_threshold,
            "shadow_softness": self.shadow_softness,
            "shadow_mask_strength": self.shadow_mask_strength,
            "shadow_influence": self.shadow_influence,
            "direct_shadow_model": self.direct_shadow_model,
            "wuwa_shadow_process": self.wuwa_shadow_process,
            "wuwa_shadow_front_offset": self.wuwa_shadow_front_offset,
            "wuwa_shadow_width": self.wuwa_shadow_width,
            "wuwa_shadow_influence": self.wuwa_shadow_influence,
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
            # G is the validated first toon-shadow input. A classifies the
            # authored material region and B gates the toon highlight area;
            # both are read from this same intact packed texture.
            shadow_mask=ChannelRef("light_map", "g"),
            material_id=ChannelRef("light_map", "a"),
            material_id_decoder="genshin_5_region",
            metalness=ChannelRef("light_map", "r"),
            # R remains the first-pass specular response. B controls the
            # highlight threshold and is deliberately not an intensity map.
            specular=ChannelRef("light_map", "r"),
            specular_area=ChannelRef("light_map", "b"),
            metalness_scale=0.08,
            specular_scale=1.0,
            specular_influence=0.15,
            toon_specular_shininess=10.0,
            toon_specular_threshold_bias=1.015,
            toon_specular_softness=0.0,
            toon_specular_metal_cutoff=0.90,
            direct_shadow_model="genshin_toon",
        )
    if game == "wuwa":
        # RabbitFX/WuWa's packed normal/material layout is retained for the
        # shader adapter. B/A are exposed only as raw diagnostics; their
        # material response remains deliberately unguessed in this PR.
        kwargs = {
            "normal_xy": ("r", "g"),
            "normal_data_b": ChannelRef("normal_data", "b"),
            "normal_data_a": ChannelRef("normal_data", "a"),
        }
        if texture_api == "rabbitfx":
            kwargs.update(
                shadow_mask=ChannelRef("light_map", "g"),
                direct_shadow_model="wuwa_base",
            )
        return MaterialInterpretation(
            id=f"wuwa:{texture_api}", game=game, texture_api=texture_api,
            **kwargs,
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
