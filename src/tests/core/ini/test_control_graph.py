"""Regression coverage for the unified variable/control analysis path."""

from app.mods.analysis import analyze_mod_inis
from app.mods.controls import load_control_state
from app.mods.loader import ModLoadContext
from core.ini.control_graph import RenderEffect, build_control_graph
from core.ini.dnf import dnf_not, parse_condition_dnf
from core.ini.analysis import analyze_ini
from core.ini.program import scan_program
from core.ini.texture_roles import _condition_group_is_consistent
from core.ini.variables import VariableId, VariableResolver, source_from_path
from core.editing.present import capturable_variables, add as add_present
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
    model = _write(tmp_path / "Model.ini", r"""namespace = cx_Mod049

[Constants]
global $key_1 = 1

[TextureOverrideBody]
vb0 = ResourcePosition
vb1 = ResourceTexcoord
ib = ResourceIndex
if $key_1 == 1
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
global persist $key_1 = 0

[KeyClick]
key = x
type = cycle
$key_1 = 0,1

[KeyModViewerPresent]
$\cx_Mod049\key_1 = $key_1
""")

    parsed = analyze_mod_inis([model, menu], str(tmp_path))
    controls = parsed.control_projection

    assert len(controls["menu"]) == 1
    control = next(iter(controls["menu"].values()))
    assert control["var"].casefold() == "cx_mod049/key_1"
    assert control["domain"] == {"kind": "discrete", "values": ["0", "1"]}
    assert parsed.groups[0]["draws"][0].conditions == [[{
        "var": "cx_Mod049/key_1", "value": "1", "negate": False,
    }]]
    menu_target = next(target for target in parsed.present["target_inis"]
                       if target["value"] == "Menu.ini")
    assert menu_target["vars"] == ["cx_Mod049/key_1"]
    assert menu_target["capture_bindings"] == [{
        "ini": "Menu.ini", "authored_var": "key_1",
        "control_id": "cx_Mod049/key_1",
    }]
    state = load_control_state(ModLoadContext(str(tmp_path), [model, menu]))
    assert len(state["controls"]["menu"]) == 1


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


def test_continuous_bounds_keep_authored_comparison_boundaries(tmp_path):
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
        "kind": "continuous", "min": 0.1, "max": 0.75,
    }


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
