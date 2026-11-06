"""Asset-only texture Export preserves geometry, ownership and atomic updates."""

import copy
import json
import shutil
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest

from app.assets import index, loader, texture_assignments
from app.assets.package_format import FORMAT
from app.bridge.api import ModViewerAPI
from app.settings import paths
from app.mods.analysis import build_mod_ini_snapshot
from app.mods.loader import ModLoadContext, load_mod
from core.geometry.transport import GeometryBlob
from core.materials.game_profile import GameDetection
from tests.app.assets.test_loader import _text_vb, _wwmi_triangle, _index_text
from tests.support.model_data import standard_component_resources, triangle_geometry


@pytest.fixture
def asset_case(tmp_path, monkeypatch, request):
    variant = getattr(request, "param", "ZZMI")
    kind = variant.split("-")[0]
    root = tmp_path / "library"
    asset = root / "Asset01"
    for ordinal, name in enumerate(("Surface01", "Surface02")):
        folder = asset / f"{ordinal + 1:08x}"
        folder.mkdir(parents=True)
        base = "Components-0 t=base.png" if kind == "WWMI" else "base.png"
        light = "Components-0 t=light.png" if kind == "WWMI" else "light.png"
        for filename, color in ((base, (100, 120, 140)), (light, (200, 50, 100)),
                                ("chosen.png", (80, 100, 120))):
            Image.new("RGB", (4, 4), color).save(folder / filename)
        textures = [["Diffuse", ".png", "33333333", base],
                    ["LightMap", ".png", "44444444", light]]
        if kind == "WWMI":
            _wwmi_triangle(folder, component_name=name, vb_hash=f"{ordinal + 1:08x}")
            path = folder / "Metadata.json"
            value = json.loads(path.read_text())
            if variant != "WWMI-native":
                value["format"] = FORMAT
            value["components"][0]["texture_hashes"] = [textures]
            path.write_text(json.dumps(value))
        else:
            _text_vb(folder / f"{name}-vb0={ordinal + 1:08x}.txt", 32, [
                ((0, 0, 0), (0, 0, 1), (0, 0)),
                ((1, 0, 0), (0, 0, 1), (1, 0)),
                ((0, 1, 0), (0, 0, 1), (0, 1))])
            (folder / f"{name}-ib={ordinal + 10:08x}.txt").write_text(_index_text([0, 1, 2]))
            component = dict(
                component_name=name, ib=f"{ordinal + 10:08x}", position_vb=f"{ordinal + 1:08x}",
                object_indexes=[0], object_index_counts=[3], texture_hashes=[textures])
            (folder / "hash.json").write_text(json.dumps(dict(
                format=FORMAT, layout="geometry-objects", position_vb=f"{ordinal + 1:08x}", components=[component])))
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps(dict(version=1, modFolders=[], assetFolders=[dict(
        type=kind, path=str(root), enabled=True)])))
    monkeypatch.setattr(paths, "config_path", lambda: str(configuration))
    monkeypatch.setattr(paths, "asset_index_dir", lambda: str(tmp_path / "index-cache"))
    index.save_index(index.build_index(kind, str(root)))
    api = ModViewerAPI()
    return SimpleNamespace(api=api, root=root, asset=asset, kind=kind)


def _snapshot(payload):
    fields = dict(diffuse="tex_key", normal_map="normal_map_key", normal_data="normal_data_key",
                  light_map="light_map_key", material_map="material_map_key", emission_map="emission_map_key")
    return {key: {role: mesh.get(field) for role, field in fields.items()}
            for key, mesh in payload["meshes"].items()}


def _changes(before, after):
    original = _snapshot(before)
    return {part: roles for part, values in after.items()
            if (roles := {role: key for role, key in values.items() if key != original[part][role]})}


def _metadata(case):
    return case.asset / "00000001" / ("Metadata.json" if case.kind == "WWMI" else "hash.json")


def _native_snapshot(case):
    return {p: p.read_bytes() for p in case.asset.rglob("*.json")}


def _pick(case, role=None):
    case.api._window = SimpleNamespace(create_file_dialog=lambda *_a, **_k: [str(case.asset / "00000001/chosen.png")])
    result = case.api.pick_asset_texture_file(str(case.asset), role)
    assert "error" not in result
    return result


