"""Regression coverage for the unified variable/control analysis path."""

import json
from pathlib import Path

from app.mods.analysis import analyze_mod_inis
from app.mods.controls import load_control_state
from app.mods.loader import ModLoadContext
from core.ini.control_graph import RenderEffect, build_control_graph
from core.ini.dnf import dnf_not, parse_condition_dnf
from core.ini.analysis import analyze_ini
from core.ini.program import scan_program
from core.ini.texture_roles import _condition_group_is_consistent
from core.ini.variables import VariableId, VariableResolver, source_from_path
from core.ini.menu import attach_menu_images, extract_menu_toggles
from core.editing.present import (
    capturable_variables, add as add_present, details as present_details,
)
from core.ini.document import IniDocument


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_namespace_resolution_and_exact_forwarding(tmp_path):
    model = _write(tmp_path / "Model.ini", r"""namespace = cx_Mod049

[Constants]
global $key_1 = 1

[TextureOverrideBody]
if $key_1 == 1
    drawindexed = 3, 0, 0
endif
""")
    menu = _write(tmp_path / "Menu.ini", r"""[Constants]
global persist $key_1 = 0

[KeyClick]
key = x
type = cycle
$key_1 = 0,1

[Present]
$\cx_Mod049\key_1 = $key_1
""")
    sources = [source_from_path(model, str(tmp_path)),
               source_from_path(menu, str(tmp_path))]
    resolver = VariableResolver(sources)
    model_var = resolver.resolve("$key_1", sources[0], "Constants")
    menu_var = resolver.resolve("$key_1", sources[1], "Constants")
    qualified = resolver.resolve(r"$\CX_MOD049\KEY_1", sources[1], "Present")

    assert model_var == qualified == VariableId("namespace", "cx_mod049", "key_1")
    assert menu_var != model_var
    facts = scan_program(sources[1], resolver)
    forwarding = next(write for write in facts.writes
                      if write.section == "Present")
    assert forwarding.target == model_var
    assert forwarding.dependencies == (menu_var,)
    assert forwarding.exact_copy is True


def test_unified_graph_discovers_namespaced_interactive_control(tmp_path):
    model = _write(tmp_path / "Model.ini", r"""namespace = fixture_shared

[Constants]
global $key_1 = 0
global $key_2 = 0

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $key_1 > 0
    drawindexed = 3, 0, 0
endif
if $key_2 == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    menu = _write(tmp_path / "Menu.ini", r"""[Constants]
global $check1 = 0
global persist $value_2 = 1
global persist $key_1 = 3
global persist $value_1 = 0

[KeyClick]
key = VK_LBUTTON
type = cycle
$check1 = 0,1

[Present]
run = CommandListUpdateButtons
if $key_1 > 3
    $key_1 = 0
else
    $key_1 = $key_1 + $value_1
endif
$value_1 = 0
$\fixture_shared\key_1 = $key_1
$\fixture_shared\key_2 = $value_2

[CommandListUpdateButtons]
if $check1 == 1
    if cursor_x > 0.1 && cursor_x < 0.2
        $value_1 = 1 - $value_1
    endif
    if cursor_x > 0.2 && cursor_x < 0.3
        $value_2 = 1 - $value_2
    endif
endif
""")

    parsed = analyze_mod_inis([model, menu], str(tmp_path))
    controls = parsed.control_projection

    assert {info["var"].casefold() for info in controls["menu"].values()} == {
        "fixture_shared/key_1", "fixture_shared/key_2",
    }
    assert len(controls["menu"]) == 2
    by_var = {info["var"].casefold(): info
              for info in controls["menu"].values()}
    assert by_var["fixture_shared/key_1"]["domain"] == {
        "kind": "discrete", "values": ["0", "1", "2", "3"],
    }
    assert by_var["fixture_shared/key_2"]["domain"] == {
        "kind": "discrete", "values": ["0", "1"],
    }
    assert all("interactive" in info["controllers"]
               and "direct_key" not in info["controllers"]
               and "action" not in info
               for info in by_var.values())
    assert not controls["toggles"]
    assert all(info["capture_bindings"] for info in by_var.values())
    state = load_control_state(ModLoadContext(str(tmp_path), [model, menu]))
    assert set(info["var"] for info in state["controls"]["menu"].values()) == {
        "fixture_shared/key_1", "fixture_shared/key_2",
    }


def test_reserved_present_is_not_a_toggle_when_it_repeats_render_state(tmp_path):
    path = _write(tmp_path / "present.ini", r"""[Constants]
global persist $Outfit = 0

[KeyOutfit]
key = o
type = cycle
$Outfit = 0,1

[KeyModViewerPresent]
key = p
type = cycle
$Outfit = 0,1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Outfit == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))

    assert "KeyOutfit" in parsed.control_projection["toggles"]
    assert "KeyModViewerPresent" not in parsed.control_projection["toggles"]
    assert parsed.present["item"]["count"] == 2


