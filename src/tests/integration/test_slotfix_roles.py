"""Role recovery from explicit and legacy texture slot mappings."""

from core.ini.parser import (build_draw_groups, extract_resources, merge_sections,
                              _scan_sections_for_draws)


def _draw(tmp_path, assignments, resources, prefix=""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    lines = [
        prefix,
        "[TextureOverrideComponent01Blend]",
        "vb0 = ResourceComponent01Position",
        "vb1 = ResourceComponent01Texcoord",
        "",
        "[TextureOverrideComponent01]",
        "ib = ResourceComponent01IB",
        assignments,
        "drawindexed = 3, 0, 0",
        "",
        "[ResourceComponent01IB]",
        "filename = component01.ib",
        "format = DXGI_FORMAT_R32_UINT",
        "",
        "[ResourceComponent01Position]",
        "filename = position.buf",
        "stride = 40",
        "",
        "[ResourceComponent01Texcoord]",
        "filename = texcoord.buf",
        "stride = 20",
    ]
    for resource, filename in resources.items():
        lines.extend(["", f"[{resource}]", f"filename = {filename}"])
    path = tmp_path / "mod.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sections = merge_sections([str(path)])
    groups = build_draw_groups(sections, extract_resources(sections))
    assert len(groups) == 1
    assert len(groups[0]["draws"]) == 1
    return groups[0]["draws"][0]


def _asset10_variant_groups(tmp_path):
    components = ("Head", "Component01", "Dress")
    lines = [
        "[KeySwap]",
        "type = cycle",
        "$swapvar = 0,1,2,3",
    ]
    for component in components:
        lines.extend([
            "",
            f"[TextureOverrideAsset10{component}]",
            f"vb0 = ResourceAsset10{component}Position",
            f"vb1 = ResourceAsset10{component}Texcoord",
            f"ib = ResourceAsset10{component}IB",
            f"run = CommandListAsset10{component}",
            "drawindexed = 3, 0, 0",
            "",
            f"[CommandListAsset10{component}]",
        ])
        for variant in range(4):
            keyword = "if" if variant == 0 else "else if"
            lines.extend([
                f"{keyword} $swapvar == {variant}",
                f"ps-t0 = ResourceAsset10{component}Diffuse.{variant}",
                f"ps-t1 = ResourceAsset10{component}LightMap.{variant}",
            ])
        lines.append("endif")
    for component in components:
        lines.extend([
            "",
            f"[ResourceAsset10{component}Position]",
            f"filename = {component.lower()}-position.buf",
            "stride = 40",
            "",
            f"[ResourceAsset10{component}Texcoord]",
            f"filename = {component.lower()}-texcoord.buf",
            "stride = 20",
            "",
            f"[ResourceAsset10{component}IB]",
            f"filename = {component.lower()}.ib",
            "format = DXGI_FORMAT_R32_UINT",
        ])
        for variant in range(4):
            lines.extend([
                "",
                f"[ResourceAsset10{component}Diffuse.{variant}]",
                f"filename = {component.lower()}-diffuse-{variant}.dds",
                "",
                f"[ResourceAsset10{component}LightMap.{variant}]",
                f"filename = {component.lower()}-light-{variant}.dds",
            ])
    path = tmp_path / "asset10-variants.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sections = merge_sections([str(path)])
    return build_draw_groups(sections, extract_resources(sections))


def test_asset10_style_scopes_keep_all_component_variants_independent(tmp_path):
    groups = _asset10_variant_groups(tmp_path)

    assert [group["name"] for group in groups] == ["Asset10Head", "Asset10Component01",
                                                    "Asset10Dress"]
    for group in groups:
        draw = group["draws"][0]
        component = group["name"][len("Asset10"):].lower()
        assert [item["file"] for item in draw.texture_rules("diffuse")] == [
            f"{component}-diffuse-{variant}.dds"
            for variant in range(4)]
        assert [item["file"] for item in draw.texture_rules("light_map")] == [
            f"{component}-light-{variant}.dds"
            for variant in range(4)]
        assert draw.texture_provenance == {
            "diffuse": "mod_slot_legacy",
            "light_map": "mod_slot_legacy",
        }


def test_asset11_style_singleton_resources_keep_each_component_role(tmp_path):
    lines = []
    components = ("Head", "Component01", "Extra")
    for component in components:
        lines.extend([
            f"[TextureOverrideAsset11{component}]",
            f"vb0 = ResourceAsset11{component}Position",
            f"vb1 = ResourceAsset11{component}Texcoord",
            f"ib = ResourceAsset11{component}IB",
            f"ps-t0 = ResourceAsset11{component}Diffuse",
            f"ps-t1 = ResourceAsset11{component}LightMap",
            "drawindexed = 3, 0, 0",
            "",
            f"[ResourceAsset11{component}Position]",
            f"filename = {component.lower()}-position.buf",
            "stride = 40",
            "",
            f"[ResourceAsset11{component}Texcoord]",
            f"filename = {component.lower()}-texcoord.buf",
            "stride = 20",
            "",
            f"[ResourceAsset11{component}IB]",
            f"filename = {component.lower()}.ib",
            "format = DXGI_FORMAT_R16_UINT",
            "",
            f"[ResourceAsset11{component}Diffuse]",
            f"filename = {component.lower()}-diffuse.dds",
            "",
            f"[ResourceAsset11{component}LightMap]",
            f"filename = {component.lower()}-light-map.dds",
            "",
        ])
    path = tmp_path / "asset11-style.ini"
    path.write_text("\n".join(lines), encoding="utf-8")
    sections = merge_sections([str(path)])
    groups = build_draw_groups(sections, extract_resources(sections))

    assert [group["name"] for group in groups] == [
        "Asset11Head", "Asset11Component01", "Asset11Extra"]
    for group in groups:
        draw = group["draws"][0]
        assert [item.role_hint for item in draw.slot_textures] == [
            "diffuse", "light_map"]
        assert draw.texture_provenance == {
            "diffuse": "mod_slot_legacy",
            "light_map": "mod_slot_legacy",
        }


def test_resource_name_alone_does_not_imply_diffuse(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceSuperDiffuseTexture",
        {"ResourceSuperDiffuseTexture": "opaque.dds"},
    )

    assert draw.texture_default("diffuse") is None
    assert draw.slot_textures[0].role_hint is None


def test_repeated_legacy_slot_names_recover_texture_roles(tmp_path):
    draw = _draw(
        tmp_path,
        r"""run = CommandList\LegacySlots""",
        {"ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
         "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds",
         "ResourceComponent01LightMap.0": "component01-light-map-0.dds",
         "ResourceComponent01LightMap.1": "component01-light-map-1.dds"},
        prefix=(r"[CommandList\LegacySlots]" "\n"
                r"ps-t0 = ResourceComponent01Diffuse.0" "\n"
                r"ps-t0 = ResourceComponent01Diffuse.1" "\n"
                r"ps-t1 = ResourceComponent01LightMap.0" "\n"
                r"ps-t1 = ResourceComponent01LightMap.1" "\n"),
    )

    assert draw.texture_default("diffuse") == "component01-diffuse-1.dds"
    assert draw.texture_default("light_map") == "component01-light-map-1.dds"
    assert draw.texture_provenance == {"diffuse": "mod_slot_legacy",
                                       "light_map": "mod_slot_legacy"}


def test_conflicting_legacy_roles_on_one_slot_are_rejected(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = ResourceComponent01Diffuse.0
ps-t0 = ResourceComponent01Diffuse.1
ps-t0 = ResourceComponent01NormalMap.0
ps-t0 = ResourceComponent01NormalMap.1""",
        {"ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
         "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds",
         "ResourceComponent01NormalMap.0": "component01-normal-0.dds",
         "ResourceComponent01NormalMap.1": "component01-normal-1.dds"},
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None


def test_unreachable_legacy_scope_does_not_leak_into_a_draw(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceOpaque",
        {"ResourceOpaque": "opaque.dds",
         "ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
         "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds"},
        prefix=(r"[CommandList\Unrelated]" "\n"
                r"ps-t0 = ResourceComponent01Diffuse.0" "\n"
                r"ps-t0 = ResourceComponent01Diffuse.1" "\n"),
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None


def test_structural_slot_mapping_wins_over_legacy_mapping(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceComponent01Diffuse.0
ps-t0 = ResourceComponent01Diffuse.1
ps-t0 = ResourceOpaque""",
        {"ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
         "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds",
         "ResourceOpaque": "opaque.dds"},
    )

    assert draw.slot_textures[0].role_hint == "diffuse"
    assert draw.slot_textures[0].role_hint_source == "mod_slot_mapping"
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}


