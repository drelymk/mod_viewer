"""Authored execution scanning boundaries."""

from core.ini.draw_scan import _scan_sections_for_draws
from core.ini.sections import parse_sections


def test_draw_scan_preserves_inline_run_snapshots_without_buffer_files():
    sections = parse_sections("sample.ini", text="""[TextureOverrideBody]
ib = ResourceMissingIB
vb0 = ResourceMissingPosition
run = CommandListBody
drawindexed = 3, 3, 0

[CommandListBody]
vb1 = ResourceMissingTexcoord
drawindexed = 3, 0, 0
""")

    scanned = _scan_sections_for_draws(sections)
    body = scanned["TextureOverrideBody"]
    assert [(draw.start, draw.index_resource) for draw in body["draws"]] == [
        (0, "ResourceMissingIB"), (3, "ResourceMissingIB")]
    assert body["draws"][0].vertex_resources == {
        0: "ResourceMissingPosition", 1: "ResourceMissingTexcoord"}
