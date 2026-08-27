"""Game-and-texture-API material interpretations for packed map shaders.

Texture profiles decide how an image is transported and optionally derive a
stock normal map.  Material interpretations are a separate concern: they
describe which channels in an intact packed texture have a meaning for the
viewer shader.  Keeping the two tables separate prevents a transport recipe
from silently becoming a rendering assumption.
"""

from dataclasses import dataclass, replace

from .kind import MATERIAL_KINDS


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
    # deliberately separate from metalness/specular so the packed B/A
    # channels cannot become stock PBR inputs by accident.
    normal_data_b: ChannelRef | None = None
    normal_data_a: ChannelRef | None = None
    toon_specular_mask: ChannelRef | None = None
    metal_route: ChannelRef | None = None
    direct_specular_model: str | None = None
    shadow_mask: ChannelRef | None = None
    material_id: ChannelRef | None = None
    material_id_decoder: str | None = None
    metalness: ChannelRef | None = None
    specular: ChannelRef | None = None
    specular_area: ChannelRef | None = None
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
    shadow_level: float = 0.0
    shadow_mask_strength: float = 0.5
    shadow_influence: float = 1.0
    direct_shadow_model: str | None = None
    wuwa_shadow_process: float = 0.55
    wuwa_shadow_front_offset: float = 0.4
    wuwa_shadow_width: float = 0.01
    # RabbitFX LightMap.G is a packed shadow/visibility classification.  It
    # is not a linear multiplier for the direct-light response.
    wuwa_shadow_mask_cutoff: float = 0.1
    # Exact LightMap.G endpoints are commonly an un-authored/alternate packed
    # value rather than a RabbitFX shadow classification.  Ignore those
    # endpoints while retaining midrange authored classifications.
    wuwa_shadow_mask_endpoint_tolerance: float = 0.01
    wuwa_shadow_influence: float = 1.0
    wuwa_specular_power: float = 1.0
    wuwa_toon_specular_cutoff: float = 0.1
    wuwa_specular_mask_cutoff: float = 0.5
    # Emission is only enabled by an explicit, game-owned colour binding.
    emission_source: str | None = None
    emission_strength: float = 1.0

    def __post_init__(self):
        if self.material_kind not in MATERIAL_KINDS:
            raise ValueError(
                f"Unknown material kind: {self.material_kind}")
        if self.normal_xy is not None:
            if (not isinstance(self.normal_xy, (tuple, list))
                    or len(self.normal_xy) != 2
                    or any(channel not in _CHANNELS
                           for channel in self.normal_xy)):
                raise ValueError(
                    "normal_xy must contain exactly two RGBA channels")
        if self.direct_shadow_model not in (
                None, "zzz_toon", "genshin_toon", "wuwa_base"):
            raise ValueError(
                f"Unknown direct shadow model: {self.direct_shadow_model}")
        if self.direct_specular_model not in (None, "wuwa_body"):
            raise ValueError(
                f"Unknown direct specular model: {self.direct_specular_model}")
        if self.emission_source not in (None, "emission_map_rgb"):
            raise ValueError(f"Unknown emission source: {self.emission_source}")

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
            "toon_specular_mask": (self.toon_specular_mask.to_metadata()
                                    if self.toon_specular_mask else None),
            "metal_route": (self.metal_route.to_metadata()
                             if self.metal_route else None),
            "direct_specular_model": self.direct_specular_model,
            "shadow_mask": (self.shadow_mask.to_metadata()
                            if self.shadow_mask else None),
            "material_id": (self.material_id.to_metadata()
                            if self.material_id else None),
            "material_id_decoder": self.material_id_decoder,
            "metalness": (self.metalness.to_metadata()
                          if self.metalness else None),
            "specular": (self.specular.to_metadata()
                          if self.specular else None),
            "specular_area": (self.specular_area.to_metadata()
                              if self.specular_area else None),
            "metalness_scale": self.metalness_scale,
            "specular_scale": self.specular_scale,
            "specular_influence": self.specular_influence,
            "toon_specular_shininess": self.toon_specular_shininess,
            "toon_specular_threshold_bias": self.toon_specular_threshold_bias,
            "toon_specular_softness": self.toon_specular_softness,
            "toon_specular_metal_cutoff": self.toon_specular_metal_cutoff,
            "shadow_threshold": self.shadow_threshold,
            "shadow_softness": self.shadow_softness,
            "shadow_level": self.shadow_level,
            "shadow_mask_strength": self.shadow_mask_strength,
            "shadow_influence": self.shadow_influence,
            "direct_shadow_model": self.direct_shadow_model,
            "wuwa_shadow_process": self.wuwa_shadow_process,
            "wuwa_shadow_front_offset": self.wuwa_shadow_front_offset,
            "wuwa_shadow_width": self.wuwa_shadow_width,
            "wuwa_shadow_mask_cutoff": self.wuwa_shadow_mask_cutoff,
            "wuwa_shadow_mask_endpoint_tolerance": (
                self.wuwa_shadow_mask_endpoint_tolerance),
            "wuwa_shadow_influence": self.wuwa_shadow_influence,
            "wuwa_specular_power": self.wuwa_specular_power,
            "wuwa_toon_specular_cutoff": self.wuwa_toon_specular_cutoff,
            "wuwa_specular_mask_cutoff": self.wuwa_specular_mask_cutoff,
            "emission_source": self.emission_source,
            "emission_strength": self.emission_strength,
        }
        return result


