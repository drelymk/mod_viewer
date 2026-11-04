"""Coverage planning regressions for explicit original Asset filling."""

import pytest

from app.assets import composition as asset_composition
from app.assets import index as asset_index
from core.geometry.component_coverage import collect_component_overrides
from core.ini.document import IniDocument
from tests.support_snapshot import snapshot_context


def _index(*geometries, asset_path="Asset01"):
    return {
        "type": "ZZMI",
        "assets": [{"path": asset_path, "geometry": list(geometries)}],
        "byGeometryHash": {
            geometry["hash"]: [{"asset": 0, "geometry": ordinal}]
            for ordinal, geometry in enumerate(geometries)
        },
    }


def _geometry(hash_value, *ranges):
    return {
        "hash": hash_value,
        "ranges": [
            {"firstIndex": first, "indexCount": count}
            for first, count in ranges
        ],
        "metadata": "Asset01/hash.json",
        "componentName": hash_value,
    }


def _context(tmp_path, texts, roots=("asset-root",)):
    paths = []
    for name, text in texts.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    context = snapshot_context(str(tmp_path), paths)
    context.asset_folders = [
        {"type": "ZZMI", "path": root, "enabled": True} for root in roots]
    return context


def _zzmi(hash_value, extra=""):
    return (
        "[TextureOverrideComponent01]\n"
        f"hash = {hash_value}\n"
        "drawindexed = 3, 0, 0\n"
        "run = CommandList\\ZZMI\\SetTextures\n"
        f"{extra}"
    )


def test_collect_component_overrides_preserves_component_identity_and_range():
    result = collect_component_overrides({
        "TextureOverrideComponent02": [
            "hash = 0xAAAAAAAA",
            "match_first_index = 300",
            "match_index_count = 12",
        ],
    }, "nested/component02.ini")

    assert len(result) == 1
    assert result[0].key.geometry_hash == "aaaaaaaa"
    assert result[0].first_index == 300
    assert result[0].index_count == 12
    assert result[0].asset_identity_evidence is True


def test_auxiliary_buffer_hash_does_not_identify_another_asset():
    result = collect_component_overrides({
        "TextureOverrideComponent01Blend": [
            "hash = 0xAAAAAAAA",
            "handling = skip",
            "vb1 = ResourceComponent01Texcoord",
            "Draw = 3, 0",
        ],
    }, "mod.ini")

    assert result[0].asset_identity_evidence is False


def test_collect_component_overrides_follows_nested_command_lists():
    result = collect_component_overrides({
        "TextureOverrideComponent01": [
            "hash = 0xAAAAAAAA",
            "match_first_index = 300",
            "run = CommandListComponent01",
        ],
        "CommandListComponent01": [
            "handling = skip",
            "run = CommandListComponent01Draw",
        ],
        "CommandListComponent01Draw": [
            "ib = ResourceComponent01IB",
            "drawindexed = 12000, 300, 0",
        ],
    }, "mod.ini")

    assert len(result) == 1
    assert result[0].geometry_hash == "aaaaaaaa"
    assert result[0].first_index == 300
    assert result[0].asset_identity_evidence is True


