"""Shared Asset and material enrichment pipeline regressions."""

from types import SimpleNamespace
from unittest.mock import patch

from app.mods.enrichment import enrich_mod_analysis
from app.mods import loader
from core.materials.game_profile import GameDetection


def test_enrichment_runs_asset_before_wuwa_texture_fallback(tmp_path):
    events = []
    parsed = SimpleNamespace(
        groups=[{"name": "Body", "draws": []}],
        game=SimpleNamespace(game="wuwa"),
    )
    context = SimpleNamespace(
        mod_dir=str(tmp_path),
        asset_folders=[],
        dds_classification_cache={},
    )
    bindings = [[SimpleNamespace()]]

    def apply_assets(*args, **kwargs):
        events.append("asset")

    def apply_fallback(*args, **kwargs):
        events.append("wuwa")

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
            patch("app.mods.enrichment.wuwa_texture_fallback.apply",
                  side_effect=apply_fallback):
        result = enrich_mod_analysis(parsed, context)

    assert result == (bindings, {
        "index_status": "unavailable",
        "exact_draws": 0,
    })
    assert events == ["asset", "wuwa"]


def test_full_and_semantic_loads_share_enrichment_stage(tmp_path):
    parsed = loader.ParsedModAnalysis(
        groups=[{"name": "Body", "draws": []}],
        toggles={}, menu={}, defaults={}, state_rules=[], present={},
        game=GameDetection(
            game="unknown", runtime="unknown", texture_api="unknown",
            confidence="low", scores={}),
    )
    context = loader.ModLoadContext(
        str(tmp_path), [str(tmp_path / "mod.ini")], {}, {})
    enriched = ([], {"index_status": "unavailable"})

    with patch.object(loader, "analyze_mod_inis", return_value=parsed), \
            patch.object(loader, "enrich_mod_analysis",
                         return_value=enriched) as enrich, \
            patch.object(loader, "build_mesh_semantics",
                         return_value={"Body-1": {}}), \
            patch.object(loader, "build_mesh_result",
                         return_value=SimpleNamespace(
                             meshes={"Body-1": {}}, textures={})), \
            patch.object(loader, "_assign_material_profiles", return_value={}):
        semantic_result = loader.load_mesh_semantics(context)
        assert semantic_result["meshes"] == {"Body-1": {}}
        assert enrich.call_count == 1

        full_result = loader.load_mod(context=context)

    assert not full_result.get("error")
    assert enrich.call_count == 2
