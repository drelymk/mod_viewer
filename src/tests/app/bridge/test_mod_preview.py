from types import SimpleNamespace

import pytest

from app.bridge.mod_preview import ModPreview
from core.geometry.skinning import SkinningSource


class _Access:
    def mod_folder(self, path):
        return path


class _Publication:
    def __init__(self, events):
        self.events = events
        self.game_profile = "unknown"

    def register(self, *args, **kwargs):
        return "/texture/0"

    def set_game_profile(self, value):
        self.events.append(("profile", value))
        self.game_profile = value

    def commit(self, **kwargs):
        self.events.append(("commit", kwargs))

    def discard(self):
        self.events.append(("discard",))


def _context():
    return SimpleNamespace(metadata={}, asset_folders=[])


def test_model_skinning_preview_includes_validated_saved_bones(monkeypatch):
    preview = ModPreview(_Access())
    context = _context()
    context.metadata = {
        "weight": {"selected_bones": [{
            "source": "Hair\\HairBlend.buf", "bone_id_offset": 0,
            "bone_ids": [9, True, -1, 7, 9],
        }]},
    }
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder: ("mod", {}, {}, context))
    monkeypatch.setattr(
        preview, "_skinning_draws",
        lambda *_args: (
            SimpleNamespace(game=SimpleNamespace(game="genshin")), {}))

    result = preview.get_model_skinning_preview("mod")

    assert result["status"] == "error"
    assert result["saved_bones"] == [{
        "source": "Hair/HairBlend.buf", "bone_id_offset": 0,
        "bone_ids": [7, 9],
    }]


def test_single_and_bulk_skin_entries_share_source_descriptor():
    draw = SimpleNamespace(skinning_source=SkinningSource(
        file=r"Hair\HairBlend.buf", stride=8, influence_count=4,
        encoding="wwmi_u8_4", bone_id_offset=24))
    decoded = SimpleNamespace(
        indices=b"index", weights=b"weight", vertex_count=1,
        influence_count=1, bone_ids=(45,), diagnostics={})

    single, _single_blob = ModPreview._skin_entry(decoded, draw, 0)
    bulk, _bulk_blob = ModPreview._skin_entry(decoded, draw, 11)

    assert single["source"] == bulk["source"] == {
        "key": "hair/hairblend.buf|offset=24",
        "file": "Hair/HairBlend.buf",
        "bone_id_offset": 24,
    }


def test_load_commits_texture_publication_after_geometry(monkeypatch):
    events = []
    publication = _Publication(events)
    preview = ModPreview(_Access())
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder: ("mod", {}, {}, _context()))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.begin_texture_publication",
        lambda _folder: publication)
    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_mod",
        lambda **_kwargs: {
            "meshes": {"Body-1": {}},
            "metadata": {"game": {"id": "genshin"}},
            "controls": {"present": {}},
        })
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.hydrate_textures",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.hydrate_present",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_payload_geometry",
        lambda *_args, **_kwargs: events.append(("publish",)),
    )

    result = preview.load_mod("mod")

    assert result["meshes"] == {"Body-1": {}}
    assert [event[0] for event in events] == ["profile", "publish", "commit"]
    assert preview._active_mesh_keys == {"mod": {"Body-1"}}


def test_failed_load_discards_publication_and_clears_active_meshes(monkeypatch):
    events = []
    publication = _Publication(events)
    preview = ModPreview(_Access())
    preview._active_mesh_keys["mod"] = {"old"}
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder: ("mod", {}, {}, _context()))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.begin_texture_publication",
        lambda _folder: publication)
    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_mod",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("load failed")),
    )

    with pytest.raises(RuntimeError, match="load failed"):
        preview.load_mod("mod")

    assert events == [("discard",)]
    assert "mod" not in preview._active_mesh_keys


def test_semantic_control_read_reuses_active_mesh_keys(monkeypatch):
    preview = ModPreview(_Access())
    preview._active_mesh_keys["mod"] = {"Body-1"}
    captured = []
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder: ("mod", {}, {"KeyNew"}, _context()))
    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_control_state",
        lambda *args, **kwargs: captured.append((args, kwargs)) or {
            "controls": {"present": {}},
        })
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.hydrate_present",
        lambda *_args, **_kwargs: None)

    result = preview.get_control_state("mod")

    assert result["controls"]["present"] == {}
    assert captured[0][1]["active_mesh_keys"] == {"Body-1"}