def test_present_sequences_keep_duplicate_positions_and_only_real_mismatch_fails(
        tmp_path):
    first = _write(tmp_path / "first.ini", r"""[Constants]
global persist $A = 1
global persist $B = 1
global persist $C = 0
global persist $D = 0

[KeyModViewerPresent]
key = ]
back = [
type = cycle
$A = 1,0,1
$B = 1,1,1
$C = 0,0,0
$D = 0,1,0
""")
    details = present_details(IniDocument.load(first))
    assert details["count"] == 3
    assert details["vars"] == {
        "A": ["1", "0", "1"],
        "B": ["1", "1", "1"],
        "C": ["0", "0", "0"],
        "D": ["0", "1", "0"],
    }

    second = _write(tmp_path / "second.ini", r"""[Constants]
global persist $A = 0
global persist $B = 0

[KeyModViewerPresent]
key = ]
back = [
type = cycle
$A = 0,1,0
$B = 0,0,0
""")
    aligned = analyze_mod_inis([first, second], str(tmp_path)).present["item"]
    assert aligned["count"] == 3
    assert aligned["sync_error"] is None

    Path(second).write_text(Path(second).read_text(encoding="utf-8")
                            .replace("$B = 0,0,0", "$B = 0,0"),
                            encoding="utf-8")
    mismatch = analyze_mod_inis([first, second], str(tmp_path)).present["item"]
    assert mismatch["count"] == 0
    assert "different position counts" in mismatch["sync_error"]


def test_modeled_state_keeps_safe_present_derivation_and_drops_runtime_wrappers(
        tmp_path):
    path = _write(tmp_path / "derived.ini", r"""[Constants]
global persist $Outfit = 0
global $Piece = 0
global $object_detected = 0

[KeyOutfit]
key = o
type = cycle
$Outfit = 0,1

[Present]
if $Outfit == 1
    $Piece = 1
else
    $Piece = 0
endif

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $object_detected
    if $Piece == 1
        Resource\ZZMI\Diffuse = ResourceBodyOn
        drawindexed = 3, 0, 0
    else
        Resource\ZZMI\Diffuse = ResourceBodyOff
        drawindexed = 3, 0, 0
    endif
endif
[ResourceBodyOn]
filename = body-on.dds
[ResourceBodyOff]
filename = body-off.dds
[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    modeled = {variable.name for variable in
               parsed.control_graph.modeled_state_variables}

    assert {"outfit", "piece"} <= modeled
    assert "object_detected" not in modeled
    assert set(parsed.defaults) == {"Outfit", "Piece"}
    assert all(clause["var"].casefold() != "object_detected"
               for group in parsed.groups for draw in group["draws"]
               for branch in draw.conditions for clause in branch)
    assert all(clause["var"].casefold() != "object_detected"
               for group in parsed.groups for draw in group["draws"]
               for variant in draw.texture_rules("diffuse")
               for branch in variant["conditions"] for clause in branch)
    assert {rule["var"] for rule in parsed.state_rules} == {"Piece"}


def test_selector_image_conflict_fails_closed(tmp_path):
    source = {
        "ImageA": ["if $slot == 1", "ps-t100 = ResourceA", "endif"],
        "ImageB": ["if $slot == 1", "ps-t100 = ResourceB", "endif"],
    }
    resources = {
        "ResourceA": {"filename": "menu/a.dds"},
        "ResourceB": {"filename": "menu/b.dds"},
    }
    menu = {"one": {
        "name": "State", "var": "State", "selector": {
            "var": "clickedSlot", "value": "1",
        }, "_selector_names": ["clickedSlot", "slot"],
    }}

    attach_menu_images(menu, source, resources)
    assert "image_file" not in menu["one"]


def test_key_run_closures_do_not_cross_contaminate_sibling_keys(tmp_path):
    path = _write(tmp_path / "closures.ini", r"""[Constants]
global persist $Hair = 0
global persist $Dress = 0

[KeyHair]
key = h
run = CommandListHair

[KeyDress]
key = d
run = CommandListDress

[CommandListHair]
$Hair = 1 - $Hair

[CommandListDress]
$Dress = 1 - $Dress

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Hair == 1
    drawindexed = 3, 0, 0
endif
[TextureOverrideDress]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Dress == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    hair = resolver.resolve("$Hair", source, "Constants")
    dress = resolver.resolve("$Dress", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("visibility", (hair,)),
                  RenderEffect("visibility", (dress,))])

    by_trigger = {action.trigger: action for action in graph.actions
                  if action.trigger in {"h", "d"}}
    assert [write.target for write in by_trigger["h"].assignments] == [hair]
    assert [write.target for write in by_trigger["d"].assignments] == [dress]