def _base_profile_for(game, texture_api):
    if game == "zzz" and texture_api in ("zzmi", "rabbitfx"):
        return MaterialInterpretation(
            id=f"zzz:{texture_api}", game=game, texture_api=texture_api,
            material_id=ChannelRef("material_map", "r"),
            # ZZZ toon diffuse currently uses N·L only. LightMap.G remains
            # the validated metallic input and must not be reused as a shadow
            # mask without new evidence.
            metalness=ChannelRef("light_map", "g"),
            specular=ChannelRef("material_map", "b"),
            shadow_threshold=0.5,
            shadow_softness=0.04,
            shadow_level=0.35,
            shadow_influence=0.45,
            direct_shadow_model="zzz_toon",
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
            shadow_threshold=0.5,
            shadow_softness=0.04,
            shadow_level=0.35,
            shadow_influence=0.45,
            direct_shadow_model="genshin_toon",
        )
    if game == "wuwa":
        # RabbitFX/WuWa's packed normal/material layout is retained for the
        # shader adapter. The conservative base keeps B/A diagnostic-only;
        # the exact reliable body specialization adds its own response refs.
        kwargs = {
            "normal_xy": ("r", "g"),
            "normal_data_b": ChannelRef("normal_data", "b"),
            "normal_data_a": ChannelRef("normal_data", "a"),
        }
        if texture_api == "rabbitfx":
            kwargs.update(
                shadow_mask=ChannelRef("light_map", "g"),
                direct_shadow_model="wuwa_base",
                emission_source="emission_map_rgb",
            )
        return MaterialInterpretation(
            id=f"wuwa:{texture_api}", game=game, texture_api=texture_api,
            **kwargs,
        )
    return None


def _specialized_profile_for(game, texture_api, material_kind):
    """Return a validated kind-specific profile, when one exists.

    Only reliable exact-kind selection reaches this hook.  The first
    production specialization is intentionally limited to WuWa RabbitFX
    body/cloth semantics; other kinds keep the conservative base profile.
    """
    if (game, texture_api, material_kind) != ("wuwa", "rabbitfx", "body"):
        return None
    base = _base_profile_for(game, texture_api)
    if base is None:
        return None
    return replace(
        base,
        id="wuwa:rabbitfx:body",
        material_kind="body",
        toon_specular_mask=ChannelRef("normal_data", "b"),
        metal_route=ChannelRef("normal_data", "a"),
        direct_specular_model="wuwa_body",
        wuwa_specular_power=1.0,
        wuwa_toon_specular_cutoff=0.1,
        wuwa_specular_mask_cutoff=0.5,
    )


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