def _automatic_bindings(case, tmp_path, monkeypatch):
    mod = tmp_path / "mod"
    mod.mkdir(exist_ok=True)
    for name, data in triangle_geometry().items():
        (mod / name).write_bytes(data)
    game = {"GIMI": "genshin", "ZZMI": "zzz", "WWMI": "wuwa"}[case.kind]
    monkeypatch.setattr("app.mods.analysis.resolve_game_detection", lambda *_a, **_k: GameDetection(
        game=game, runtime=case.kind.lower(), texture_api=case.kind.lower(), confidence="high", scores={}))
    text = ""
    for ordinal, name in enumerate(("Surface01", "Surface02")):
        geometry_hash = f"{ordinal + (1 if case.kind == 'WWMI' else 10):08x}"
        text += (f"[TextureOverride{name}]\nhash = {geometry_hash}\nmatch_first_index = 0\nmatch_index_count = 3\n"
                 "vb0 = ResourceComponent01Position\nvb1 = ResourceComponent01Texcoord\n"
                 "ib = ResourceComponent01IB\ndrawindexed = 3,0,0\n")
    text += standard_component_resources(position_file="p.buf", texcoord_file="t.buf", ib_file="i.buf")
    ini = mod / "mod.ini"
    ini.write_text(text)
    context = ModLoadContext(str(mod), build_mod_ini_snapshot([str(ini)], str(mod)),
                             asset_folders=[dict(type=case.kind, path=str(case.root), enabled=True)])
    payload = load_mod(context=context, geometry=GeometryBlob(), texture_source=lambda _path, role: f"/texture/test/{role}")
    assert payload["asset_resolution"]["exact_draws"] == 2
    assert ini.read_text() == text
    return {mesh["identity"]["geometry"]["hash"]: _snapshot({"meshes": {"part": mesh}})["part"]
            for mesh in payload["meshes"].values()}


@pytest.mark.parametrize("asset_case", ["GIMI", "ZZMI", "WWMI", "WWMI-native"], indirect=True)
def test_asset_assignments_export_reopen_clear_and_portable_copy(asset_case, tmp_path, monkeypatch):
    case = asset_case
    originals = {p.relative_to(case.asset): p.read_bytes() for p in case.asset.rglob("*") if p.is_file()}
    before = case.api.load_asset(str(case.asset))
    token = before["metadata"]["asset"]["texture_session"]
    roles = _snapshot(before)
    first, second = roles
    chosen = _pick(case)
    normal = _pick(case, "normal_map")
    emission = _pick(case, "emission_map")
    assert normal["role"] == ("normal_data" if case.kind == "WWMI" else "normal_map")
    roles[first].update(diffuse=chosen["tex_key"], light_map=None, emission_map=emission["tex_key"])
    roles[first][normal["role"]] = normal["tex_key"]
    old_automatic = _automatic_bindings(case, tmp_path, monkeypatch)
    old_index = index.snapshot_index(case.kind, str(case.root))
    assert case.api.export_asset_textures(str(case.asset), token, _changes(before, roles)) == {"saved": 1}
    assert {p.relative_to(case.asset) for p in case.asset.rglob("*") if p.is_file()} == set(originals)
    assert all((case.asset / p).read_bytes() == data for p, data in originals.items() if p.suffix != ".json")
    native = json.loads(_metadata(case).read_bytes())["components"][0]
    assert ["Diffuse", ".png", "", "chosen.png"] in native["texture_hashes"][0]
    assert "diffuse" in native["texture_assignment_roles"]["0"]
    assert all(item[2] == "" for item in native["texture_hashes"][0]
               if item[0] in ("NormalMap", "EmissionMap") and item[3] == "chosen.png")
    assert str(case.root) not in _metadata(case).read_text()
    assert index.snapshot_index(case.kind, str(case.root)) != old_index
    assert not (case.asset / "texture_assignments.json").exists()
    for relative, data in originals.items():
        if relative.suffix != ".json":
            continue
        old = json.loads(data)
        current = json.loads((case.asset / relative).read_bytes())
        assert {key: value for key, value in current.items() if key != "components"} == {
            key: value for key, value in old.items() if key != "components"}
        for old_entry, new_entry in zip(old["components"], current["components"]):
            assert {key: value for key, value in new_entry.items()
                    if key not in ("texture_hashes", "texture_assignment_roles")} == {
                key: value for key, value in old_entry.items() if key != "texture_hashes"}
    assert not list(case.asset.rglob("*.ini")) and not (case.asset / ".mod_viewer.json").exists()
    after = case.api.load_asset(str(case.asset))
    assert _snapshot(after) == roles
    assert _snapshot(after)[second] == _snapshot(before)[second]
    second_metadata = next(p for p in originals if p.suffix == ".json" and p.parts[0] == "00000002")
    assert (case.asset / second_metadata).read_bytes() == originals[second_metadata]
    automatic = _automatic_bindings(case, tmp_path, monkeypatch)
    first_hash = next(iter(before["meshes"].values()))["asset_source"]["geometry_hash"]
    assert automatic[first_hash] == roles[first]
    assert automatic[first_hash]["diffuse"] != old_automatic[first_hash]["diffuse"]
    copied_root = tmp_path / "copied-library"
    shutil.copytree(case.asset, copied_root / "RenamedAsset")
    record, = index.build_index(case.kind, str(copied_root))["assets"]
    loaded = loader.load_asset_parts(case.kind, str(copied_root), record)
    assert set(loaded.parts[0].textures) == {"diffuse", normal["role"], "emission_map"}
    assert all(t.source == "explicit" and str(copied_root) in t.path
               for t in loaded.parts[0].textures.values())
    assert set(loaded.parts[1].textures) == {role for role, key in roles[second].items() if key}
    token = after["metadata"]["asset"]["texture_session"]
    roles[first] = dict.fromkeys(texture_assignments.ROLES)
    assert case.api.export_asset_textures(str(case.asset), token, _changes(after, roles)) == {"saved": 1}
    assert _snapshot(case.api.load_asset(str(case.asset))) == roles
    assert all(value is None for value in _automatic_bindings(case, tmp_path, monkeypatch)[first_hash].values())


