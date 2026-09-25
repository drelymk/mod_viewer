"""Authoritative, per-file INI inputs for one mod load."""

from dataclasses import dataclass, field

from core.ini.document import IniDocument


@dataclass(frozen=True)
class IniRecord:
    path: str
    relative_path: str
    document: IniDocument
    sections: dict
    namespace: str | None = None
    var_prefix: str | None = None
    source_name: str | None = None
    canonical_vars: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModIniSnapshot:
    mod_dir: str
    source: object
    records: tuple[IniRecord, ...]
    revision: int | None = None
