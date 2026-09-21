"""Small, reusable frontend payload builders for browser tests."""

import base64
import copy
import struct


_PNG_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "ScLkWQAAAABJRU5ErkJggg==")
_MOD_LIBRARY = "fixture-mod-library"


def _f32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _u32(*values):
    return base64.b64encode(struct.pack(f"<{len(values)}I", *values)).decode()


def _payload(label="A"):
    texture_pool = [
        {"tex_key": f"diffuse::{label}-one.png", "label": f"{label} one"},
        {"tex_key": f"diffuse::{label}-two.png", "label": f"{label} two"},
    ]
    return {
        "meshes": {
            f"Body-{label}-0": {
                "component": f"Body {label}",
                "drawindexed": [3, 0, 0],
                "pos": _f32(0, 0, 0, 1, 0, 0, 0, 1, 0),
                "idx": _u32(0, 1, 2),
                "skinning_available": True,
                "tex_key": texture_pool[0]["tex_key"],
                "texture_pool_id": "p0",
                "texture_variants": [{
                    "conditions": [[{
                        "var": "menu", "value": "1", "negate": False,
                    }]],
                    "tex_key": texture_pool[1]["tex_key"],
                }],
                "shape_targets": [{
                    "var": "shape",
                    "pos": _f32(0, 0, 0, 1.2, 0, 0, 0, 1.2, 0),
                }],
                "conditions": [],
                "sources": [{"ini": f"{label}.ini", "line": 10,
                              "section": "TextureOverrideBody",
                              "occurrence": {
                                  "section": "TextureOverrideBody",
                                  "ordinal": 0, "path": [],
                              }}],
            },
        },
        "texture_pools": {"p0": texture_pool},
        "textures": {option["tex_key"]: _PNG_URI for option in texture_pool},
        "controls": {
            "toggles": {
                f"Key{label}": {
                    "name": f"Toggle {label}", "ini": f"{label}.ini",
                    "section": f"Key{label}", "wired": True,
                    "vars": [{"var": "toggle", "default": "0",
                              "values": ["0", "1"]}],
                },
            },
            "menu": {
                "menu": {"name": "Menu", "slot": 1, "var": "menu",
                         "default": "0", "values": ["0", "1"],
                         "effects": []},
                "shape": {"name": "Shape", "var": "shape",
                          "kind": "shape_slider", "default": "0",
                          "min": "0", "max": "1", "step": "0.1"},
            },
            "present": {"target_inis": []},
        },
        "state": {"rules": [], "defaults": {
            "toggle": "0", "menu": "0", "shape": "0",
        }},
        "geometry": None,
        "metadata": {"mesh_names": {}, "material_profiles": {}},
        "health": {"summary": {"issues": 0, "errors": 0},
                   "files": {}, "issues": []},
    }