def test_asset_export_rejects_untrusted_stale_and_wrong_role_requests(asset_case, tmp_path, monkeypatch):
    case = asset_case
    assert "error" in case.api.export_asset_textures(str(case.asset), "invented", {})
    payload = case.api.load_asset(str(case.asset))
    token = payload["metadata"]["asset"]["texture_session"]
    original = _snapshot(payload)
    original_metadata = _native_snapshot(case)
    first = next(iter(original))
    attacks = []
    for role, value in (("diffuse", str(case.asset / "00000001/chosen.png")),
                        ("diffuse", "diffuse::asset/invented/file.png"),
                        ("diffuse", original[first]["light_map"]),
                        ("diffuse", 42), ("unknown", None)):
        changed = copy.deepcopy(original)
        changed[first][role] = value
        attacks.append(changed)
    attacks.extend(({}, {"invented-part": original[first]}))
    for changed in attacks:
        assert "error" in case.api.export_asset_textures(str(case.asset), token, changed)
    assert "error" in case.api.export_asset_textures(str(tmp_path), token, original)
    assert _native_snapshot(case) == original_metadata
    outside = tmp_path / "outside.png"
    Image.new("RGB", (4, 4)).save(outside)
    case.api._window = SimpleNamespace(create_file_dialog=lambda *_a, **_k: [str(outside)])
    assert "error" in case.api.pick_asset_texture_file(str(case.asset))
    case.api.load_asset(str(case.asset))
    assert "error" in case.api.export_asset_textures(str(case.asset), token, original)
    fresh = case.api.load_asset(str(case.asset))
    monkeypatch.setattr(case.api._mod_preview, "load_mod", lambda *_: {})
    case.api.load_mod("mod-01")
    assert "error" in case.api.export_asset_textures(
        str(case.asset), fresh["metadata"]["asset"]["texture_session"], _snapshot(fresh))


def test_asset_assignment_atomic_failure_retry_and_external_revision(asset_case, monkeypatch):
    case = asset_case
    payload = case.api.load_asset(str(case.asset))
    token = payload["metadata"]["asset"]["texture_session"]
    roles = _snapshot(payload)
    assert case.api.export_asset_textures(str(case.asset), token, roles) == {"saved": 2}
    previous = _native_snapshot(case)
    previous_index = index.snapshot_index(case.kind, str(case.root))
    changed = copy.deepcopy(roles)
    changed[next(iter(changed))]["diffuse"] = _pick(case)["tex_key"]
    original_replace = texture_assignments.os.replace
    calls = 0
    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Synthetic write failure")
        return original_replace(source, destination)
    monkeypatch.setattr(texture_assignments.os, "replace", fail_second)
    assert "error" in case.api.export_asset_textures(str(case.asset), token, changed)
    assert _native_snapshot(case) == previous
    assert index.snapshot_index(case.kind, str(case.root)) == previous_index
    assert not list(case.asset.rglob(".asset-textures-*.tmp"))
    monkeypatch.setattr(texture_assignments.os, "replace", original_replace)
    assert case.api.export_asset_textures(str(case.asset), token, changed) == {"saved": 2}
    filename = _metadata(case)
    external = json.loads(filename.read_bytes())
    external["note"] = "external revision"
    filename.write_text(json.dumps(external))
    assert "error" in case.api.export_asset_textures(str(case.asset), token, roles)
    assert json.loads(filename.read_bytes()) == external


