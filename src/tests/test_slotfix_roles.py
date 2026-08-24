"""Role recovery from explicit and legacy texture slot mappings."""

from core.ini_parser import (build_draw_groups, extract_resources, merge_sections,
                              _scan_sections_for_draws)


def _draw(tmp_path, assignments, resources, prefix=""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    lines = [
        prefix,
        "[TextureOverrideBodyBlend]",
        "vb0 = ResourceBodyPosition",
        "vb1 = ResourceBodyTexcoord",
        "",
        "[TextureOverrideBody]",
        "ib = ResourceBodyIB",
        assignments,
        "drawindexed = 3, 0, 0",
        "",
        "[ResourceBodyIB]",
        "filename = body.ib",
        "format = DXGI_FORMAT_R32_UINT",
        "",
        "[ResourceBodyPosition]",
        "filename = position.buf",
        "stride = 40",
        "",
        "[ResourceBodyTexcoord]",
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


def _barbara_variant_groups(tmp_path):
    components = ("Head", "Body", "Dress")
    lines = [
        "[KeySwap]",
        "type = cycle",
        "$swapvar = 0,1,2,3",
    ]
    for component in components:
        lines.extend([
            "",
            f"[TextureOverrideBarbara{component}]",
            f"vb0 = ResourceBarbara{component}Position",
            f"vb1 = ResourceBarbara{component}Texcoord",
            f"ib = ResourceBarbara{component}IB",
            f"run = CommandListBarbara{component}",
            "drawindexed = 3, 0, 0",
            "",
            f"[CommandListBarbara{component}]",
        ])
        for variant in range(4):
            keyword = "if" if variant == 0 else "else if"
            lines.extend([
                f"{keyword} $swapvar == {variant}",
                f"ps-t0 = ResourceBarbara{component}Diffuse.{variant}",
                f"ps-t1 = ResourceBarbara{component}LightMap.{variant}",
            ])
        lines.append("endif")
    for component in components:
        lines.extend([
            "",
            f"[ResourceBarbara{component}Position]",
            f"filename = {component.lower()}-position.buf",
            "stride = 40",
            "",
            f"[ResourceBarbara{component}Texcoord]",
            f"filename = {component.lower()}-texcoord.buf",
            "stride = 20",
            "",
            f"[ResourceBarbara{component}IB]",
            f"filename = {component.lower()}.ib",
            "format = DXGI_FORMAT_R32_UINT",
        ])
        for variant in range(4):
            lines.extend([
                "",
                f"[ResourceBarbara{component}Diffuse.{variant}]",
                f"filename = {component.lower()}-diffuse-{variant}.dds",
                "",
                f"[ResourceBarbara{component}LightMap.{variant}]",
                f"filename = {component.lower()}-light-{variant}.dds",
            ])
    path = tmp_path / "barbara-variants.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sections = merge_sections([str(path)])
    return build_draw_groups(sections, extract_resources(sections))


def test_barbara_style_scopes_keep_all_component_variants_independent(tmp_path):
    groups = _barbara_variant_groups(tmp_path)

    assert [group["name"] for group in groups] == ["BarbaraHead", "BarbaraBody",
                                                    "BarbaraDress"]
    for group in groups:
        draw = group["draws"][0]
        component = group["name"][len("Barbara"):].lower()
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


def test_beidou_style_singleton_resources_keep_each_component_role(tmp_path):
    lines = []
    components = ("Head", "Body", "Extra")
    for component in components:
        lines.extend([
            f"[TextureOverrideBeidou{component}]",
            f"vb0 = ResourceBeidou{component}Position",
            f"vb1 = ResourceBeidou{component}Texcoord",
            f"ib = ResourceBeidou{component}IB",
            f"ps-t0 = ResourceBeidou{component}Diffuse",
            f"ps-t1 = ResourceBeidou{component}LightMap",
            "drawindexed = 3, 0, 0",
            "",
            f"[ResourceBeidou{component}Position]",
            f"filename = {component.lower()}-position.buf",
            "stride = 40",
            "",
            f"[ResourceBeidou{component}Texcoord]",
            f"filename = {component.lower()}-texcoord.buf",
            "stride = 20",
            "",
            f"[ResourceBeidou{component}IB]",
            f"filename = {component.lower()}.ib",
            "format = DXGI_FORMAT_R16_UINT",
            "",
            f"[ResourceBeidou{component}Diffuse]",
            f"filename = {component.lower()}-diffuse.dds",
            "",
            f"[ResourceBeidou{component}LightMap]",
            f"filename = {component.lower()}-light-map.dds",
            "",
        ])
    path = tmp_path / "beidou-style.ini"
    path.write_text("\n".join(lines), encoding="utf-8")
    sections = merge_sections([str(path)])
    groups = build_draw_groups(sections, extract_resources(sections))

    assert [group["name"] for group in groups] == [
        "BeidouHead", "BeidouBody", "BeidouExtra"]
    for group in groups:
        draw = group["draws"][0]
        assert [item.role_hint for item in draw.slot_textures] == [
            "diffuse", "light_map"]
        assert draw.texture_provenance == {
            "diffuse": "mod_slot_legacy",
            "light_map": "mod_slot_legacy",
        }


def test_proven_slotfix_assignment_uses_diffuse_pipeline(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceOpaque""",
        {"ResourceOpaque": "opaque.dds"},
    )

    assert draw.texture_default("diffuse") == "opaque.dds"
    assert draw.slot_textures[0].role_hint == "diffuse"
    assert draw.slot_textures[0].role_hint_source == "mod_slot_mapping"
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}


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
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds",
         "ResourceBodyLightMap.0": "body-light-map-0.dds",
         "ResourceBodyLightMap.1": "body-light-map-1.dds"},
        prefix=(r"[CommandList\LegacySlots]" "\n"
                r"ps-t0 = ResourceBodyDiffuse.0" "\n"
                r"ps-t0 = ResourceBodyDiffuse.1" "\n"
                r"ps-t1 = ResourceBodyLightMap.0" "\n"
                r"ps-t1 = ResourceBodyLightMap.1" "\n"),
    )

    assert draw.texture_default("diffuse") == "body-diffuse-1.dds"
    assert draw.texture_default("light_map") == "body-light-map-1.dds"
    assert draw.texture_provenance == {"diffuse": "mod_slot_legacy",
                                       "light_map": "mod_slot_legacy"}


def test_single_declared_legacy_resource_classifies_itself(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceBodyDiffuse",
        {"ResourceBodyDiffuse": "body-diffuse.dds"},
    )

    assert draw.slot_textures[0].role_hint == "diffuse"
    assert draw.slot_textures[0].role_hint_source == "legacy_slot_mapping"
    assert draw.texture_default("diffuse") == "body-diffuse.dds"


def test_single_opaque_resource_stays_unresolved(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceShadowLookup",
        {"ResourceShadowLookup": "shadow.dds"},
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None


def test_legacy_sibling_inherits_an_exact_role_anchor(tmp_path):
    draw = _draw(
        tmp_path,
        r"""if $style == 0
ps-t4 = ResourceAstraLegANormalMap
else
ps-t4 = ResourceAstraLegANormalMapNSFW
endif""",
        {"ResourceAstraLegANormalMap": "leg-normal.dds",
         "ResourceAstraLegANormalMapNSFW": "leg-normal-nsfw.dds"},
        prefix="[KeyStyle]\ntype = cycle\n$style = 0,1\n",
    )

    assert {item.role_hint for item in draw.slot_textures} == {"normal_map"}
    assert all(item.role_hint_source == "legacy_slot_mapping"
               for item in draw.slot_textures)
    assert {item["file"] for item in draw.texture_rules("normal_map")} == {
        "leg-normal.dds", "leg-normal-nsfw.dds"}


def test_legacy_sibling_moving_slots_rejects_the_family(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t4 = ResourceAstraLegANormalMap
ps-t5 = ResourceAstraLegANormalMapNSFW""",
        {"ResourceAstraLegANormalMap": "leg-normal.dds",
         "ResourceAstraLegANormalMapNSFW": "leg-normal-nsfw.dds"},
    )

    assert all(item.role_hint is None for item in draw.slot_textures)
    assert draw.texture_default("normal_map") is None


def test_legacy_variant_suffixes_recover_texture_roles(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = ResourceBodyDiffuse.0
ps-t0 = ResourceBodyDiffuse.1
ps-t1 = ResourceBodyLightMap.0
ps-t1 = ResourceBodyLightMap.1""",
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds",
         "ResourceBodyLightMap.0": "body-light-map-0.dds",
         "ResourceBodyLightMap.1": "body-light-map-1.dds"},
    )

    assert draw.texture_default("diffuse") == "body-diffuse-1.dds"
    assert draw.texture_default("light_map") == "body-light-map-1.dds"
    assert draw.slot_textures[0].role_hint == "diffuse"
    assert draw.slot_textures[1].role_hint == "light_map"
    assert draw.slot_textures[0].role_hint_source == "legacy_slot_mapping"
    assert draw.slot_textures[1].role_hint_source == "legacy_slot_mapping"
    assert draw.texture_provenance == {"diffuse": "mod_slot_legacy",
                                       "light_map": "mod_slot_legacy"}


def test_legacy_family_does_not_classify_an_opaque_resource(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = ResourceBodyDiffuse.0
ps-t0 = ResourceBodyDiffuse.1
ps-t0 = ResourceShadowLookup""",
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds",
         "ResourceShadowLookup": "shadow.dds"},
    )

    assert draw.slot_textures[0].resource == "ResourceShadowLookup"
    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_provenance == {"diffuse": "mod_slot_legacy"}


def test_conflicting_legacy_roles_on_one_slot_are_rejected(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = ResourceBodyDiffuse.0
ps-t0 = ResourceBodyDiffuse.1
ps-t0 = ResourceBodyNormalMap.0
ps-t0 = ResourceBodyNormalMap.1""",
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds",
         "ResourceBodyNormalMap.0": "body-normal-0.dds",
         "ResourceBodyNormalMap.1": "body-normal-1.dds"},
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None


def test_legacy_family_cannot_move_between_slots(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = ResourceBodyDiffuse.0
ps-t2 = ResourceBodyDiffuse.1""",
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds"},
    )

    assert all(item.role_hint is None for item in draw.slot_textures)
    assert draw.texture_default("diffuse") is None


def test_unreachable_legacy_scope_does_not_leak_into_a_draw(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceOpaque",
        {"ResourceOpaque": "opaque.dds",
         "ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds"},
        prefix=(r"[CommandList\Unrelated]" "\n"
                r"ps-t0 = ResourceBodyDiffuse.0" "\n"
                r"ps-t0 = ResourceBodyDiffuse.1" "\n"),
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None


def test_structural_slot_mapping_wins_over_legacy_mapping(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceBodyDiffuse.0
ps-t0 = ResourceBodyDiffuse.1
ps-t0 = ResourceOpaque""",
        {"ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds",
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
ps-t0 = ResourceBodyDiffuse.0
ps-t0 = ResourceBodyDiffuse.1
endif""",
        {"ResourceExplicit": "explicit.dds",
         "ResourceBodyDiffuse.0": "body-diffuse-0.dds",
         "ResourceBodyDiffuse.1": "body-diffuse-1.dds"},
        prefix="[KeyStyle]\ntype = cycle\n$style = 0,1\n",
    )

    assert {item["file"] for item in draw.texture_rules("diffuse")} == {
        "explicit.dds", "body-diffuse-0.dds", "body-diffuse-1.dds"}
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


def test_explicit_semantic_assignment_beats_slotfix(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceOpaque
Resource\GIMI\Diffuse = ResourceExplicit""",
        {"ResourceExplicit": "explicit.dds", "ResourceOpaque": "opaque.dds"},
    )

    assert draw.texture_default("diffuse") == "explicit.dds"
    assert draw.texture_provenance == {}


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
        "TextureOverrideBody": [
            r"Resource\GIMI\Diffuse = ResourceBodyDiffuse",
            "drawindexed = 3, 0, 0",
        ],
        "TextureOverrideHair": [
            "ps-t0 = ResourceHairDiffuse",
            "drawindexed = 3, 0, 0",
        ],
        r"CommandList\SlotMap": [
            r"ps-t0 = Resource\GIMI\Diffuse",
        ],
    }

    info = _scan_sections_for_draws(sections)
    hair = info["TextureOverrideHair"]["draws"][0]

    assert hair.slot_textures[0].role_hint == "diffuse"
    assert hair.diffuse_variants == [{
        "res": "ResourceHairDiffuse", "cond": [], "source": "slot",
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