def test_input_root_can_reach_present_menu_without_becoming_a_toggle(tmp_path):
    path = _write(tmp_path / "present-menu.ini", r"""[Constants]
global $mouse_clicked
global persist $Hair = 0

[KeyMouse]
key = m
$mouse_clicked = 1

[Present]
if $mouse_clicked
    run = CommandListSetHair
endif

[CommandListSetHair]
$Hair = 1 - $Hair

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Hair == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))

    assert "KeyMouse" not in parsed.control_projection["toggles"]
    hair = next(item for item in parsed.control_projection["menu"].values()
                if item["var"] == "Hair")
    assert "interactive" in hair["controllers"]


def test_selector_branches_keep_assignments_and_aliases_separate(tmp_path):
    path = _write(tmp_path / "selector.ini", r"""[Constants]
global persist $Hair = 0
global persist $Dress = 0
global $slot
global $hoveredSlot
global $clickedSlot

[CommandListClickedSlot]
$hoveredSlot = $slot
$clickedSlot = $hoveredSlot
if $clickedSlot == 1
    $Hair = 1 - $Hair
elif $clickedSlot == 2
    $Dress = 1 - $Dress
endif

[CommandListSlotItemImage]
if $slot == 1
    ps-t100 = ResourceMenuItem.1
elif $slot == 2
    ps-t100 = ResourceMenuItem.2
endif

[ResourceMenuItem.1]
filename = hair.dds
[ResourceMenuItem.2]
filename = dress.dds

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Hair == 1
    drawindexed = 3, 0, 0
endif
[TextureOverrideDress]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Dress == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    actions = [action for action in parsed.control_graph.actions
               if action.trigger == "CommandListClickedSlot"]
    assert {(action.selector["value"], action.writes[0].name)
            for action in actions} == {("1", "hair"), ("2", "dress")}
    assert {action.selector["var"].name for action in actions} == {
        "clickedslot"}
    menu = parsed.control_projection["menu"]
    assert next(item for item in menu.values() if item["var"] == "Hair")[
        "image_file"] == "hair.dds"
    assert next(item for item in menu.values() if item["var"] == "Dress")[
        "image_file"] == "dress.dds"


def test_generic_selector_projection_does_not_regress_legacy_menu_contract(
        tmp_path):
    path = _write(tmp_path / "selector-oracle.ini", r"""[Constants]
global persist $Hair = 0
global persist $Dress = 0
global $slot
global $clickedSlot

[CommandListClickedSlot]
$clickedSlot = $slot
if $clickedSlot == 1
    $Hair = 1 - $Hair
elif $clickedSlot == 2
    $Dress = 1 - $Dress
endif

[CommandListSlotItemImage]
if $slot == 1
    ps-t100 = ResourceMenuItem.1
elif $slot == 2
    ps-t100 = ResourceMenuItem.2
endif
[ResourceMenuItem.1]
filename = hair.dds
[ResourceMenuItem.2]
filename = dress.dds

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Hair == 1
    drawindexed = 3, 0, 0
endif
[TextureOverrideDress]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Dress == 1
    drawindexed = 3, 0, 0
endif
[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    source = source_from_path(path, str(tmp_path))
    legacy = extract_menu_toggles(source.sections)
    attach_menu_images(legacy, source.sections, source.resources)
    parsed = analyze_mod_inis([path], str(tmp_path))
    generic = {item["var"].casefold(): item
               for item in parsed.control_projection["menu"].values()}

    assert {item["var"].casefold() for item in legacy.values()} <= set(generic)
    for item in legacy.values():
        projected = generic[item["var"].casefold()]
        assert projected["domain"]["values"] == item["values"]
        assert projected.get("image_file") == item.get("image_file")


def test_shape_program_scan_preserves_authored_state():
    source = source_from_path(
        "shape.ini", "",
        text=r"""[Constants]
global persist $ShapeValue = 0.5

[CustomShaderComputeShapes]
x88 = $ShapeValue
cs-t50 = copy ResourcePosition.Base
cs-t51 = copy ResourcePosition.Target

[ResourcePosition.Base]
filename = base.buf
stride = 40
[ResourcePosition.Target]
filename = target.buf
stride = 40
""")
    facts = scan_program(source)
    assert facts.declarations[0].var.name == "shapevalue"


def test_shape_effect_is_a_generic_continuous_control(tmp_path):
    path = _write(tmp_path / "shape.ini", r"""[Constants]
global persist $ShapeValue = 0.5

[CustomShaderComputeShapes]
x88 = $ShapeValue
cs-t50 = copy ResourcePosition.Base
cs-t51 = copy ResourcePosition.Target

[ResourcePosition.Base]
filename = base.buf
stride = 40
[ResourcePosition.Target]
filename = target.buf
stride = 40
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    control = next(iter(parsed.control_projection["menu"].values()))
    assert control["domain"]["kind"] == "continuous"
    assert control["domain"]["min"] == 0.0
    assert control["domain"]["max"] == 1.0


def test_shape_control_keeps_continuous_menu_domain_with_key_cycle(tmp_path):
    path = _write(tmp_path / "shape-key.ini", r"""[Constants]
global persist $ShapeValue = 0.5

[KeyShape]
key = s
type = cycle
$ShapeValue = 0,1

[CustomShaderComputeShapes]
x88 = $ShapeValue
cs-t50 = copy ResourcePosition.Base
cs-t51 = copy ResourcePosition.Target

[ResourcePosition.Base]
filename = base.buf
stride = 40
[ResourcePosition.Target]
filename = target.buf
stride = 40
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    menu = next(iter(parsed.control_projection["menu"].values()))
    assert menu["domain"]["kind"] == "continuous"
    assert "KeyShape" in parsed.control_projection["toggles"]


def test_graph_infers_wrapped_increment_domain_and_preserves_post(tmp_path):
    path = _write(tmp_path / "toggle.ini", r"""[Constants]
global persist $Hair = 0

[KeyHair]
key = h
type = cycle
$Hair = $Hair + 1
if $Hair >= 3
    $Hair = 0
endif
post $Hair = $Hair
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    hair = resolver.resolve("$Hair", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("visibility", (hair,))])

    assert graph.controls[hair.key].domain == {
        "kind": "discrete", "values": ["0", "1", "2"],
    }
    assert [write.phase for write in facts.writes][-1] == "post"
    assert len(graph.controls) == 1


def test_target_current_interpolation_is_one_continuous_control(tmp_path):
    path = _write(tmp_path / "shape.ini", r"""[Constants]
global persist $currFlat = 0
global persist $rangeFlat = 0

[CommandListDrag]
$currFlat = $currFlat + (($rangeFlat - $currFlat) / $td) * $dt
post $rangeFlat = $currFlat
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    current = resolver.resolve("$currFlat", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("shape", (current,))], shape_vars=(current,))

    assert set(graph.controls) == {current.key}
    assert graph.controls[current.key].domain["kind"] == "continuous"


def test_direct_and_interactive_paths_share_one_control(tmp_path):
    path = _write(tmp_path / "menu.ini", r"""[Constants]
global persist $Hair = 0

[KeyHair]
key = h
type = cycle
$Hair = 0,1

[CommandListClick]
$Hair = 1 - $Hair
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    hair = resolver.resolve("$Hair", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (hair,))])

    control = graph.controls[hair.key]
    assert [controller.kind for controller in control.controllers] == [
        "direct_key", "interactive",
    ]
    assert len(graph.controls) == 1


def test_menu_only_generic_commandlist_becomes_interactive_control(tmp_path):
    path = _write(tmp_path / "menu_only.ini", r"""[Constants]
global persist $Outfit = 0

[CommandListGenericPicker]
if $choice == 0
    $Outfit = 0
elif $choice == 1
    $Outfit = 1
endif

[TextureOverrideOnly]
if $Outfit == 1
    Resource\ZZMI\Diffuse = TextureAlternate
endif
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    menu = parsed.control_projection["menu"]
    outfit = next(info for info in menu.values() if info["var"] == "Outfit")
    assert "interactive" in outfit["controllers"]
    assert any(action["trigger"] == "CommandListGenericPicker"
               for action in parsed.control_projection["actions"])


def test_runtime_commandlist_does_not_create_a_viewer_control(tmp_path):
    source = source_from_path("runtime.ini", str(tmp_path), text=r"""[Constants]
global persist $State = 0

[TextureOverrideRuntime]
run = CommandListRuntime
if $State == 1
    Resource\ZZMI\Diffuse = TextureAlternate
endif

[CommandListRuntime]
$State = 1
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    state = resolver.resolve("$State", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (state,))])
    assert graph.controls == {}
    assert state.key in graph.internal_controllers