def test_concurrent_asset_previews_do_not_overwrite_a_newer_export(asset_case):
    case = asset_case
    other = ModViewerAPI()
    first = case.api.load_asset(str(case.asset))
    second = other.load_asset(str(case.asset))
    jobs = [(case.api, first), (other, second)]
    def save(job):
        api, payload = job
        return api.export_asset_textures(str(case.asset), payload["metadata"]["asset"]["texture_session"], _snapshot(payload))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, jobs))
    assert sum("error" in result for result in results) == 1
    assert sum(result == {"saved": 2} for result in results) == 1


@pytest.mark.parametrize("failure", ["build", "save"])
def test_native_metadata_and_index_roll_back_together(asset_case, monkeypatch, failure):
    case = asset_case
    payload = case.api.load_asset(str(case.asset))
    token = payload["metadata"]["asset"]["texture_session"]
    roles = _snapshot(payload)
    roles[next(iter(roles))]["diffuse"] = _pick(case)["tex_key"]
    previous, previous_index = _native_snapshot(case), index.snapshot_index(case.kind, str(case.root))
    name = "build_index" if failure == "build" else "save_index"
    original = getattr(index, name)
    def fail(*args, **kwargs):
        if failure == "save":
            original(*args, **kwargs)
        raise OSError("Synthetic index failure")
    monkeypatch.setattr(index, name, fail)
    assert "error" in case.api.export_asset_textures(str(case.asset), token, roles)
    assert _native_snapshot(case) == previous
    assert index.snapshot_index(case.kind, str(case.root)) == previous_index
    assert not list(case.asset.rglob(".asset-textures-*.tmp"))
    monkeypatch.setattr(index, name, original)
    assert case.api.export_asset_textures(str(case.asset), token, roles) == {"saved": 2}


def test_relabeling_a_captured_candidate_preserves_its_native_hash(asset_case):
    case = asset_case
    filename = _metadata(case)
    value = json.loads(filename.read_bytes())
    row = value["components"][0]["texture_hashes"][0]
    row.append(["TextureSlot02", ".png", "55555555", "chosen.png"])
    filename.write_text(json.dumps(value))
    index.save_index(index.build_index(case.kind, str(case.root)))
    payload = case.api.load_asset(str(case.asset))
    roles = _snapshot(payload)
    roles[next(iter(roles))]["diffuse"] = _pick(case)["tex_key"]
    assert case.api.export_asset_textures(
        str(case.asset), payload["metadata"]["asset"]["texture_session"], roles) == {"saved": 2}
    row = json.loads(filename.read_bytes())["components"][0]["texture_hashes"][0]
    assert ["Diffuse", ".png", "55555555", "chosen.png"] in row
    assert not any(item[0] == "Diffuse" and item[2] == "33333333" for item in row)


@pytest.mark.parametrize("container", ["list", "records", "components"])
def test_native_json_entry_and_range_provenance_is_preserved(asset_case, container):
    case = asset_case
    components = []
    for path in sorted(case.asset.rglob("hash.json")):
        component = json.loads(path.read_bytes())["components"][0]
        for row in component["texture_hashes"]:
            for item in row:
                item[3] = path.parent.name + "/" + item[3]
        components.append(component)
        path.unlink()
    first = components[0]
    first.update(object_indexes=[0, 3], object_index_counts=[3, 3], object_classifications=["A", "B"])
    first["texture_hashes"].append(copy.deepcopy(first["texture_hashes"][0]))
    first["textureHashes"] = first.pop("texture_hashes")
    (case.asset / "00000001/Surface01B-ib=0000000a.txt").write_text(
        _index_text([0, 1, 2]).replace("first index: 0", "first index: 3"))
    document = components if container == "list" else {container: components, "custom": "retained"}
    filename = case.asset / "hash.json"
    filename.write_text(json.dumps(document))
    index.save_index(index.build_index(case.kind, str(case.root)))
    payload = case.api.load_asset(str(case.asset))
    roles = _snapshot(payload)
    assert len(roles) == 3
    first_key, second_key, third_key = roles
    roles[first_key]["diffuse"] = None
    roles[second_key]["diffuse"] = _pick(case)["tex_key"]
    assert case.api.export_asset_textures(str(case.asset), payload["metadata"]["asset"]["texture_session"], _changes(payload, roles)) == {"saved": 2}
    actual = json.loads(filename.read_bytes())
    entries = actual if container == "list" else actual[container]
    assert "texture_hashes" not in entries[0]
    assert ["Diffuse", "", "", None] in entries[0]["textureHashes"][0]
    assert ["Diffuse", ".png", "", "00000001/chosen.png"] in entries[0]["textureHashes"][1]
    if container != "list":
        assert actual["custom"] == "retained"
    assert _snapshot(case.api.load_asset(str(case.asset))) == roles
    assert roles[third_key]["diffuse"] is not None


