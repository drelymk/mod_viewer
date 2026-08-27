"""Evidence-based game, runtime and texture-API detection.

The viewer only needs a conservative profile for texture interpretation.  This
module deliberately looks at the already parsed section projection rather than
raw files: comments, filenames and folder names must not be able to identify a
game.  Runtime, game and texture API are separate axes because WWMI and
RabbitFX, for example, commonly appear together but describe different
layers.
"""

from collections import defaultdict
from dataclasses import dataclass
import re

from ..ini.sections import line_source
from ..ini.texture_roles import _legacy_texture_evidence


@dataclass(frozen=True)
class GameEvidence:
    """One structural observation supporting a profile on one axis."""

    profile: str
    strength: int
    code: str
    source: dict | None = None


@dataclass(frozen=True)
class GameDetection:
    """Resolved mod-level identity used by the loader and texture pipeline."""

    game: str
    runtime: str
    texture_api: str
    confidence: str
    scores: dict[str, int]
    evidence: tuple[GameEvidence, ...] = ()

    def to_metadata(self):
        """Return the deliberately small JSON-safe public representation."""
        return {
            "id": self.game,
            "runtime": self.runtime,
            "texture_api": self.texture_api,
            "confidence": self.confidence,
        }


_RESOURCE_ROLE_RE = re.compile(
    r"^resource[\\/]([^\\/]+)[\\/]"
    r"(diffuse|normalmap|lightmap|materialmap)\b", re.I)
_RESOURCE_ASSIGN_RE = re.compile(
    r"^resource[\\/]([^\\/]+)[\\/]"
    r"(diffuse|normalmap|lightmap|materialmap)\b\s*=", re.I)
_COMMAND_TEXTURE_RE = re.compile(r"settextures", re.I)
_SET_TEXTURES_RUN_RE = re.compile(
    r"^run\s*=\s*commandlist[\\/]([^\\/]+)[\\/]settextures\b", re.I)
_DRAW_TYPE_ROUTE_RE = re.compile(
    r"\$?draw_type\s*==\s*(?:2|4)\b", re.I)
_VB_RE = re.compile(r"^vb([012])\s*=\s*(?:ref\s+)?(\S+)", re.I)
_CHECK_IB_RE = re.compile(r"^checktextureoverride\s*=\s*ib\b", re.I)
_DIRECT_TEXTURE_RE = re.compile(r"^ps-t\d+\s*=\s*(?:ref\s+)?(\S+)", re.I)
_WWMI_MARKER_RE = re.compile(
    r"(?:required[_\\]?wwmi[_\\]?version|object[_\\]?guid|"
    r"\\wwmi(?:v\d+)?\\)", re.I)
_SRMI_MARKER_RE = re.compile(
    r"(?:resource[\\/]srmi(?:v\d+)?[\\/]"
    r"(?:positionbuffer|blendbuffer|drawbuffer)\b|"
    r"\$\s*[\\/]?srmi(?:v\d+)?[\\/]"
    r"(?:vertex_count|vertcount)\b|"
    r"\bnamespace\s*=\s*srmi(?:v\d+)?\b)", re.I)


def _is_wwmi_structural(section, text):
    """Ignore asset filenames when looking for WWMI markers."""
    if re.match(r"filename\s*=", text, re.I):
        return False
    return bool(_WWMI_MARKER_RE.search(text)
                or _WWMI_MARKER_RE.search(str(section)))


def _is_srmi_structural(section, text):
    """Ignore asset filenames when looking for SRMI markers."""
    if re.match(r"filename\s*=", text, re.I):
        return False
    return bool(_SRMI_MARKER_RE.search(text)
                or _SRMI_MARKER_RE.search(str(section)))


def _source(section, line):
    source = line_source(line)
    if source:
        return source
    return {"section": section, "line": str(line).strip()}


def _iter_lines(sections):
    for section, lines in (sections or {}).items():
        for line in lines:
            text = str(line).strip()
            if not text or text.startswith((";", "#")):
                continue
            yield section, line, text


def _add(evidence, profile, strength, code, section, line):
    evidence.append(GameEvidence(
        profile=profile, strength=strength, code=code,
        source=_source(section, line)))


def _section_is(section, token):
    return token in str(section).lower()


