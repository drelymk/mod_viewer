"""Small snapshot context fixture for application tests."""

from pathlib import Path

from app.mods.analysis import build_mod_ini_snapshot
from app.mods.loader import ModLoadContext
from core.ini.document import IniDocument
from core.mod_source import mod_source_for_path


def snapshot_context(mod_dir, paths=(), documents=None, metadata=None):
    paths = [str(path) for path in paths]
    documents = dict(documents or {})
    for path in paths:
        if path not in documents:
            documents[path] = (IniDocument.load(path) if Path(path).is_file()
                               else IniDocument.from_string("", path=path))
    source = mod_source_for_path(str(mod_dir))
    snapshot = build_mod_ini_snapshot(
        paths, str(mod_dir), documents, source=source,
        require_documents=True)
    return ModLoadContext(str(mod_dir), snapshot, metadata or {})
