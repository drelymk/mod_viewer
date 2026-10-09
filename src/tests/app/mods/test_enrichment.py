"""Shared Asset and material enrichment pipeline regressions."""

from types import SimpleNamespace
from unittest.mock import patch

from app.mods.enrichment import enrich_mod_analysis
from app.mods import loader
from core.materials.game_profile import GameDetection
from tests.support_snapshot import snapshot_context


def test_enrichment_collects_inventory_before_shared_assignment(tmp_path):
    events = []
    parsed = SimpleNamespace(
        groups=[{"name": "Component01", "draws": []}],
        game=SimpleNamespace(game="wuwa"),
        resource_files=["Components-2 t=aaaaaaaa.dds"],
        texture_override_indexes=[],
    )
    context = SimpleNamespace(
        mod_dir=str(tmp_path),
        asset_folders=[],
        dds_classification_cache={},
    )
    bindings = [[SimpleNamespace()]]

    def apply_assets(*args, **kwargs):
        events.append("assignment")
        assert kwargs["inventory"] == inventory
        assert kwargs["texture_indexes"] is parsed.texture_override_indexes

    inventory = {"choice.dds": {"file": "choice.dds", "source": "mod"}}

    def collect(*args, **kwargs):
        events.append("inventory")
        return inventory

    summary = SimpleNamespace(to_dict=lambda: {
        "index_status": "unavailable",
        "exact_draws": 0,
    })
    with patch("app.mods.enrichment.asset_resolver.resolve_groups",
               return_value=bindings), \
            patch("app.mods.enrichment.asset_resolver.summarize_groups",
                  return_value=summary), \
            patch("app.mods.enrichment.asset_enrichment.apply",
                  side_effect=apply_assets), \
            patch("app.mods.enrichment.asset_textures.collect_texture_inventory",
                  side_effect=collect):
        result = enrich_mod_analysis(parsed, context)

    assert result == (bindings, {
        "index_status": "unavailable",
        "exact_draws": 0,
    })
    assert events == ["inventory", "assignment"]


def test_full_and_semantic_loads_share_enrichment_stage(tmp_path):
    parsed = loader.ParsedModAnalysis(
        groups=[{"name": "Component01", "draws": []}],
        toggles={}, menu={}, defaults={}, state_rules=[], present={},
        game=GameDetection(
            game="unknown", runtime="unknown", texture_api="unknown",
            confidence="low", scores={}),
    )
    context = snapshot_context(
        str(tmp_path), [str(tmp_path / "mod.ini")], {}, {})
    enriched = ([], {"index_status": "unavailable"})

    with patch.object(loader, "analyze_mod_inis", return_value=parsed), \
            patch.object(loader, "enrich_mod_analysis",
                         return_value=enriched) as enrich, \
            patch.object(loader, "build_mesh_semantics",
                         return_value={"Component01-1": {}}), \
            patch.object(loader, "build_mesh_result",
                         return_value=SimpleNamespace(
                             meshes={"Component01-1": {}}, textures={}, geometry=b"")), \
            patch.object(loader, "_assign_material_profiles", return_value={}):
        semantic_result = loader.load_mesh_semantics(context)
        assert semantic_result["meshes"] == {
            "Component01-1": {"material_kind_override": None}}
        assert enrich.call_count == 1

        full_result = loader.load_mod(context=context)

    assert not full_result.get("error")
    assert enrich.call_count == 2