def _binding_is_blend(section, value, sections):
    """Whether a vertex binding structurally names a blend resource."""
    value_low = value.lower()
    if "blend" in value_low:
        return True
    target = value_low
    if target == "ref":
        return False
    for name in sections:
        if str(name).lower() == target:
            # Once the binding target resolves, its resource name is the
            # authoritative signal. Do not let a parent `...Blend` override
            # turn an explicitly resolved Texcoord resource into a blend.
            return "blend" in str(name).lower()
    return "blend" in str(section).lower()


def _command_texture_namespace(section):
    """Return the known namespace embedded in a SetTextures section name."""
    lower = str(section).lower()
    if not lower.startswith("commandlist") or not _COMMAND_TEXTURE_RE.search(lower):
        return None
    for namespace in ("rabbitfx", "gimi", "zzmi", "wwmi"):
        if namespace in lower:
            return namespace
    return None


def _resource_namespace(value, pattern=_RESOURCE_ROLE_RE):
    match = pattern.match(str(value))
    return match.group(1).lower() if match else None


def _legacy_direct_texture_item(items, sections):
    """Return one validated classic direct texture binding, if present."""
    section_lookup = {
        str(name).casefold(): name for name in (sections or {})
    }
    for section, line, text in items:
        match = _DIRECT_TEXTURE_RE.match(text)
        if not match:
            continue
        if _legacy_texture_evidence(
                match.group(1), sections, section_lookup):
            return section, line
    return None


def _zzmi_texture_item(items):
    """Return one real ZZMI texture/API observation, if present."""
    for section, line, text in items:
        if _command_texture_namespace(section) == "zzmi":
            return section, line, text
        run_match = _SET_TEXTURES_RUN_RE.match(text)
        if run_match and run_match.group(1).lower() == "zzmi":
            return section, line, text
        if (_resource_namespace(section) == "zzmi"
                or _resource_namespace(text, _RESOURCE_ASSIGN_RE) == "zzmi"):
            return section, line, text
        direct_match = _DIRECT_TEXTURE_RE.match(text)
        if direct_match and _resource_namespace(direct_match.group(1)) == "zzmi":
            return section, line, text
    return None


def _add_settextures_evidence(game, runtime, texture_api, namespace,
                               section, line):
    """Add one command-list API observation across the relevant axes."""
    if namespace == "rabbitfx":
        _add(texture_api, "rabbitfx", 45, "rabbitfx_settextures", section, line)
    elif namespace == "gimi":
        _add(texture_api, "gimi", 45, "gimi_settextures", section, line)
        _add(game, "genshin", 45, "gimi_settextures", section, line)
        _add(runtime, "gimi", 45, "gimi_settextures", section, line)
    elif namespace == "zzmi":
        _add(texture_api, "zzmi", 45, "zzmi_settextures", section, line)
        _add(game, "zzz", 45, "zzmi_settextures", section, line)
        _add(runtime, "zzmi", 45, "zzmi_settextures", section, line)
    elif namespace == "wwmi":
        _add(texture_api, "raw", 20, "wwmi_settextures", section, line)


def _add_resource_namespace_evidence(game, runtime, texture_api, namespace,
                                     section, line, code):
    """Add weak resource-namespace evidence without assigning a game."""
    if namespace == "rabbitfx":
        _add(texture_api, "rabbitfx", 30, code, section, line)
    elif namespace == "gimi":
        _add(texture_api, "gimi", 18, code, section, line)
        _add(game, "genshin", 8, code, section, line)
        _add(runtime, "gimi", 8, code, section, line)
    elif namespace == "zzmi":
        _add(texture_api, "zzmi", 18, code, section, line)
        _add(game, "zzz", 8, code, section, line)
        _add(runtime, "zzmi", 8, code, section, line)
    elif namespace == "wwmi":
        _add(texture_api, "raw", 12, code, section, line)


