from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Event
from typing import Callable
import json


class Cancelled(Exception):
    pass


class Unsupported(ValueError):
    pass


@dataclass
class Context:
    cancel: Event = field(default_factory=Event)
    progress: Callable[[str, int, int], None] = lambda *_: None

    def tick(self, stage, current=0, total=0):
        if self.cancel.is_set():
            raise Cancelled("Operation cancelled")
        self.progress(stage, current, total)


@dataclass(frozen=True)
class Resource:
    hash: str
    address: str = ""
    version: int = 0

    @property
    def key(self):
        return (self.address or self.hash, self.version)


@dataclass
class Call:
    id: str
    sequence: int
    kind: str
    args: dict
    bindings: dict
    shaders: dict
    outputs: list[Resource] = field(default_factory=list)
    source: Resource | None = None
    line: int = 0


@dataclass
class Part:
    first: int
    count: int
    base_vertex: int
    ib_file: str
    draws: list[str]
    textures: list[dict] = field(default_factory=list)
    index_offset: int = 0
    sha256: str = ""


@dataclass
class Component:
    id: str
    name: str
    ib: str
    status: str
    reason: str
    streams: dict
    parts: list[Part]
    provenance: dict
    vertex_count: int = 0
    triangle_count: int = 0
    diagnostics: list[str] = field(default_factory=list)
    morph: dict = field(default_factory=dict)
    bounds: list = field(default_factory=list)

    @property
    def requires_review(self):
        return (self.status in ("uncertain", "unsupported") or (
            self.status == "included" and self.provenance.get("geometry_kind") == "direct"
            and not self.provenance.get("gimi_face_family")))

    @property
    def default_included(self):
        return (self.status == "included" and not self.requires_review
                and not self.provenance.get("duplicate_outline_pass")
                and not self.provenance.get("duplicate_capture_pass")
                and not self.provenance.get("auxiliary_face_layer"))


@dataclass
class Analysis:
    dump: str
    profile: str
    components: list[Component]
    diagnostics: list[str]
    metrics: dict
    schema: int = 1

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for c in data["components"]:
            c["parts"] = [Part(**p) for p in c["parts"]]
        data["components"] = [Component(**c) for c in data["components"]]
        return cls(**data)
