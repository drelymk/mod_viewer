"""Coverage planning regressions for explicit original Asset filling."""

from types import SimpleNamespace

from app import asset_composition, asset_index
from core.component_coverage import collect_component_overrides
from core.ini_document import IniDocument


def _index(*geometries, asset_path="Character"):
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
        "metadata": "Character/hash.json",
        "componentName": hash_value,
    }


def _context(tmp_path, texts, roots=("asset-root",)):
    paths = []
    for name, text in texts.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    return SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=paths, docs={},
        asset_folders=[{"type": "ZZMI", "path": root, "enabled": True}
                       for root in roots],
    )


def _zzmi(hash_value, extra=""):
    return (
        "[TextureOverrideBody]\n"
        f"hash = {hash_value}\n"
        "run = CommandList\\ZZMI\\SetTextures\n"
        f"{extra}"
    )


def test_collect_component_overrides_includes_skip_and_range():
    result = collect_component_overrides({
        "TextureOverrideFace": [
            "hash = 0xAAAAAAAA",
            "match_first_index = 300",
            "match_index_count = 12",
            "handling = skip",
        ],
    }, "nested/face.ini")

    assert len(result) == 1
    assert result[0].key.geometry_hash == "aaaaaaaa"
    assert result[0].first_index == 300
    assert result[0].index_count == 12
    assert result[0].handling_skip is True


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
        "nested/face.ini": _zzmi(
            "bbbbbbbb", "handling = skip\nhash = deadbeef\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert len(plan.asset_parts) == 2
    assert len(plan.covered_parts) == 2
    assert not plan.missing_parts
    assert len(plan.skipped_parts) == 1


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


def test_hash_only_skip_covers_all_ranges(tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12), (300, 24)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    context = _context(tmp_path, {
        "mod.ini": _zzmi("aaaaaaaa", "handling = skip\n"),
    })

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"
    assert len(plan.skipped_parts) == 2


def test_plan_reads_staged_document_projection(tmp_path, monkeypatch):
    index = _index(_geometry("aaaaaaaa", (0, 12)))
    monkeypatch.setattr(asset_index, "load_index",
                        lambda _type, _root: index)
    path = str(tmp_path / "mod.ini")
    document = IniDocument.from_string(
        _zzmi("aaaaaaaa"), path=path)
    context = SimpleNamespace(
        mod_dir=str(tmp_path), ini_paths=[path], docs={path: document},
        asset_folders=[{"type": "ZZMI", "path": "asset-root",
                        "enabled": True}],
    )

    plan = asset_composition.plan_missing_asset_parts(context)

    assert plan.status == "nothing_missing"


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