def collect_game_evidence(sections, resources=None):
    """Collect ``(game, runtime, texture_api)`` evidence lists.

    Evidence codes are intentionally coarse and are later de-duplicated by
    code during resolution.  A mod with dozens of ``Resource\\GIMI`` lines
    therefore does not overpower one genuinely strong runtime marker.
    """
    del resources  # reserved for future structural resource metadata
    game, runtime, texture_api = [], [], []
    items = list(_iter_lines(sections))

    # WWMI globals and registration variables are the strongest available
    # runtime signal.  They identify the framework, and the supported corpus
    # maps that runtime to WuWa without relying on names or filenames.
    wwmi_item = next(
        (item for item in items if _is_wwmi_structural(item[0], item[2])), None)
    if wwmi_item is None:
        for section, lines in (sections or {}).items():
            if _WWMI_MARKER_RE.search(str(section)):
                line = next(iter(lines), section)
                wwmi_item = (section, line, str(line))
                break
    if wwmi_item:
        section, line, _text = wwmi_item
        _add(game, "wuwa", 100, "wwmi_runtime_marker", section, line)
        _add(runtime, "wwmi", 100, "wwmi_runtime_marker", section, line)

    # SRMI has an explicit namespace and runtime-owned buffer vocabulary. It
    # must be resolved before generic DRAW_TYPE/vb2 heuristics can be applied;
    # otherwise modern HSR templates look like ZZZ mods.
    srmi_item = next(
        (item for item in items if _is_srmi_structural(item[0], item[2])), None)
    if srmi_item is None:
        for section, lines in (sections or {}).items():
            if _SRMI_MARKER_RE.search(str(section)):
                line = next(iter(lines), section)
                srmi_item = (section, line, str(line))
                break
    if srmi_item:
        section, line, _text = srmi_item
        _add(game, "hsr", 100, "srmi_runtime_marker", section, line)
        _add(runtime, "srmi", 100, "srmi_runtime_marker", section, line)
        _add(texture_api, "raw", 20, "srmi_runtime_marker", section, line)

    draw_type_route_item = next(
        (item for item in items if _DRAW_TYPE_ROUTE_RE.search(item[2])), None)
    has_vb2_blend = False
    has_vb1_blend = False
    vb2_item = None
    vb1_item = None
    for section, line, text in items:
        match = _VB_RE.match(text)
        if not match:
            continue
        index, value = match.groups()
        if index == "2" and _binding_is_blend(section, value, sections):
            has_vb2_blend = True
            vb2_item = (section, line, text)
        if index == "1" and _binding_is_blend(section, value, sections):
            has_vb1_blend = True
            vb1_item = (section, line, text)

    check_ib_item = next(
        (item for item in items if _CHECK_IB_RE.match(item[2])), None)

    # DRAW_TYPE + vb2=Blend is shared by SRMI and other modern templates. A
    # ZZZ classification needs its 2/4 route plus the other ZZMI-specific
    # structure, including checktextureoverride and a ZZMI texture binding.
    zzmi_texture_item = _zzmi_texture_item(items)
    if (not srmi_item and draw_type_route_item and has_vb2_blend
            and check_ib_item and zzmi_texture_item):
        section, line, _text = draw_type_route_item
        _add(game, "zzz", 90, "zzz_draw_type_vb2_blend", section, line)
        _add(runtime, "zzmi", 80, "zzz_draw_type_vb2_blend", section, line)
        if vb2_item:
            _add(runtime, "zzmi", 80, "zzmi_vb2_blend_binding",
                 vb2_item[0], vb2_item[1])
        _add(runtime, "zzmi", 60, "zzz_draw_type_checktextureoverride",
             check_ib_item[0], check_ib_item[1])

    # Classic GIMI routing is the combination, not any one resource name:
    # Position/Blend/Texcoord sections with the blend buffer on vb1.
    has_position_section = any(_section_is(section, "position")
                               for section, _line, _text in items)
    has_blend_section = any(_section_is(section, "blend")
                            for section, _line, _text in items)
    has_texcoord_section = any(_section_is(section, "texcoord")
                               for section, _line, _text in items)
    classic_gimi_routing = (
        has_position_section and has_blend_section and has_texcoord_section
        and has_vb1_blend)
    if classic_gimi_routing:
        section, line, _text = vb1_item
        _add(game, "genshin", 75, "gimi_position_blend_texcoord", section, line)
        _add(runtime, "gimi", 65, "gimi_position_blend_texcoord", section, line)

        legacy_texture_item = _legacy_direct_texture_item(items, sections)
        if legacy_texture_item:
            _add(texture_api, "gimi", 32,
                 "gimi_classic_direct_textures",
                 legacy_texture_item[0], legacy_texture_item[1])

    # SetTextures sections are useful API evidence.  A command-list API is
    # stronger than a bare Resource namespace, but still does not use a
    # filename or folder name as a game signal.
    for section, lines in (sections or {}).items():
        namespace = _command_texture_namespace(section)
        if not namespace:
            continue
        line = next(iter(lines), section)
        _add_settextures_evidence(game, runtime, texture_api, namespace,
                                  section, line)

    # Real mods normally invoke the command list from an override body rather
    # than exposing a section named CommandList\...\SetTextures.  Inspect the
    # parsed statement itself so detection does not depend on synthetic
    # section names.
    for section, line, text in items:
        match = _SET_TEXTURES_RUN_RE.match(text)
        if not match:
            continue
        namespace = match.group(1).lower()
        _add_settextures_evidence(game, runtime, texture_api, namespace,
                                  section, line)

    # Texture role resource namespaces identify a binding API, but are weak
    # game evidence by design.  In particular, RabbitFX alone never forces
    # WuWa and a GIMI resource line alone never forces Genshin.
    seen_resource_namespaces = set()
    for section, line, _text in items:
        namespace = _resource_namespace(section)
        if namespace is None:
            namespace = _resource_namespace(_text, _RESOURCE_ASSIGN_RE)
            code = f"{namespace}_resource_assignment" if namespace else None
        else:
            code = f"{namespace}_resource_namespace"
        if not namespace:
            continue
        if namespace in seen_resource_namespaces:
            continue
        seen_resource_namespaces.add(namespace)
        _add_resource_namespace_evidence(
            game, runtime, texture_api, namespace, section, line, code)

    direct_item = next(
        (item for item in items if _DIRECT_TEXTURE_RE.match(item[2])), None)
    if direct_item:
        direct_match = _DIRECT_TEXTURE_RE.match(direct_item[2])
        direct_value = direct_match.group(1) if direct_match else ""
        namespace = _resource_namespace(direct_value)
        if namespace:
            if namespace == "rabbitfx":
                _add(texture_api, "rabbitfx", 30,
                     "rabbitfx_direct_binding", direct_item[0], direct_item[1])
            elif namespace == "gimi":
                _add(texture_api, "gimi", 18,
                     "gimi_direct_binding", direct_item[0], direct_item[1])
            elif namespace == "zzmi":
                _add(texture_api, "zzmi", 18,
                     "zzmi_direct_binding", direct_item[0], direct_item[1])
    if direct_item and not texture_api:
        _add(texture_api, "raw", 15, "direct_ps_texture_binding",
             direct_item[0], direct_item[1])

    return game, runtime, texture_api