def test_semantic_and_legacy_variants_keep_disjoint_conditions(tmp_path):
    draw = _draw(
        tmp_path,
        r"""if $style == 0
Resource\GIMI\Diffuse = ResourceExplicit
else
ps-t0 = ResourceComponent01Diffuse.0
ps-t0 = ResourceComponent01Diffuse.1
endif""",
        {"ResourceExplicit": "explicit.dds",
         "ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
         "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds"},
        prefix="[KeyStyle]\ntype = cycle\n$style = 0,1\n",
    )

    assert {item["file"] for item in draw.texture_rules("diffuse")} == {
        "explicit.dds", "component01-diffuse-0.dds", "component01-diffuse-1.dds"}
    assert draw.texture_provenance == {"diffuse": "mod_slot_legacy"}


def test_proven_slotfix_assignment_keeps_conditional_variants(tmp_path):
    draw = _draw(
        tmp_path,
        r"""if $skin == 0
ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceRed
else
ps-t0 = ResourceBlue
endif""",
        {"ResourceRed": "red.dds", "ResourceBlue": "blue.dds"},
        prefix="[KeySkin]\ntype = cycle\n$skin = 0,1\n",
    )

    assert [item["file"] for item in draw.texture_variants] == [
        "red.dds", "blue.dds"]
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}


