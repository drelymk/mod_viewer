"""Atomic menu cleanup uses staged documents and never writes mod files."""

import pytest

from app.bridge import toggle as toggle_api
from app.session import edit as edit_session
from core.ini.menu import extract_menu_toggles
from core.ini.sections import sections_from_document, extract_resources
from tests.core.editing.test_menu import MENU


@pytest.mark.parametrize("failure", [False, True], ids=["staged-lifecycle", "atomic-rollback"])
def test_menu_delete_is_atomic_and_memory_only(api_root, failure):
    from pathlib import Path
    root = Path(api_root)
    first = root / "first.ini"
    second = root / "second.ini"
    first.write_text(MENU, encoding="utf-8")
    second.write_text(MENU + ("[TextureOverrideOther]\nx0 = $Mode\n" if failure else ""), encoding="utf-8")
    images = root / "icons"
    images.mkdir()
    for name in ("badge.dds", "pressed.dds", "slider.dds"):
        (images / name).write_bytes(b"generated image fixture")
    untouched = root / "untouched.ini"
    untouched.write_bytes(b"[ResourceEmpty]\r\n[CommandListEmpty]\n[Constants]\nglobal $Retained = 1")
    paths = [str(first), str(second), str(untouched)]
    edit_session.load_documents(api_root, paths)
    with edit_session.transaction(api_root, [str(first)]) as transaction:
        transaction.document(str(first)).insert_lines(1, ["global $Retained = 7"])
    before = {path: edit_session.peek(api_root, path).to_string() for path in paths}
    files = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = toggle_api.delete_menu_toggles(api_root)
    if failure:
        assert result["error"]
        assert {path: edit_session.peek(api_root, path).to_string() for path in paths} == before
    else:
        assert result == {"ok": True, "pending": True, "result": {"controls_removed": 4, "files": 2}}
        assert "global $Retained = 7" in edit_session.peek(api_root, str(first)).to_string()
        for path in paths[:2]:
            document = edit_session.peek(api_root, path)
            sections = sections_from_document(document)
            assert not extract_menu_toggles(sections, resources=extract_resources(sections))
            assert document.section("ResourceBadge") is None
            assert document.section("ResourceSlider") is not None
        assert edit_session.dirty_documents(api_root) == {"first.ini", "second.ini"}
        assert edit_session.peek(api_root, str(untouched)).to_bytes() == files[untouched]
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == files


@pytest.mark.parametrize("pulse", [False, True], ids=["flip-controller", "pulse-controller"])
def test_menu_delete_follows_authorized_namespace_controller_and_target(api_root, pulse):
    from pathlib import Path
    root = Path(api_root)
    controller = root / "controller.ini"
    target = root / "target.ini"
    controller_text = r"""[Constants]
global persist $Value = 1
[CommandListButton]
$Value = 1 - $Value
[Present]
$\Fixture\Mode = $Value
"""
    if pulse:
        controller_text = r"""[Constants]
global persist $Value = 1
global $Pulse = 0
[CommandListButton]
$Pulse = 1 - $Pulse
[Present]
$Value = $Value + $Pulse
if $Value > 1
    $Value = 0
endif
$\Fixture\Mode = $Value
"""
    controller.write_text(controller_text, encoding="utf-8")
    target.write_text("""namespace = Fixture
[Constants]
global $Mode = 1
[TextureOverridePart]
ib = ResourceIndex
vb0 = ResourcePosition
vb1 = ResourceTexcoord
if $Mode == 1
    drawindexed = 3,0,0
endif
[ResourceIndex]
filename = part.ib
format = R32_UINT
[ResourcePosition]
filename = position.buf
stride = 12
[ResourceTexcoord]
filename = texcoord.buf
stride = 8
""", encoding="utf-8")
    paths = [str(controller), str(target)]
    edit_session.load_documents(api_root, paths)
    before = [path.read_bytes() for path in (controller, target)]
    result = toggle_api.delete_menu_toggles(api_root)
    assert result == {"ok": True, "pending": True, "result": {"controls_removed": 1, "files": 2}}
    assert "$Value" not in edit_session.peek(api_root, str(controller)).to_string()
    assert "$Pulse" not in edit_session.peek(api_root, str(controller)).to_string()
    assert edit_session.peek(api_root, str(controller)).sections == []
    assert "$Mode" not in edit_session.peek(api_root, str(target)).to_string()
    assert "$\\Fixture\\Mode" not in edit_session.peek(api_root, str(controller)).to_string()
    assert "drawindexed = 3,0,0" in edit_session.peek(api_root, str(target)).to_string()
    assert [path.read_bytes() for path in (controller, target)] == before


def test_menu_delete_rejects_read_only_archives(api_root):
    from pathlib import Path
    import zipfile
    from core.mod_source import ZipModSource
    archive = Path(api_root) / "mod.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("mod.ini", MENU)
    data = archive.read_bytes()
    source = ZipModSource(str(archive))
    edit_session.load_documents(api_root, [source.document_path("mod.ini")], source=source)
    result = toggle_api.delete_menu_toggles(api_root)
    assert result["error"]
    assert not edit_session.has_pending(api_root)
    assert archive.read_bytes() == data
