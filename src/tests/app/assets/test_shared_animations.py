import hashlib
import json
import struct

import pytest

from app.assets import shared_animations
from app.assets.animations import AnimationSessions


SOURCE = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Spine01", parent=0, translation=[0, 0, 10], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Accessory01", parent=1, translation=[1, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Accessory02", parent=2, translation=[1, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])]
# The target reuses Accessory names on its own chain: same names, another rest pose.
TARGET = [dict(name="Root", parent=-1, translation=[0, 0, 0], rotation=[0, 0, 0, -1], scale=[1, 1, 1]),
          dict(name="Spine01", parent=0, translation=[0, 0, 10.00001], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Extra01", parent=1, translation=[0, 2, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Accessory01", parent=1, translation=[3, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1]),
          dict(name="Accessory02", parent=3, translation=[1, 0, 0], rotation=[0, 0, 0, 1], scale=[1, 1, 1])]
RIG = dict(id="a" * 64, bones=TARGET)


def frames_for(rows):
    return b"".join(struct.pack("<7f", *row) for frame in rows for row in frame)


def write_library(root, name, *, bones=SOURCE, label="Set01", clips=None, fingerprint="build-1"):
    directory = root / shared_animations.DIRECTORY / name
    directory.mkdir(parents=True)
    sid = shared_animations.skeleton_id(shared_animations.validate_skeleton(bones))
    clips = clips if clips is not None else {"Walk01": [0, 1, 2], "Wave01": [0, 1, 2, 3]}
    entries = []
    for key, keyed in clips.items():
        frames = [[[*bone["translation"][:2], bone["translation"][2] + frame, 0, 0, 0, 1] for bone in bones]
                  for frame in range(2)]
        data = frames_for(frames)
        (directory / (key + ".bin")).write_bytes(data)
        (directory / (key + ".json")).write_text(json.dumps(dict(
            format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
            skeleton_id=sid, frame_count=2, bone_count=len(bones), duration=1, data_file=key + ".bin",
            sha256=hashlib.sha256(data).hexdigest(), keyed_bones=keyed,
            source="Client/Content/Aki/Character/Role/BodyType01/BaseAnim/" + key)))
        entries.append(dict(id=key, name=key, file=key + ".json", duration=1, keyed_bones=keyed))
    (directory / "manifest.json").write_text(json.dumps(dict(
        format=shared_animations.FORMAT, version=1, label=label, skeleton_id=sid, skeleton=bones, clips=entries,
        fingerprint=fingerprint)))
    return directory


def test_shared_library_maps_only_proven_joints_and_lists_after_asset_clips(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda url: None)
    own = tmp_path / "Asset01" / "Animation"
    own.mkdir(parents=True)
    data = frames_for([[[*bone["translation"], *bone["rotation"]] for bone in TARGET]] * 2)
    (own / "Idle01.bin").write_bytes(data)
    (own / "Idle01.json").write_text(json.dumps(dict(
        format="mod-viewer-animation", version=1, coordinate_space="asset", layout="frame-major-tq-f32le",
        skeleton_id=RIG["id"], frame_count=2, bone_count=len(TARGET), duration=1, data_file="Idle01.bin",
        sha256=hashlib.sha256(data).hexdigest())))
    (own / "manifest.json").write_text(json.dumps(dict(
        format="mod-viewer-animations", version=1, skeleton_id=RIG["id"],
        clips=[dict(id="idle01", name="Idle01", file="Idle01.json", duration=1)])))
    write_library(tmp_path, "Body01-Set01-0123456789")
    # A library whose root joint is not the rig's never applies, and a corrupt one is skipped alone.
    write_library(tmp_path, "Body01-Set02-0123456789", bones=[{**SOURCE[0], "name": "OtherRoot"}],
                  clips={"Other01": [0]})
    (tmp_path / shared_animations.DIRECTORY / "Body01-Set03-0123456789").mkdir()
    (tmp_path / shared_animations.DIRECTORY / "Body01-Set03-0123456789" / "manifest.json").write_text("{broken")

    mapping = shared_animations.bone_mapping(SOURCE, TARGET)
    assert mapping == {0: 0, 1: 1}
    sessions = AnimationSessions()
    catalog = sessions.catalog(str(tmp_path), "Asset01", RIG, lambda: True)
    # Asset clips come first. Wave01 animates an Accessory chain the rig only shares by name, so it is
    # not offered; Walk01 keys only that chain's end, which holds the rig's rest pose.
    assert [clip["name"] for clip in catalog["animations"]] == ["Idle01", "Set01 / Walk01"]
    assert [clip.get("shared", False) for clip in catalog["animations"]] == [False, True]
    assert sessions.load(catalog["animation_session"], "idle01")["status"] == "ok"
    clip_id = catalog["animations"][1]["id"]
    assert clip_id == shared_animations.catalog_id("Body01-Set01-0123456789", "Walk01")
    result = sessions.load(catalog["animation_session"], clip_id)
    assert result["status"] == "ok"
    clip = result["clip"]
    assert clip["skeleton_id"] == RIG["id"] and clip["bone_count"] == len(TARGET) and "morphs" not in clip
    assert shared_animations.structural_bones(SOURCE, [0, 1, 2]) == {0, 1}
    rows = list(struct.iter_unpack("<7f", published[-1]))
    assert len(rows) == 2 * len(TARGET)
    second = rows[len(TARGET):]
    # Proven joints carry the source motion; every other joint holds the rig's own rest pose.
    assert second[0][:3] == (0, 0, 1) and second[1][:3] == pytest.approx((0, 0, 11))
    assert second[2] == (0, 2, 0, 0, 0, 0, 1) and second[3][:3] == (3, 0, 0) and second[4][:3] == (1, 0, 0)
    # The sign-equivalent root rest still maps, so the root takes the source rotation.
    assert second[0][3:] == (0, 0, 0, 1)
    assert second[3] == rows[3]

    # A changed library binary fails only that load.
    (tmp_path / shared_animations.DIRECTORY / "Body01-Set01-0123456789" / "Walk01.bin").write_bytes(b"short")
    assert sessions.load(catalog["animation_session"], clip_id)["status"] == "error"


def test_asset_ribbon_layers_and_physics_cover_shared_clip_chains(tmp_path, monkeypatch):
    published = []
    monkeypatch.setattr("app.assets.animations.server.publish_geometry",
                        lambda blob, **_: published.append(blob) or f"/geometry/{len(published)}")
    monkeypatch.setattr("app.assets.animations.server.release_geometry", lambda url: None)
    library = "Body01-Set01-0123456789"
    write_library(tmp_path, library, clips={"Walk01": [0, 1], "Run01": [0, 1]})
    own = tmp_path / "Asset01" / "Animation"
    (own / "SharedLayers").mkdir(parents=True)
    layer = frames_for([[[9, 9, frame, 0, 0, 0, 1]] for frame in range(2)])
    file = "SharedLayers/" + "a" * 32 + ".bin"
    (own / file).write_bytes(layer)
    manifest = dict(format="mod-viewer-animations", version=1, skeleton_id=RIG["id"], clips=[],
                    layer_bones=[2, 4], shared_layer_masks=[[4]],
                    shared_layers=[dict(library=library, fingerprint="build-1", clip="Walk01", file=file,
                                        sha256=hashlib.sha256(layer).hexdigest(), frame_count=2, mask=0)])
    (own / "manifest.json").write_text(json.dumps(manifest))
    sessions = AnimationSessions()

    def load(name):
        catalog = sessions.catalog(str(tmp_path), "Asset01", RIG, lambda: True)
        result = sessions.load(catalog["animation_session"],
                               next(clip["id"] for clip in catalog["animations"] if clip["name"] == name))
        assert result["status"] == "ok"
        return result["clip"], list(struct.iter_unpack("<7f", published[-1]))

    # The baked layer drives its chain; the rest of the Asset's layer-authored bones get physics.
    clip, rows = load("Set01 / Walk01")
    assert rows[len(TARGET) + 4] == (9, 9, 1, 0, 0, 0, 1) and rows[len(TARGET) + 1][:3] == pytest.approx((0, 0, 11))
    assert clip["secondary_bones"] == [2]
    # A clip without a layer simulates every unmapped layer-authored bone and keeps them at rest.
    clip, rows = load("Set01 / Run01")
    assert clip["secondary_bones"] == [2, 4] and rows[len(TARGET) + 4][:3] == (1, 0, 0)
    # A layer baked against another library build never applies.
    manifest["shared_layers"][0]["fingerprint"] = "build-0"
    (own / "manifest.json").write_text(json.dumps(manifest))
    clip, rows = load("Set01 / Walk01")
    assert clip["secondary_bones"] == [2, 4] and rows[len(TARGET) + 4][:3] == (1, 0, 0)
    # A truncated layer fails only that load.
    manifest["shared_layers"][0]["fingerprint"] = "build-1"
    (own / "manifest.json").write_text(json.dumps(manifest))
    (own / file).write_bytes(layer[:-4])
    catalog = sessions.catalog(str(tmp_path), "Asset01", RIG, lambda: True)
    walk = next(clip["id"] for clip in catalog["animations"] if clip["name"] == "Set01 / Walk01")
    assert sessions.load(catalog["animation_session"], walk)["status"] == "error"