def test_semantic_and_slot_roles_keep_disjoint_conditional_branches(tmp_path):
    draw = _draw(
        tmp_path,
        r"""if $style == 0
Resource\GIMI\Diffuse = ResourceExplicit
else
ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceOpaque
endif""",
        {"ResourceExplicit": "explicit.dds", "ResourceOpaque": "opaque.dds"},
        prefix="[KeyStyle]\ntype = cycle\n$style = 0,1\n",
    )

    assert draw.texture_default("diffuse") == "explicit.dds"
    assert {item["file"] for item in draw.texture_variants} == {
        "explicit.dds", "opaque.dds"}
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}


def test_explicit_role_in_one_path_does_not_suppress_another_path():
    sections = {
        "TextureOverrideComponent01": [
            r"Resource\GIMI\Diffuse = ResourceComponent01Diffuse",
            "drawindexed = 3, 0, 0",
        ],
        "TextureOverrideComponent02": [
            "ps-t0 = ResourceComponent02Diffuse",
            "drawindexed = 3, 0, 0",
        ],
        r"CommandList\SlotMap": [
            r"ps-t0 = Resource\GIMI\Diffuse",
        ],
    }

    info = _scan_sections_for_draws(sections)
    component02 = info["TextureOverrideComponent02"]["draws"][0]

    assert component02.slot_textures[0].role_hint == "diffuse"
    assert component02.diffuse_variants == [{
        "res": "ResourceComponent02Diffuse", "cond": [], "source": "slot",
    }]


def test_ambiguous_slot_role_does_not_guess(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = Resource\GIMI\NormalMap
ps-t0 = ResourceOpaque""",
        {"ResourceOpaque": "opaque.dds"},
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None


def test_slot_role_hints_do_not_leak_between_inis(tmp_path):
    mapped = _draw(
        tmp_path / "mapped",
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceMapped""",
        {"ResourceMapped": "mapped.dds"},
    )
    opaque = _draw(
        tmp_path / "opaque",
        "ps-t0 = ResourceOpaque",
        {"ResourceOpaque": "opaque.dds"},
    )

    assert mapped.slot_textures[0].role_hint == "diffuse"
    assert opaque.slot_textures[0].role_hint is None
    assert opaque.texture_default("diffuse") is None