def test_unreferenced_helper_commandlist_does_not_create_a_viewer_control(tmp_path):
    source = source_from_path("helper.ini", str(tmp_path), text=r"""[Constants]
global persist $State = 0

[CommandListHelper]
$State = 1

[TextureOverrideRuntime]
if $State == 1
    Resource\ZZMI\Diffuse = TextureAlternate
endif
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    state = resolver.resolve("$State", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (state,))])
    assert graph.controls == {}
    assert graph.actions == []
    assert state.key in graph.internal_controllers


def test_external_framework_namespace_remains_internal(tmp_path):
    source = source_from_path("framework.ini", str(tmp_path), text=r"""[TextureOverrideBody]
if $\WWMIv1\vg_offset == 1
    Resource\ZZMI\Diffuse = TextureAlternate
endif
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    external = resolver.resolve(r"$\WWMIv1\vg_offset", source,
                                "TextureOverrideBody")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (external,))])
    assert external.is_external
    assert graph.controls == {}
    assert graph.internal_controllers == {}


def test_control_graph_serializes_complete_action_contract(tmp_path):
    path = _write(tmp_path / "actions.ini", r"""[Constants]
global persist $Outfit = 0
global persist $Material = 0

[KeyApply]
key = x
run = CommandListApply

[CommandListApply]
$Outfit = 1
$Material = 2

[TextureOverrideBody]
if $Outfit == 1
    Resource\ZZMI\Diffuse = TexOutfit
endif
if $Material == 2
    Resource\ZZMI\NormalMap = TexMaterial
endif
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    outfit = resolver.resolve("$Outfit", source, "Constants")
    material = resolver.resolve("$Material", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (outfit,)),
                  RenderEffect("texture", (material,))])

    action = graph.to_dict()["actions"][0]
    assert action["kind"] == "compound_action"
    assert action["trigger"] == "x"
    assert action["writes"] == [outfit.key, material.key]
    assert [item["target"] for item in action["assignments"]] == [
        outfit.key, material.key]
    assert all("expression" in item and "source" in item
               for item in action["assignments"])
    assert [item["operation"] for item in action["assignments"]] == [
        {"kind": "set", "value": "1"},
        {"kind": "set", "value": "2"},
    ]
    serialized = graph.to_dict()
    assert serialized["schema_version"] == 4
    json.dumps(serialized)


def test_shared_namespace_actions_are_not_cross_attached_to_one_control(
        tmp_path):
    first = _write(tmp_path / "first.ini", r"""namespace = shared

[Constants]
global $State = 0

[KeyFirst]
key = f
run = CommandListFirst

[CommandListFirst]
$State = 1

[TextureOverrideFirst]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $State == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = first-position.buf
stride = 12
[ResourceTexcoord]
filename = first-texcoord.buf
stride = 8
[ResourceIndex]
filename = first-index.buf
format = R32_UINT
""")
    second = _write(tmp_path / "second.ini", r"""namespace = shared

[Constants]
global $State = 0

[KeySecond]
key = s
run = CommandListSecond

[CommandListSecond]
$State = 0

[TextureOverrideSecond]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $State == 0
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = second-position.buf
stride = 12
[ResourceTexcoord]
filename = second-texcoord.buf
stride = 8
[ResourceIndex]
filename = second-index.buf
format = R32_UINT
""")

    parsed = analyze_mod_inis([first, second], str(tmp_path))
    state = next(item for item in parsed.control_projection["menu"].values()
                 if item["var"].casefold() == "shared/state")

    # The shared state is one control, but two source-scoped operations are
    # possible.  The projection must not attach whichever action happens to be
    # first in graph order.
    assert "action" not in state
    sources = {
        action["source"]["ini_path"]
        for action in parsed.control_projection["actions"]
        if action["trigger"] in {"f", "s"}
    }
    assert sources == {"first.ini", "second.ini"}


def test_ambiguous_selector_dispatch_is_not_projected_as_one_action(tmp_path):
    path = _write(tmp_path / "ambiguous.ini", r"""[Constants]
global $first_choice = 0
global $second_choice = 0
global persist $Hair = 0
global persist $Dress = 0

[CommandListAmbiguous]
if $first_choice == 1
    $Hair = 1 - $Hair
endif
if $second_choice == 1
    $Dress = 1 - $Dress
endif

[TextureOverrideBody]
if $Hair == 1
    drawindexed = 3, 0, 0
endif
[TextureOverrideDress]
if $Dress == 1
    drawindexed = 3, 0, 0
endif
""")
    parsed = analyze_mod_inis([path], str(tmp_path))

    assert parsed.control_graph.actions == []
    assert parsed.control_projection["menu"] == {}


def test_conditional_copy_does_not_create_selector_alias(tmp_path):
    path = _write(tmp_path / "conditional-alias.ini", r"""[Constants]
global $enabled = 0
global $slot = 0
global $clicked = 0
global persist $Hair = 0

[CommandListClicked]
if $enabled == 1
    $clicked = $slot
endif
if $clicked == 1
    $Hair = 1 - $Hair
elif $clicked == 2
    $Hair = $Hair + 1
endif

[TextureOverrideBody]
if $Hair == 1
    drawindexed = 3, 0, 0
endif
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    hair = resolver.resolve("$Hair", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("visibility", (hair,))])

    actions = [action for action in graph.actions
               if action.trigger == "CommandListClicked"]
    assert {action.selector["value"] for action in actions} == {"1", "2"}
    assert all({"slot", "hovered"}.isdisjoint(
        {variable.name for variable in action.selector_aliases})
        for action in actions)


