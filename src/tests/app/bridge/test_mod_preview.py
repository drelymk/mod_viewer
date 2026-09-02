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


@pytest.mark.parametrize(
    "disabled_ini, expected_name",
    [(False, "Active.ini"), (True, "DISABLEDActive.ini")],
)
def test_authoritative_context_discovers_only_selected_ini_mode(
        disabled_ini, expected_name, monkeypatch):
    preview = ModPreview(_Access())
    discovered = []
    ini_path = f"mod/{expected_name}"
    monkeypatch.setattr(
        "app.bridge.mod_preview.discover_ini_paths",
        lambda folder, *, disabled=False: discovered.append(disabled)
        or [ini_path],
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.edit_session.document_paths",
        lambda _folder: [],
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.edit_session.load_documents",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.edit_session.overrides_for",
        lambda _folder: {},
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.edit_session.new_sections_for",
        lambda _folder: {},
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.edit_session.documents_for",
        lambda _folder: {},
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.load",
        lambda _folder: {},
    )
    monkeypatch.setattr(
        "app.bridge.mod_preview.asset_folders.load_registry",
        lambda: [],
    )

    _folder, _overrides, _pending, context = preview.authoritative_context(
        "mod", disabled_ini=disabled_ini)

    assert discovered == [disabled_ini]
    assert context.ini_paths == [ini_path]


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
        lambda _folder, **_kwargs: ("mod", {}, {}, _context()))
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


def test_load_forwards_disabled_mode_to_authoritative_context(monkeypatch):
    publication = _Publication([])
    preview = ModPreview(_Access())
    context = _context()
    context.ini_paths = ["mod/DISABLEDActive.ini"]
    captured = {}

    def authoritative_context(_folder, *, disabled_ini=False):
        captured["disabled_ini"] = disabled_ini
        return "mod", {}, {}, context

    monkeypatch.setattr(preview, "authoritative_context", authoritative_context)
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.begin_texture_publication",
        lambda _folder: publication)

    def load_model(**kwargs):
        captured["context"] = kwargs["context"]
        return {
            "meshes": {"Body-1": {}},
            "metadata": {"game": {"id": "genshin"}},
            "controls": {"present": {}},
        }

    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_mod", load_model)
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.hydrate_textures",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.hydrate_present",
        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.publish_payload_geometry",
        lambda *_args, **_kwargs: None,
    )

    preview.load_mod("mod", disabled_ini=True)

    assert captured["disabled_ini"] is True
    assert captured["context"].ini_paths == ["mod/DISABLEDActive.ini"]


def test_load_reports_disabled_ini_error_when_discovery_is_empty(monkeypatch):
    publication = _Publication([])
    preview = ModPreview(_Access())
    context = _context()
    context.ini_paths = []
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder, **_kwargs: ("mod", {}, {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.server.begin_texture_publication",
        lambda _folder: publication)
    monkeypatch.setattr(
        "app.bridge.mod_preview.mod_loader.load_mod",
        lambda **_kwargs: {
            "error": "No active .ini files found in this folder.",
        })

    result = preview.load_mod("mod", disabled_ini=True)

    assert result["error"] == "No disabled .ini files found in this folder."
    assert publication.events == [("discard",)]


def test_failed_load_discards_publication_and_clears_active_meshes(monkeypatch):
    events = []
    publication = _Publication(events)
    preview = ModPreview(_Access())
    preview._active_mesh_keys["mod"] = {"old"}
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder, **_kwargs: ("mod", {}, {}, _context()))
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


def test_save_texture_color_forwards_complete_target_request(monkeypatch):
    preview = ModPreview(_Access())
    preview._active_mesh_keys["mod"] = {"Body-1", "Body-2"}
    context = _context()
    captured = []
    cleared = []
    monkeypatch.setattr(
        preview, "authoritative_context",
        lambda _folder: ("mod", {"override": 1}, {}, context))
    monkeypatch.setattr(
        "app.bridge.mod_preview.save_texture_color",
        lambda *args: captured.append(args) or {
            "status": "ok", "tex_key": "diffuse::body.dds",
        })
    monkeypatch.setattr(
        "app.bridge.mod_preview.metadata.clear_mesh_color_adjustments",
        lambda folder, keys: cleared.append((folder, keys)) or {"saved": True})

    targets = [{
        "semantic_key": "Body-1", "metadata_key": "Body::one",
        "adjustment": {"hue": 30},
    }]
    usage = [{
        "semantic_key": "Body-1",
        "texture_keys": {
            "diffuse": "diffuse::body.dds", "normal_map": None,
            "normal_data": None, "light_map": None,
            "material_map": None, "emission_map": None,
        },
    }]
    result = preview.save_texture_color(
        "mod", "diffuse::body.dds", targets, usage)

    assert result["status"] == "ok"
    assert captured == [(context, {"override": 1}, {"Body-1", "Body-2"},
                         "diffuse::body.dds", targets, usage)]
    assert cleared == [("mod", ["Body::one"])]
