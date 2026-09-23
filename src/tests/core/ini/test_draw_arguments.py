"""Constant draw ranges and finite cycle guards preserve authored state."""

import operator

import pytest

from core.ini.analysis import analyze_ini
from core.ini.dnf import build_bool_alias_map, normalize_dnf, parse_condition_dnf
from core.ini.draw_arguments import immutable_draw_constants, resolve_drawindexed
from core.ini.health import analyze_mod
from core.ini.sections import parse_sections


def _visible(conditions, **state):
    return not conditions or any(all(
        (str(state[clause["var"]]) == clause["value"]) != clause["negate"]
        for clause in group) for group in conditions)


@pytest.mark.parametrize("op,compare", [
    ("<", operator.lt), ("<=", operator.le),
    (">", operator.gt), (">=", operator.ge),
])
@pytest.mark.parametrize("threshold", [-3, -1, 0.5, 2, 4])
def test_numeric_cycle_comparisons_and_negation(op, compare, threshold):
    sections = parse_sections("fixture.ini", text="""[Constants]
global persist $Style = -2
[KeyStyle]
type = cycle
$Style = -1, 0.5, 2
""")
    sections["CommandListCompare"] = [f"$allowed = ($STYLE {op} {threshold})"]
    aliases = build_bool_alias_map(sections)
    for expression, invert in [(f"$STYLE {op} {threshold}", False),
                               (f"!($STYLE {op} {threshold})", True),
                               ("$allowed", False)]:
        conditions = normalize_dnf(
            parse_condition_dnf(expression, aliases), {"Style"})
        for value in [-2, -1, 0.5, 2]:
            assert _visible(conditions, Style=value) == (compare(value, threshold) != invert)


@pytest.mark.parametrize("declaration,mutation,arguments,expected", [
    ("global $Count = 3", "", "$COUNT, 0, -2", (3, 0, -2)),
    ("global $Count = 0", "", "$Count, 0, 0", (0, 0, 0)),
    ("global persist $Count = 3", "", "$Count, 0, 0", None),
    ("global $Count = 3", "$count = 6", "$Count, 0, 0", None),
    ("global $Count = 3", "post $COUNT = 6", "$Count, 0, 0", None),
    ("global $Count = 3", "$Count += 3", "$Count, 0, 0", None),
    ("global $Count = 3", r"$\Provider\Count = 6", "$Count, 0, 0", None),
    ("global $Count = 3\nglobal $count = 3", "", "$Count, 0, 0", None),
    ("if $enabled\nglobal $Count = 3\nendif", "", "$Count, 0, 0", None),
    ("global $Count = 3 + 3", "", "$Count, 0, 0", None),
    ("global $Count = -3", "", "$Count, 0, 0", None),
    ("global $Count = 3", "", "3, -1, 0", None),
    ("global $Count = 3", "", "$Count + 3, 0, 0", None),
    ("global $Count = 3", "", r"$\Other\Count, 0, 0", None),
    ("global $Count = 3", "", "$Missing, 0, 0", None),
])
def test_draw_argument_resolution_is_conservative(declaration, mutation,
                                                 arguments, expected):
    sections = parse_sections("fixture.ini", text=(
        f"[Constants]\n{declaration}\n[Present]\n{mutation}\n"))
    assert resolve_drawindexed(arguments, immutable_draw_constants(sections)) == expected


def test_constant_draw_lifecycle_keeps_guards_textures_sources_and_diagnostics(tmp_path):
    text = r"""[Constants]
global $Count = 3
global $Offset = 3
global persist $Top = 0
global persist $Skirt = 0
[KeyTop]
type = cycle
$Top = 0, 1, 2
[KeySkirt]
type = cycle
$Skirt = 0, 1
[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceBodyIB
run = CommandListBody
[CommandListBody]
Resource\ZZMI\Diffuse = ref ResourceOriginal
if $skirt == 0 && $top < 2
drawindexed = $COUNT, 0, 0
elif $top >= 2
Resource\ZZMI\Diffuse = ref ResourceAlternate
drawindexed = $Count, $Offset, 0
else
drawindexed = 3, 6, -1
endif
[ResourcePosition]
filename = position.buf
stride = 40
[ResourceTexcoord]
filename = texcoord.buf
stride = 20
[ResourceBodyIB]
filename = body.ib
format = DXGI_FORMAT_R32_UINT
[ResourceOriginal]
filename = original.dds
[ResourceAlternate]
filename = alternate.dds
"""
    path = tmp_path / "mod.ini"
    path.write_text(text)
    sections = parse_sections(str(path))
    result = analyze_ini(sections, var_prefix="Mod::")
    draws = result.draw_groups[0]["draws"]
    assert [(draw.count, draw.start, draw.base) for draw in draws] == [
        (3, 0, 0), (3, 3, 0), (3, 6, -1)]
    for top in range(3):
        for skirt in range(2):
            visible = [i for i, draw in enumerate(draws)
                       if _visible(draw.conditions, **{"Mod::Top": top, "Mod::Skirt": skirt})]
            assert visible == ([0] if skirt == 0 and top < 2 else [1] if top >= 2 else [2])
    assert draws[0].texture_default_file == "original.dds"
    for top in range(3):
        assignments = draws[1].texture_assignments
        applied = [item["file"] for item in assignments if _visible(
            item["conditions"], **{"Mod::Top": top, "Mod::Skirt": 0})]
        assert applied[-1] == ("alternate.dds" if top >= 2 else "original.dds")
    assert [draw.occurrence.ordinal for draw in draws] == [0, 1, 2]
    for draw in draws:
        assert draw.sources[0]["section"] == "CommandListBody"
        assert text.splitlines()[draw.sources[0]["line_no"] - 1].startswith("drawindexed")

    def unsupported(overrides=None):
        return [issue for issue in analyze_mod(str(tmp_path), overrides=overrides)["issues"]
                if issue["code"] == "unsupported_drawindexed_arguments"]

    assert unsupported() == []
    staged = text + "\n[Present]\n$Count = 6\n"
    staged_result = analyze_ini(parse_sections(str(path), text=staged))
    assert [(draw.count, draw.start) for draw in staged_result.draw_groups[0]["draws"]] == [(3, 6)]
    issues = unsupported({str(path): staged})
    assert len(issues) == 2
    assert all("skipped" in issue["message"] for issue in issues)
    assert path.read_text() == text

    # An unresolved explicit range must not become an entire-buffer draw.
    only_unresolved = staged.replace("drawindexed = 3, 6, -1", "drawindexed = $Count, 6, -1")
    assert analyze_ini(parse_sections(str(path), text=only_unresolved)).draw_groups == []
    # The same variable spelling in another INI has its own constant scope.
    other = analyze_ini(parse_sections("other.ini", text=text.replace("global $Count = 3", "global $Count = 6")))
    assert other.draw_groups[0]["draws"][0].count == 6
    assert result.draw_groups[0]["draws"][0].count == 3
