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


def test_proven_slotfix_assignment_uses_diffuse_pipeline(tmp_path):
    draw = _draw(
        tmp_path,
        r"""ps-t0 = Resource\GIMI\Diffuse
ps-t0 = ResourceOpaque""",
        {"ResourceOpaque": "opaque.dds"},
    )

    assert draw.texture_default("diffuse") == "opaque.dds"
    assert draw.slot_textures[0].role_hint == "diffuse"
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
        r"""ps-t0 = ResourceBodyDiffuse
ps-t1 = ResourceBodyLightMap""",
        {"ResourceBodyDiffuse": "body-diffuse.dds",
         "ResourceBodyLightMap": "body-light-map.dds"},
        prefix=(r"[CommandList\LegacySlots]" "\n"
                r"ps-t0 = ResourceBodyDiffuse" "\n"
                r"ps-t1 = ResourceBodyLightMap" "\n"),
    )

    assert draw.texture_default("diffuse") == "body-diffuse.dds"
    assert draw.texture_default("light_map") == "body-light-map.dds"
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic",
                                       "light_map": "mod_slot_semantic"}


def test_single_legacy_slot_name_does_not_imply_a_role(tmp_path):
    draw = _draw(
        tmp_path,
        "ps-t0 = ResourceBodyDiffuse",
        {"ResourceBodyDiffuse": "body-diffuse.dds"},
    )

    assert draw.slot_textures[0].role_hint is None
    assert draw.texture_default("diffuse") is None


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