def test_texture_only_geometry_replacement_and_skip_coverage_lifecycle(
        tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12), (300, 24)),
                   _geometry("bbbbbbbb", (0, 6)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi("bbbbbbbb") + (
            "[TextureOverrideComponent02IB]\n"
            "hash = aaaaaaaa\n"
            "run = CommandList Surface Material\n"
            "[CommandList Surface Material]\n"
            "Resource\\ZZMI\\Diffuse = ref ResourceSurfaceDiffuse\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "ready"
    assert {part.geometry_hash for part in plan.covered_parts} == {"bbbbbbbb"}
    assert [part.first_index for part in plan.missing_parts] == [0, 300]
    helper = context.ini.records[0].sections["CommandList Surface Material"]
    helper.append("post HaNdLiNg = SkIp ; suppress the original")
    helper.append("vb1 = ResourceBlend")
    suppressed = asset_composition.plan_missing_asset_parts(context)
    assert suppressed.status == "nothing_missing"
    assert len(suppressed.covered_parts) == 3
    assert not suppressed.missing_parts
    helper.pop(-2)
    assert [part.first_index for part in asset_composition.plan_missing_asset_parts(context).missing_parts] == [0, 300]
    helper[-1] = "ib = ResourceReplacementIB"
    replaced = asset_composition.plan_missing_asset_parts(context)
    assert replaced.status == "nothing_missing"
    assert not replaced.missing_parts
    helper.pop()
    restored = asset_composition.plan_missing_asset_parts(context)
    assert restored.to_dict() == plan.to_dict()


@pytest.mark.parametrize("lines, expected", [
    (["ps-t0 = ResourceSurface"], False),
    (["ps-t0 = null"], False),
    (["Resource\\GIMI\\NormalMap = ref ResourceSurface"], False),
    (["ps-t0 = ResourceSurface", "handling = skip"], True),
    (["ps-t0 = ResourceSurface", "ib = null"], True),
    (["ps-t0 = ResourceSurface", "ib = ResourceIndices"], True),
    (["ps-t0 = ResourceSurface", "drawindexed = auto"], True),
    (["vb1 = ResourceBlend", "handling = skip"], False),
    (["run = CommandList\\Unknown\\Helper"], True),
    (["run = CommandListSkinTexture"], False),
    ([r"run = CommandList\ZZMIv1\SkinTexture"], False),
    (["checktextureoverride = ps-t4"], False),
    (["run = CommandListSkinTexture", "run = CommandListUnknown"], True),
    ([], True),
])
def test_texture_hook_coverage_preserves_explicit_geometry_and_legacy_identity(
        lines, expected):
    overrides = collect_component_overrides({
        "TextureOverrideSurface": ["hash = aaaaaaaa", *lines],
    }, "mod.ini")
    assert len(overrides) == 1
    assert overrides[0].asset_identity_evidence is expected


def test_external_texture_check_face_coverage_tracks_local_geometry_and_suppression(
        tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12)),
                   _geometry("bbbbbbbb", (0, 9), (9, 6), (15, 3)))
    monkeypatch.setattr(asset_index, "load_index", lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi("aaaaaaaa") + (
            "[TextureOverrideSurface]\n"
            "if $\\Runtime\\style == 2\n"
            "hash = bbbbbbbb\n"
            "match_priority = 2\n"
            "run = CommandList Surface Material\n"
            "endif\n"
            "[CommandList Surface Material]\n"
            "post run = CommandListSkinTexture\n"),
    })
    plan = asset_composition.plan_missing_asset_parts(context)
    assert plan.status == "ready"
    assert [part.first_index for part in plan.missing_parts] == [0, 9, 15]
    sections = context.ini.records[0].sections
    helper = sections["CommandList Surface Material"]
    helper.append("handling = skip")
    assert asset_composition.plan_missing_asset_parts(context).status == "nothing_missing"
    helper.pop()
    sections["CommandListSkinTexture"] = ["ib = ResourceReplacementIB"]
    assert asset_composition.plan_missing_asset_parts(context).status == "nothing_missing"
    del sections["CommandListSkinTexture"]
    assert asset_composition.plan_missing_asset_parts(context).to_dict() == plan.to_dict()


def test_plan_unions_nested_inis_and_ignores_non_asset_hashes(
        tmp_path, monkeypatch):
    index = _index(
        _geometry("aaaaaaaa", (0, 12)),
        _geometry("bbbbbbbb", (0, 6)),
    )
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi("aaaaaaaa"),
        "nested/component02.ini": _zzmi(
            "bbbbbbbb", "handling = skip\nhash = deadbeef\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert len(plan.asset_parts) == 2
    assert len(plan.covered_parts) == 2
    assert not plan.missing_parts


def test_plan_ignores_auxiliary_hash_that_matches_another_asset(
        tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12)), asset_path="Asset08")
    index["assets"].append({
        "path": "Other",
        "geometry": [_geometry("bbbbbbbb", (0, 6))],
    })
    index["byGeometryHash"]["bbbbbbbb"] = [{"asset": 1, "geometry": 0}]
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": (
            "[TextureOverrideComponent01Blend]\n"
            "hash = bbbbbbbb\n"
            "handling = skip\n"
            "vb1 = ResourceComponent01Texcoord\n"
            "Draw = 3, 0\n"
            "[TextureOverrideComponent01]\n"
            "hash = aaaaaaaa\n"
            "drawindexed = 3, 0, 0\n"
            "run = CommandList\\ZZMI\\SetTextures\n"
        ),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert plan.asset["path"] == "Asset08"


def test_unknown_game_fallback_searches_all_asset_types(
        tmp_path, monkeypatch):
    indexes = {
        "GIMI": _index(_geometry("bbbbbbbb", (0, 12)),
                        asset_path="Other"),
        "ZZMI": _index(_geometry("aaaaaaaa", (0, 12)),
                        asset_path="Asset09"),
    }
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, _root: indexes[asset_type])
    context = _context(tmp_path, {
        "mod.ini": (
            "[TextureOverrideComponent01]\n"
            "hash = aaaaaaaa\n"
            "drawindexed = 3, 0, 0\n"
        ),
    })
    context.asset_folders = [
        {"type": "GIMI", "path": "gimi-root", "enabled": True},
        {"type": "ZZMI", "path": "zzmi-root", "enabled": True},
    ]

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert plan.asset_type == "ZZMI"
    assert plan.asset["path"] == "Asset09"