def test_selector_states_ignore_in_place_numeric_updates(tmp_path):
    path = _write(tmp_path / "selector-loop.ini", r"""[Constants]
global $slot = 0
global $clicked = 0
global persist $Hair = 0

[CommandListDraw]
$slot = $slot + 1
$hovered = $slot
$clicked = $hovered
if $clicked == 1
    $Hair = 1 - $Hair
endif

[TextureOverrideBody]
if $Hair == 1
    drawindexed = 3, 0, 0
endif
""")
    source = source_from_path(path, str(tmp_path))
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    hair = resolver.resolve("$Hair", source, "TextureOverrideBody")
    clicked = resolver.resolve("$clicked", source, "CommandListDraw")
    graph = build_control_graph(
        [facts], [RenderEffect("visibility", (hair,))])

    states = graph.selector_states({"var": clicked, "value": "1"})
    assert {(state.variable.name, state.value) for state in states} == {
        ("clicked", "1"), ("hovered", "1"), ("slot", "1"),
    }
    assert not any(edge.source == edge.target
                   for edge in graph.selector_flow)


def test_runtime_present_compound_action_is_not_user_facing_without_selector(
        tmp_path):
    path = _write(tmp_path / "runtime-compound.ini", r"""[Constants]
global persist $Hair = 0
global persist $Dress = 0

[Present]
run = CommandListRecompute

[CommandListRecompute]
$Hair = 1
$Dress = 1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Hair == 1
    drawindexed = 3, 0, 0
endif
[TextureOverrideDress]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $Dress == 1
    drawindexed = 3, 0, 0
endif
[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    action = next(action for action in parsed.control_projection["actions"]
                  if action["trigger"] == "CommandListRecompute")

    assert action["kind"] == "compound_action"
    assert action["user_facing"] is False
    assert not any(item.get("action") == action
                   for item in parsed.control_projection["menu"].values())


def test_unified_analysis_preserves_render_contract_for_equivalent_state(
        tmp_path):
    path = _write(tmp_path / "render.ini", r"""[Constants]
global persist $State = 0

[KeyState]
key = s
type = cycle
$State = 0,1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $State == 1
    drawindexed = 3, 0, 0
endif

[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
[ResourceIndex]
filename = index.buf
format = R32_UINT
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    draw = parsed.groups[0]["draws"][0]

    assert {
        "count": draw.count,
        "start": draw.start,
        "base": draw.base,
        "conditions": draw.conditions,
    } == {
        "count": 3,
        "start": 0,
        "base": 0,
        "conditions": [[{
            "var": "State", "value": "1", "negate": False,
        }]],
    }
    assert any(
        effect.kind == "visibility"
        and [variable.name for variable in effect.variables] == ["state"]
        for effect in parsed.control_graph.effects)


def test_dnf_keeps_numeric_comparison_operators():
    less_than = parse_condition_dnf("$v < 2", {})
    assert less_than == [[{
        "var": "v", "value": "2", "negate": False, "op": "<",
    }]]
    assert dnf_not(less_than) == [[{
        "var": "v", "value": "2", "negate": False, "op": ">=",
    }]]


def test_key_run_projects_source_ordered_compound_action(tmp_path):
    path = _write(tmp_path / "actions.ini", r"""[Constants]
global persist $Outfit = 0
global persist $Material = 0

[KeyApply]
key = x
run = CommandListApply

[CommandListApply]
$Outfit = 1
$Material = 2

[TextureOverrideBody]
if $Outfit == 1
    Resource\ZZMI\Diffuse = TexOutfit
endif
if $Material == 2
    Resource\ZZMI\NormalMap = TexMaterial
endif
""")

    parsed = analyze_mod_inis([path], str(tmp_path))
    action = next(item for item in parsed.control_projection["actions"]
                  if item["trigger"].casefold() == "x")
    assert action["kind"] == "compound_action"
    assert [item["target"] for item in action["assignments"]] == [
        "Outfit", "Material"]
    assert set(action["writes"]) == {"Outfit", "Material"}
    assert {item["controllers"][0] for item in parsed.control_projection["menu"].values()} == {
        "compound_action"}
    state = load_control_state(ModLoadContext(str(tmp_path), [path]))
    assert state["controls"]["actions"] == parsed.control_projection["actions"]


def test_continuous_bounds_ignore_unpaired_comparison_conditions(tmp_path):
    source = source_from_path("shape.ini", str(tmp_path), text=r"""[Constants]
global persist $shape = 0.5

[CommandListDrag]
if $shape < 0.75
    $shape = $shape + 1
endif
if $shape > 0.1
    $shape = $shape - 1
endif
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    shape = resolver.resolve("$shape", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("shape", (shape,))], shape_vars=(shape,))
    assert graph.controls[shape.key].domain == {
        "kind": "continuous", "min": 0.0, "max": 1.0,
    }


def test_continuous_bounds_require_same_branch_clamp_write(tmp_path):
    source = source_from_path("shape.ini", str(tmp_path), text=r"""[Constants]
global persist $shape = 0.5

[CommandListClamp]
if $shape < 0
    $shape = 0
endif
if $shape > 1
    $shape = 1
endif
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    shape = resolver.resolve("$shape", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("shape", (shape,))], shape_vars=(shape,))
    assert graph.controls[shape.key].domain == {
        "kind": "continuous", "min": 0, "max": 1,
    }


def test_cursor_assignment_is_continuous_when_render_facing(tmp_path):
    source = source_from_path("slider.ini", str(tmp_path), text=r"""[Constants]
global persist $slider = 0.5

[CommandListDrag]
$slider = $cursor_x
if $slider < 0
    $slider = 0
endif
if $slider > 1
    $slider = 1
endif
""")
    resolver = VariableResolver([source])
    facts = scan_program(source, resolver)
    slider = resolver.resolve("$slider", source, "Constants")
    graph = build_control_graph(
        [facts], [RenderEffect("texture", (slider,))])
    assert graph.controls[slider.key].domain == {
        "kind": "continuous", "min": 0, "max": 1,
    }


def test_key_semicolon_binding_survives_program_scan():
    source = source_from_path("backbow.ini", "", text=r"""[Key$BackBow]
key = ;
type = cycle
$BackBow = 0,1
""")
    facts = scan_program(source)
    assert facts.key_inputs[0].key == ";"


def test_texture_only_semantic_assignment_is_a_render_control(tmp_path):
    path = _write(tmp_path / "texture.ini", r"""[Constants]
global persist $Material = 0

[KeyMaterial]
key = m
type = cycle
$Material = 0,1

[TextureOverrideOnly]
if $Material == 1
    Resource\ZZMI\Diffuse = TextureAlternate
endif
""")
    parsed = analyze_mod_inis([path], str(tmp_path))
    assert "KeyMaterial" in parsed.control_projection["toggles"]
    assert parsed.control_projection["toggles"]["KeyMaterial"]["wired"] is True


def test_pending_key_is_visible_without_becoming_wired(tmp_path):
    path = _write(tmp_path / "pending.ini", r"""[KeyFresh]
key = f
type = cycle
$Fresh = 0,1
""")
    parsed = analyze_mod_inis(
        [path], str(tmp_path),
        pending_new_sections={"pending.ini": {"KeyFresh"}})
    info = parsed.control_projection["toggles"]["KeyFresh"]
    assert info["pending"] is True
    assert info["wired"] is False
    assert info["vars"] == {"Fresh": ["0", "1"]}


def test_present_capture_bindings_use_forwarded_state_and_local_names():
    doc = IniDocument.from_string(
        "[KeyLocal]\nkey = l\ntype = cycle\n$local = 0,1\n",
        path="model.ini")
    bindings = [{"ini": "model.ini", "authored_var": "local",
                 "control_id": "cx_mod/local"}]
    assert capturable_variables(doc, bindings) == ["local"]
    add_present(doc, "p", "", {"cx_mod/local": "1"}, bindings)
    assert "$local = 1" in doc.to_string()


def test_texture_precedence_preserves_operators_and_rejects_empty_ranges():
    assert _condition_group_is_consistent([{
        "var": "v", "value": "1", "negate": False, "op": ">="}, {
        "var": "v", "value": "1", "negate": False, "op": "<",
    }]) is False
    assert _condition_group_is_consistent([{
        "var": "v", "value": "1", "negate": False, "op": "<="}, {
        "var": "v", "value": "1", "negate": False, "op": ">=",
    }]) is True
