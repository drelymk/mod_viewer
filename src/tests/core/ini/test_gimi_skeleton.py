"""Stock GIMI skinning-pass palette proof regressions."""

import pytest

from core.ini.draw_groups import build_draw_groups
from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.gimi_skeleton import stock_gimi_palette_sources
from core.ini.sections import extract_resources, parse_sections
from tests.support.model_data import standard_component_resources

STOCK = r"""[Constants]
global $swapvar = 0
[TextureOverrideComponent01Position]
hash = 11111111
run = CommandListComponent01Position
[CommandListComponent01Position]
if $swapvar == 0
vb0 = ResourceComponent01Position
endif
[TextureOverrideComponent01Blend]
hash = 33333333
run = CommandListComponent01Blend
[CommandListComponent01Blend]
if $swapvar == 0
vb1 = ResourceComponent01Blend
handling = skip
draw = 4, 0
endif
[TextureOverrideComponent01Texcoord]
hash = 44444444
vb1 = ResourceComponent01Texcoord
[TextureOverrideComponent01A]
hash = 22222222
match_first_index = 0
ib = ResourceComponent01IB
run = CommandList\global\ORFix\ORFix
drawindexed = 6, 0, 0
[ResourceComponent01Blend]
type = Buffer
stride = 32
filename = blend.buf
"""


@pytest.mark.parametrize("change, accepted", [
    ("stock", True), ("variable_count", True), ("namespaced_count", True), ("texture_helper", True),
    ("no_skip", False), ("indexed_only", False), ("written", False), ("referenced", False),
    ("stride", False), ("vertex_shader", False), ("unknown_helper", False), ("unfiled", False),
])
def test_stock_gimi_palette_proof_requires_an_unchanged_skinning_pass(change, accepted):
    text = STOCK + standard_component_resources(position_stride=40)
    replacements = {
        "variable_count": ("draw = 4, 0", "draw = $count, 0"),
        "namespaced_count": ("draw = 4, 0", r"draw = $\mod01\count, 0"),
        "texture_helper": (r"run = CommandList\global\ORFix\ORFix", r"run = CommandList\GIMI\SetTextures"),
        "no_skip": ("handling = skip\n", ""),
        "indexed_only": ("draw = 4, 0", "drawindexed = 6, 0, 0"),
        "stride": ("stride = 32\nfilename = blend.buf", "stride = 40\nfilename = blend.buf"),
        "vertex_shader": ("ib = ResourceComponent01IB", "ib = ResourceComponent01IB\nvs-cb0 = ResourceOther"),
        "unknown_helper": (r"run = CommandList\global\ORFix\ORFix", r"run = CommandList\Effects\Run"),
        "unfiled": ("filename = blend.buf\n", ""),
    }
    if change in replacements:
        before, after = replacements[change]
        assert before in text
        text = text.replace(before, after)
    elif change == "written":
        text += "\n[CommandListOther]\nResourceComponent01Blend = copy ResourceOther\n"
    elif change == "referenced":
        text += "\n[CommandListOther]\nResourceOther = ref ResourceComponent01Blend\n"
    sections = parse_sections("source-01.ini", text=text)
    resources = extract_resources(sections)
    hashes, safe = stock_gimi_palette_sources(sections, resources, _scan_sections_for_draws(sections, resources=resources))
    source = build_draw_groups(sections, extract_resources(sections))[0]["draws"][0].skinning_source
    # A Blend without a 32-byte file never becomes a GIMI skin source at all.
    proven = source.native_palette_hashes if source is not None else None
    if change not in ("stride", "unfiled"):
        assert source is not None and source.encoding == "gimi_f32_u32_4"
    assert (proven == ("33333333",)) is accepted, (change, hashes, proven)
    if accepted:
        assert hashes == {"blend.buf": ("33333333",)} and "textureoverridecomponent01a" in safe
