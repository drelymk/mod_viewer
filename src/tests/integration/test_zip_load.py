"""ZIP-backed model loading through the ordinary mod pipeline."""

import zipfile

from app.mods import loader
from tests.support.model_data import basic_model_ini, triangle_geometry


def test_load_mod_reads_deep_geometry_and_reports_zip_source_metadata(tmp_path):
    ini = basic_model_ini()
    geometry = triangle_geometry()
    archive_path = tmp_path / "packed.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Export/some/random/deep/mod.ini", ini)
        for name, data in geometry.items():
            archive.writestr(f"Export/some/random/deep/{name}", data)

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["metadata"]["source_kind"] == "zip"
    assert payload["metadata"]["source_read_only"] is True
    assert len(payload["meshes"]) == 1
    assert payload["meshes"]["Component01-1"]["identity"]["source"] \
        == "some/random/deep/mod.ini"


def test_load_mod_does_not_cap_zip_ini_discovery_before_geometry(tmp_path):
    ini = basic_model_ini()
    geometry = triangle_geometry()
    archive_path = tmp_path / "many-inis.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for index in range(10):
            archive.writestr(f"Export/{index:02}.ini", "[KeyUnused]\nkey = F1\n")
        archive.writestr("Export/zz-geometry.ini", ini)
        for name, data in geometry.items():
            archive.writestr(f"Export/{name}", data)

    payload = loader.load_mod(str(archive_path))

    assert not payload.get("error")
    assert payload["meshes"]["Component01-1"]["identity"]["source"] \
        == "zz-geometry.ini"