def test_asset_metadata_and_texture_symlinks_cannot_escape(asset_case, tmp_path):
    case = asset_case
    outside = tmp_path / "outside.json"
    metadata = _metadata(case)
    original = metadata.read_bytes()
    outside.write_bytes(original)
    metadata.unlink()
    try:
        metadata.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable")
    record = {"path": "Asset01", "geometry": [{"metadata": "Asset01/00000001/hash.json"}]}
    with pytest.raises(ValueError, match="escape"):
        texture_assignments.capture(str(case.root), record)
    metadata.unlink()
    metadata.write_bytes(original)
    payload = case.api.load_asset(str(case.asset))
    roles = _snapshot(payload)
    chosen = _pick(case)
    selected = case.asset / "00000001/chosen.png"
    selected.unlink()
    selected.symlink_to(outside)
    roles[next(iter(roles))]["diffuse"] = chosen["tex_key"]
    assert "error" in case.api.export_asset_textures(str(case.asset), payload["metadata"]["asset"]["texture_session"], roles)


@pytest.mark.parametrize("asset_case", ["GIMI", "ZZMI", "WWMI"], indirect=True)
def test_editing_one_part_preserves_unavailable_and_unedited_roles(asset_case):
    case = asset_case
    filename = _metadata(case)
    value = json.loads(filename.read_bytes())
    missing = ["NormalMap", ".dds", "66666666", "missing-normal.dds"]
    value["components"][0]["texture_hashes"][0].append(missing)
    filename.write_text(json.dumps(value))
    index.save_index(index.build_index(case.kind, str(case.root)))
    payload = case.api.load_asset(str(case.asset))
    roles = _snapshot(payload)
    first, second = roles
    original = filename.read_bytes()
    second_file = case.asset / "00000002" / filename.name
    old_rows = json.loads(second_file.read_bytes())["components"][0]["texture_hashes"][0]
    roles[second]["diffuse"] = _pick(case)["tex_key"]
    assert case.api.export_asset_textures(str(case.asset), payload["metadata"]["asset"]["texture_session"],
                                          _changes(payload, roles)) == {"saved": 1}
    assert filename.read_bytes() == original
    assert missing in json.loads(filename.read_bytes())["components"][0]["texture_hashes"][0]
    updated = json.loads(second_file.read_bytes())["components"][0]
    assert all(row in updated["texture_hashes"][0] for row in old_rows if row[0] != "Diffuse")
    assert updated["texture_assignment_roles"] == {"0": ["diffuse"]}
    reopened = case.api.load_asset(str(case.asset))
    assert _snapshot(reopened) == roles
    assert roles[first]["normal_map"] is None and roles[first]["normal_data"] is None
    # Editing another role of the affected part must retain its unavailable map.
    assert case.api.export_asset_textures(str(case.asset), reopened["metadata"]["asset"]["texture_session"],
                                          {first: {"diffuse": roles[second]["diffuse"]}}) == {"saved": 1}
    updated = json.loads(filename.read_bytes())["components"][0]
    assert missing in updated["texture_hashes"][0]
    assert updated["texture_assignment_roles"] == {"0": ["diffuse"]}


@pytest.mark.parametrize("asset_case", ["GIMI", "ZZMI", "WWMI"], indirect=True)
def test_nested_picked_image_reopens_and_supplies_automatic_defaults(asset_case, tmp_path, monkeypatch):
    case = asset_case
    payload = case.api.load_asset(str(case.asset))
    roles = _snapshot(payload)
    first = next(iter(roles))
    image = case.asset / "00000001/Textures/High/chosen.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), (90, 110, 130)).save(image)
    case.api._window = SimpleNamespace(create_file_dialog=lambda *_a, **_k: [str(image)])
    picked = case.api.pick_asset_texture_file(str(case.asset))
    roles[first]["diffuse"] = picked["tex_key"]
    assert case.api.export_asset_textures(str(case.asset), payload["metadata"]["asset"]["texture_session"],
                                          _changes(payload, roles)) == {"saved": 1}
    assert _snapshot(case.api.load_asset(str(case.asset))) == roles
    geometry_hash = payload["meshes"][first]["asset_source"]["geometry_hash"]
    assert _automatic_bindings(case, tmp_path, monkeypatch)[geometry_hash] == roles[first]