def test_unknown_game_fallback_keeps_cross_type_asset_ambiguity(
        tmp_path, monkeypatch):
    indexes = {
        "GIMI": _index(_geometry("aaaaaaaa", (0, 12)),
                        asset_path="Asset01"),
        "ZZMI": _index(_geometry("aaaaaaaa", (0, 12)),
                        asset_path="ZZZAsset"),
    }
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda asset_type, _root: indexes[asset_type])
    context = _context(tmp_path, {
        "mod.ini": (
            "[TextureOverrideComponent01]\n"
            "hash = aaaaaaaa\n"
            "drawindexed = 3, 0, 0\n"
        ),
    })
    context.asset_folders = [
        {"type": "GIMI", "path": "gimi-root", "enabled": True},
        {"type": "ZZMI", "path": "zzmi-root", "enabled": True},
    ]

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "asset_ambiguous"
    assert not plan.missing_parts


def test_candidate_matching_reuses_indexes_and_unique_hash_support(
        tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12)), asset_path="AssetA")
    index["assets"].append({
        "path": "AssetB",
        "geometry": [_geometry("bbbbbbbb", (0, 12))],
    })
    index["byGeometryHash"]["bbbbbbbb"] = [{"asset": 1, "geometry": 0}]
    calls = []

    def load_index(asset_type, root):
        calls.append((asset_type, root))
        return index

    monkeypatch.setattr(asset_index, "load_index", load_index)
    context = _context(tmp_path, {
        "mod.ini": (
            _zzmi("aaaaaaaa")
            + _zzmi("aaaaaaaa").replace(
                "TextureOverrideComponent01", "TextureOverrideComponent02")
            + _zzmi("bbbbbbbb").replace(
                "TextureOverrideComponent01", "TextureOverrideHair")
        ),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "asset_ambiguous"
    assert calls == [("ZZMI", "asset-root")]


def test_plan_matches_requested_range_only(tmp_path, monkeypatch):
    index = _index(_geometry(
        "aaaaaaaa", (0, 12), (300, 24), (600, 18)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi(
            "aaaaaaaa", "match_first_index = 300\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "ready"
    assert [part.first_index for part in plan.covered_parts] == [300]
    assert [part.first_index for part in plan.missing_parts] == [0, 600]


def test_plan_matches_range_start_when_index_count_differs(
        tmp_path, monkeypatch):
    index = _index(_geometry(
        "aaaaaaaa", (0, 12), (300, 24)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi(
            "aaaaaaaa",
            "match_first_index = 300\n"
            "match_index_count = 12\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert [part.first_index for part in plan.covered_parts] == [300]
    assert [part.first_index for part in plan.missing_parts] == [0]


def test_hash_only_component_identity_covers_all_ranges(tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12), (300, 24)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": "[TextureOverrideComponent01IB]\nhash = aaaaaaaa\n",
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert len(plan.covered_parts) == 2


def test_plan_reads_staged_document_projection(tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12), (300, 24)),
                   _geometry("bbbbbbbb", (0, 6)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    filename = tmp_path / "mod.ini"
    text = (_zzmi("bbbbbbbb") + "[TextureOverrideComponent02]\n"
            "hash = aaaaaaaa\nps-t0 = ResourceSurfaceDiffuse\n")
    filename.write_text(text, encoding="utf-8")
    saved_bytes = filename.read_bytes()
    path = str(filename)
    document = IniDocument.from_string(text, path=path)
    def plan():
        context = snapshot_context(str(tmp_path), [path], {path: document})
        context.asset_folders = [
            {"type": "ZZMI", "path": "asset-root", "enabled": True}]
        return asset_composition.plan_missing_asset_parts(context)
    assert [part.first_index for part in plan().missing_parts] == [0, 300]
    document.insert_lines(len(document.lines), ["handling = skip"])
    assert plan().status == "nothing_missing"
    assert filename.read_bytes() == saved_bytes


def test_plan_refuses_duplicate_original_assets(tmp_path, monkeypatch):
    indexes = {
        "one": _index(_geometry("aaaaaaaa", (0, 12)), asset_path="One"),
        "two": _index(_geometry("aaaaaaaa", (0, 12)), asset_path="Two"),
    }
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda _type, root: indexes[root])
    context = _context(tmp_path, {"mod.ini": _zzmi("aaaaaaaa")},
                       roots=("one", "two"))

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "asset_ambiguous"
    assert not plan.missing_parts


def test_plan_refuses_equal_support_for_different_original_assets(
        tmp_path, monkeypatch):
    indexes = {
        "one": _index(_geometry("aaaaaaaa", (0, 12)), asset_path="One"),
        "two": _index(_geometry("bbbbbbbb", (0, 12)), asset_path="Two"),
    }
    monkeypatch.setattr(
        asset_index, "load_index",
        lambda _type, root: indexes[root])
    context = _context(tmp_path, {
        "mod.ini": (
            "[TextureOverrideComponent01]\n"
            "hash = aaaaaaaa\n"
            "drawindexed = 3, 0, 0\n"
            "run = CommandList\\ZZMI\\SetTextures\n"
            "[TextureOverrideComponent02]\n"
            "hash = bbbbbbbb\n"
            "drawindexed = 3, 0, 0\n"
            "run = CommandList\\ZZMI\\SetTextures\n"
        ),
    }, roots=("one", "two"))

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "asset_ambiguous"
