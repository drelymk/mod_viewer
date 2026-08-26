from types import SimpleNamespace

from app.bridge.asset_preview import AssetPreview


class _Access:
    def asset_folder(self, path):
        return path

    def mod_folder(self, path):
        return path


class _Publication:
    def __init__(self, events):
        self.events = events

    def register(self, *args, **kwargs):
        return "/texture/0"

    def set_game_profile(self, value):
        self.events.append(("profile", value))

    def commit(self, **kwargs):
        self.events.append(("commit", kwargs))

    def discard(self):
        self.events.append(("discard",))


def test_direct_asset_load_publishes_geometry_before_commit(monkeypatch):
    events = []
    publication = _Publication(events)
    preview = AssetPreview(_Access())
    monkeypatch.setattr(
        preview, "_asset_selection",
        lambda _folder: ("asset", {"type": "GIMI", "path": "root"},
                         {}, {"path": "Character"}),
    )
    monkeypatch.setattr(
        "app.bridge.asset_preview.server.begin_texture_publication",
        lambda _folder: publication)
    monkeypatch.setattr(
        "app.bridge.asset_preview.asset_loader.load_asset",
        lambda *_args, **_kwargs: SimpleNamespace(
            payload={"meshes": {"Body": {}}}),
    )
    monkeypatch.setattr(
        "app.bridge.asset_preview.server.publish_payload_geometry",
        lambda *_args, **_kwargs: events.append(("publish",)),
    )

    result = preview.load_asset("asset")

    assert result == {"meshes": {"Body": {}}}
    assert [event[0] for event in events] == ["profile", "publish", "commit"]


def test_asset_fill_uses_non_replacing_publication(monkeypatch):
    events = []
    publication = _Publication(events)
    preview = AssetPreview(_Access())

    class Plan:
        status = "ready"
        asset_type = "GIMI"
        root = "asset-root"
        asset = {"path": "Character"}
        missing_parts = ("hair",)

        def to_dict(self):
            return {"status": self.status, "coverage": {}}

    monkeypatch.setattr(preview, "_asset_fill_plan", lambda *_args: Plan())
    monkeypatch.setattr(
        "app.bridge.asset_preview.server.begin_texture_publication",
        lambda _folder: publication)
    monkeypatch.setattr(
        "app.bridge.asset_preview.asset_loader.load_asset_parts",
        lambda *_args, **_kwargs: SimpleNamespace(parts=["hair"], warnings=[]),
    )
    monkeypatch.setattr(
        "app.bridge.asset_preview.build_asset_fill_payload",
        lambda *_args, **_kwargs: {"geometry": {"url": "/geometry/fill"},
                                   "meshes": {"Hair": {}}},
    )
    published = []
    monkeypatch.setattr(
        "app.bridge.asset_preview.server.publish_payload_geometry",
        lambda *_args, **kwargs: published.append(kwargs),
    )

    result = preview.load_missing_asset_parts("mod", SimpleNamespace(), {})

    assert result["status"] == "loaded"
    assert published == [{"replace": False}]
    assert [event[0] for event in events] == ["profile", "commit"]
