"""Role recovery from explicit and legacy texture slot mappings."""

import pytest

from core.ini.parser import (build_draw_groups, extract_resources, merge_sections,
                              _scan_sections_for_draws)
from tests.support.model_data import standard_component_resources
from tests.support.provenance import visible


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
        standard_component_resources(
            position_file="position.buf", texcoord_file="texcoord.buf",
            position_stride=40, texcoord_stride=20),
    ]
    for resource, filename in resources.items():
        lines.extend(["", f"[{resource}]", f"filename = {filename}"])
    path = tmp_path / "mod.ini"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sections = merge_sections([str(path)])
    groups = build_draw_groups(sections, extract_resources(sections))
    return groups[0]["draws"][0]


@pytest.mark.parametrize("variant_count", [1, 2], ids=["singleton", "variants"])
@pytest.mark.parametrize("diffuse_slots", [(0,), (3, 9)],
                         ids=["single-slot", "duplicate-slots"])
def test_legacy_roles_stay_with_their_component(
        tmp_path, variant_count, diffuse_slots):
    components = ("Component01", "Component02")
    lines = ["[KeyStyle]", "type = cycle", "$style = 0,1"]
    for component in components:
        lines.extend([
            f"[TextureOverride{component}]",
            f"ib = Resource{component}IB",
            f"vb0 = Resource{component}Position",
            f"vb1 = Resource{component}Texcoord",
            f"run = CommandList{component}",
            "drawindexed = 3, 0, 0",
            f"[CommandList{component}]",
        ])
        for variant in range(variant_count):
            if variant_count > 1:
                lines.append("if $style == 0" if variant == 0 else "else")
            suffix = f".{variant}" if variant_count > 1 else ""
            lines.extend(f"ps-t{slot} = Resource{component}Diffuse{suffix}"
                         for slot in diffuse_slots)
            lines.append(f"ps-t1 = Resource{component}LightMap{suffix}")
        if variant_count > 1:
            lines.append("endif")
        lines.append(standard_component_resources(
            position_file=f"{component}-position.buf",
            texcoord_file=f"{component}-texcoord.buf",
            ib_file=f"{component}.ib").replace("Component01", component))
        for variant in range(variant_count):
            suffix = f".{variant}" if variant_count > 1 else ""
            for role in ("Diffuse", "LightMap"):
                lines.extend([
                    f"[Resource{component}{role}{suffix}]",
                    f"filename = {component}-{role}-{variant}.dds",
                ])
    path = tmp_path / "mod.ini"
    path.write_text("\n".join(lines), encoding="utf-8")
    sections = merge_sections([str(path)])
    groups = build_draw_groups(sections, extract_resources(sections))

    assert [group["name"] for group in groups] == list(components)
    for group in groups:
        draw = group["draws"][0]
        for role, channel in (("Diffuse", "diffuse"), ("LightMap", "light_map")):
            expected = [f"{group['name']}-{role}-{variant}.dds"
                        for variant in range(variant_count)]
            files = [item["file"] for item in draw.texture_rules(channel)]
            if channel == "diffuse":
                expected_history = [filename for filename in expected
                                    for _ in diffuse_slots]
            else:
                expected_history = expected
            assert (files or [draw.texture_default(channel)]) == expected_history
            if variant_count > 1:
                for value, filename in enumerate(expected):
                    applicable = [item["file"]
                                  for item in draw.texture_rules(channel)
                                  if visible(item["conditions"],
                                             {"style": str(value)})]
                    assert applicable and set(applicable) == {filename}
        assert {item.slot: item.role_hint for item in draw.slot_textures} == {
            1: "light_map", **dict.fromkeys(diffuse_slots, "diffuse")}


@pytest.mark.parametrize("assignments, resources", [
    ("ps-t3 = ResourceComponent01Diffuse.0\n"
     "ps-t9 = ResourceComponent01Diffuse.1",
     {"ResourceComponent01Diffuse.0": "diffuse-0.dds",
      "ResourceComponent01Diffuse.1": "diffuse-1.dds"}),
    ("ps-t3 = ResourceComponent01Diffuse.0\n"
     "ps-t9 = ResourceComponent01Diffuse.0\n"
     "ps-t3 = ResourceComponent01Diffuse.1",
     {"ResourceComponent01Diffuse.0": "diffuse-0.dds",
      "ResourceComponent01Diffuse.1": "diffuse-1.dds"}),
    ("ps-t3 = ResourceComponent01Diffuse\n"
     "ps-t9 = ResourceComponent01Diffuse\n"
     "ps-t9 = ResourceComponent01NormalMap",
     {"ResourceComponent01Diffuse": "diffuse.dds",
      "ResourceComponent01NormalMap": "normal.dds"}),
    ("ps-t3 = ResourceComponent01Diffuse\n"
     "ps-t9 = ResourceComponent01Diffuse\n"
     "ps-t3 = ResourceComponent01DiffuseExtra",
     {"ResourceComponent01Diffuse": "diffuse.dds",
      "ResourceComponent01DiffuseExtra": "extra.dds"}),
], ids=["split-variants", "partial-duplicate", "role-conflict", "prefix-conflict"])
def test_inconsistent_legacy_slot_sets_are_rejected(
        tmp_path, assignments, resources):
    draw = _draw(tmp_path, assignments, resources)

    assert all(item.role_hint is None for item in draw.slot_textures)
    assert draw.texture_default("diffuse") is None
    assert draw.texture_default("normal_map") is None


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

    assert draw.texture_default("diffuse") == "opaque.dds"
    assert draw.texture_provenance == {"diffuse": "mod_slot_semantic"}


@pytest.mark.parametrize("fallback, resources", [
    ("ps-t0 = ResourceComponent01Diffuse.0\nps-t0 = ResourceComponent01Diffuse.1",
     {"ResourceComponent01Diffuse.0": "component01-diffuse-0.dds",
      "ResourceComponent01Diffuse.1": "component01-diffuse-1.dds"}),
    (r"ps-t0 = Resource\GIMI\Diffuse" "\nps-t0 = ResourceOpaque",
     {"ResourceOpaque": "opaque.dds"}),
], ids=["legacy", "mapped"])
def test_semantic_and_slot_variants_keep_disjoint_conditions(
        tmp_path, fallback, resources):
    draw = _draw(
        tmp_path,
        rf"""if $style == 0
Resource\GIMI\Diffuse = ResourceExplicit
else
{fallback}
endif""",
        {"ResourceExplicit": "explicit.dds", **resources},
        prefix="[KeyStyle]\ntype = cycle\n$style = 0,1\n",
    )

    assert [[item["file"] for item in draw.texture_rules("diffuse")
             if visible(item["conditions"], {"style": value})]
            for value in ("0", "1")] == [["explicit.dds"], list(resources.values())]


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