def _scores(evidence):
    """Score each profile, counting each evidence code at most once."""
    best = {}
    for item in evidence:
        key = (item.profile, item.code)
        best[key] = max(best.get(key, 0), int(item.strength))
    scores = defaultdict(int)
    for (profile, _code), strength in best.items():
        scores[profile] += strength
    return dict(scores)


def _resolve_axis(evidence, unknown, *, minimum, lead_required):
    scores = _scores(evidence)
    if not scores:
        return unknown
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    winner, score = ordered[0]
    runner_score = ordered[1][1] if len(ordered) > 1 else 0
    if score < minimum or score - runner_score < lead_required:
        return unknown
    return winner


def resolve_game_detection(game_evidence=(), runtime_evidence=(),
                            texture_api_evidence=()):
    """Resolve aggregate evidence into one conservative ``GameDetection``."""
    game_scores = _scores(game_evidence)
    ordered = sorted(game_scores.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        game = "unknown"
        confidence = "low"
    else:
        winner, score = ordered[0]
        runner_score = ordered[1][1] if len(ordered) > 1 else 0
        lead = score - runner_score
        if score < 20 or lead < 10:
            game = "unknown"
            confidence = "low"
        elif score >= 70 and lead >= 25:
            game = winner
            confidence = "high"
        elif score >= 30 and lead >= 15:
            game = winner
            confidence = "medium"
        else:
            game = winner
            confidence = "low"

    runtime = _resolve_axis(runtime_evidence, "unknown", minimum=25,
                            lead_required=10)
    texture_api = _resolve_axis(texture_api_evidence, "unknown", minimum=15,
                                 lead_required=5)
    return GameDetection(
        game=game,
        runtime=runtime,
        texture_api=texture_api,
        confidence=confidence,
        scores=game_scores,
        evidence=tuple(game_evidence) + tuple(runtime_evidence)
        + tuple(texture_api_evidence),
    )


def detect_game(sections, resources=None):
    """Convenience wrapper for direct callers and small fixtures."""
    game, runtime, texture_api = collect_game_evidence(sections, resources)
    return resolve_game_detection(game, runtime, texture_api)
