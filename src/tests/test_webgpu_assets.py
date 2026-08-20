from pathlib import Path
import os

import build

from app import server


def test_webgpu_vendor_set_is_pinned_and_complete():
    assert build.THREE_VERSION == server.THREE_VERSION == "0.185.0"
    assert set(build.ASSET_FILES) == {
        "three.core.js",
        "three.webgpu.js",
        "three.tsl.js",
        os.path.join("addons", "controls", "ArcballControls.js"),
    }


def test_import_map_uses_webgpu_and_tsl_entry_points():
    html = Path("src/web/index.html").read_text(encoding="utf-8")
    assert '"three":          "__THREE_URL__"' in html
    assert '"three/webgpu":    "__THREE_URL__"' in html
    assert '"three/tsl":       "__THREE_TSL_URL__"' in html
    assert '"three/addons/":  "__ADDONS_URL__"' in html