def _texture_run_payload():
    """Small ordered draw corpus for automatic texture-run regressions."""
    texture_a = "diffuse::TextureRuns-A.png"
    texture_b = "diffuse::TextureRuns-B.png"
    pool = [
        {"tex_key": texture_a, "label": "Texture A"},
        {"tex_key": texture_b, "label": "Texture B"},
    ]
    entries = {}

    def add_draw(component, index, key=None, variants=None):
        name = f"{component}-{index}"
        entry = {
            "component": component,
            "drawindexed": [3, index * 3, 0],
            "pos": _f32(0, 0, 0, 1, 0, 0, 0, 1, 0),
            "idx": _u32(0, 1, 2),
            "skinning_available": True,
            "texture_pool_id": "runs",
            "conditions": [],
            "sources": [{"ini": "TextureRuns.ini", "line": index + 1,
                          "section": "TextureOverrideRuns",
                          "occurrence": {
                              "section": "TextureOverrideRuns",
                              "ordinal": index, "path": [],
                          }}],
        }
        if key is not None:
            entry["tex_key"] = key
        if variants is not None:
            entry["texture_variants"] = variants
        entries[name] = entry

    for component, keys in {
        "Same": [texture_a, texture_a],
        "Switch": [texture_a, texture_b],
        "Revisit": [texture_a, texture_b, texture_a],
        "Runs": [texture_a, texture_a, texture_b, texture_b,
                  texture_a, texture_a],
        "Claret": [texture_a, texture_a, texture_b, texture_a, None],
    }.items():
        for index, key in enumerate(keys):
            add_draw(component, index, key)

    conditional = [{
        "conditions": [[{
            "var": "mode", "value": "1", "negate": False,
        }]],
        "tex_key": texture_a,
    }]
    add_draw("Conditional", 0, variants=conditional)
    add_draw("Conditional", 1)

    payload = _payload("TextureRuns")
    payload["meshes"] = entries
    payload["texture_pools"] = {"runs": pool}
    payload["textures"] = {
        texture_a: _PNG_URI,
        texture_b: _PNG_URI,
    }
    payload["controls"]["menu"]["mode"] = {
        "name": "Mode", "var": "mode", "kind": "menu", "default": "1",
        "values": ["0", "1"], "effects": [],
    }
    payload["state"]["defaults"]["mode"] = "1"
    return payload


def _present_payload(label="Present"):
    payload = _payload(label)
    payload["controls"]["present"] = {
        "target_inis": [{
            "value": f"{label}.ini", "label": f"{label}.ini",
            "vars": ["toggle"], "has_present": True,
        }],
        "item": {
            "inis": [f"{label}.ini"], "target_inis": [],
            "key": "ctrl p", "key_raw": "ctrl p", "back": "",
            "vars": [{"var": "toggle", "values": ["0", "1"],
                      "default": "0"}],
            "capture_vars": ["toggle"], "count": 2, "aligned": True,
            "missing_inis": [], "sync_error": None,
            "names": ["Present 1", "Present 2"],
        },
    }
    return payload

def _construction_failure_payload():
    payload = _payload("Broken")
    payload["textures"] = {
        "diffuse::Broken-one.png":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLkWQAAAABJRU5ErkJggg==",
    }
    # Keep one valid mesh first so the frontend has already allocated scene,
    # material and texture state when the next mesh construction fails.
    payload["meshes"]["Broken-after-first"] = {
        "component": "Broken after",
        "drawindexed": [3, 0, 0],
        "pos": "!",  # invalid base64: decodeF32 must reject this buffer
        "idx": _u32(0, 1, 2),
        "conditions": [],
        "sources": [{"ini": "Broken.ini", "line": 20,
                      "occurrence": {
                          "section": "TextureOverrideBody",
                          "ordinal": 1, "path": [],
                      }}],
    }
    return payload


def _source_payload():
    payload = _payload("Source")
    template = next(iter(payload["meshes"].values()))
    payload["meshes"] = {}
    for key, source in [("BodyRoot-0", "Root.ini"),
                        ("BodyNested-0", "variants/sub"),
                        ("BodyNested-1", "variants/sub")]:
        entry = copy.deepcopy(template)
        entry["component"] = "Body"
        entry["source"] = source
        entry["texture_pool_id"] = "p0" if source == "Root.ini" else "p1"
        payload["meshes"][key] = entry
    payload["texture_pools"] = {
        "p0": copy.deepcopy(payload["texture_pools"]["p0"]),
        "p1": copy.deepcopy(payload["texture_pools"]["p0"]),
    }

    first_toggle = next(iter(payload["controls"]["toggles"].values()))
    payload["controls"]["toggles"] = {
        "KeyRoot": {**copy.deepcopy(first_toggle), "name": "Duplicate",
                     "source": "Root.ini"},
        "KeyNested": {**copy.deepcopy(first_toggle), "name": "Duplicate",
                       "source": "variants/sub"},
    }
    menu = payload["controls"]["menu"]
    menu["menu"]["source"] = "Root.ini"
    menu["shape"]["source"] = "variants/sub"
    return payload
