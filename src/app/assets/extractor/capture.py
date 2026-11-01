"""Streaming inventory and ordered D3D11 state parser; never reads sibling folders."""
from collections import defaultdict
from pathlib import Path
import re
from .core import Resource, Call, Context, Unsupported

FILE = re.compile(r"^(\d{6})-((?:[a-z]+-)?(?:vb|ib|t|cb|u|o)\d*)(?:=(?:!.!=)?([a-f0-9]{8})(?:\([a-f0-9]{8}\))?)?(?=[-.])")
API = re.compile(r"^(\d{6}) ([A-Za-z][A-Za-z0-9]*)\((.*)\)(.*)$")
SLOT = re.compile(r"^\s+(\d+):.*?resource=(0x[0-9a-fA-F]+).*?hash=([a-f0-9]+)")
ARGS = re.compile(r"(\w+):([^,)]+)")
GROUPS = {"IASetVertexBuffers": "vb", "SOSetTargets": "so",
          "CSSetUnorderedAccessViews": "u", "CSSetShaderResources": "cs-t",
          "CSSetConstantBuffers": "cs-cb", "PSSetShaderResources": "ps-t"}


class Inventory:
    def __init__(self, dump, context=None):
        self.root = Path(dump).resolve()
        self.context = context or Context()
        self.entries = defaultdict(list)
        self.files = {}
        self.targets = {}
        self.formats = {}
        self.zzz_evidence = False
        self.gimi_evidence = False
        self.wwmi_evidence = False
        self.logical_bytes = 0
        if not self.root.is_dir():
            raise ValueError("Choose an existing frame-dump directory")
        for i, p in enumerate(sorted(self.root.iterdir())):
            if i % 128 == 0:
                self.context.tick("Indexing capture", i)
            if p.is_file():
                self.files[p.name] = p
                self.logical_bytes += p.stat().st_size
                if m := FILE.match(p.name):
                    self.entries[(m[1], m[2], m[3] or "", p.suffix.lower())].append(p.name)

    def path(self, name):
        p = (self.root / name).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Capture resource escapes the selected dump")
        if p.exists():
            return p
        target = self.targets.get(name)
        if target:
            return self.path(target)
        raise FileNotFoundError(name)

    def find(self, call_id, slot, resource, ext=".buf"):
        h = resource.hash if isinstance(resource, Resource) else resource
        names = self.entries.get((call_id, slot, h, ext), [])
        if len(names) != 1:
            raise Unsupported(f"Expected one {slot} {ext} snapshot at draw {call_id}; found {len(names)}")
        return names[0]

    def header(self, name):
        values, elements, active = {}, [], None
        with self.path(name).open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 512 or "vertex-data:" in line or "index-data:" in line or not line.strip():
                    break
                if line.startswith("element["):
                    active = {}
                    elements.append(active)
                elif ":" in line:
                    k, v = line.strip().split(":", 1)
                    (active if active is not None and line.startswith(" ") else values)[k] = v.strip()
        return values, elements


def parse_log(inventory, context=None):
    ctx = context or inventory.context
    states = {g: {} for g in GROUPS.values()}
    shaders, versions, calls = {}, defaultdict(int), []
    ib = None
    ib_args = {}
    pending = None
    copy_rows = []
    copy_id = copy_kind = ""

    def current(r):
        return Resource(r.hash, r.address, versions[r.address or r.hash])

    def snapshot():
        b = {g: {s: current(r) for s, r in values.items()} for g, values in states.items()}
        b["ib"] = current(ib) if ib else None
        b["ib_args"] = ib_args.copy()
        return b

    # replacement decoding accommodates localized annotations; API syntax remains ASCII.
    with inventory.path("log.txt").open(encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            if lineno % 512 == 0:
                ctx.tick("Tracing resource history", lineno)
            line = raw.rstrip()
            if "3DMigoto" in line and re.search(r"\\ZZMI(?:v\d+)?\\", line, re.IGNORECASE):
                inventory.zzz_evidence = True
            if "3DMigoto" in line and re.search(r"\\GIMI(?:v\d+)?\\", line, re.IGNORECASE):
                inventory.gimi_evidence = True
            if "3DMigoto" in line and re.search(r"\\WWMI(?:v\d+)?\\", line, re.IGNORECASE):
                inventory.wwmi_evidence = True
            # Dump targets are interpreted only beneath the chosen capture, never arbitrary log paths.
            if "3DMigoto Dumping " in line and " -> " in line:
                left, right = line.split(" -> ", 1)
                name = left.rsplit("\\", 1)[-1]
                target = "deduped/" + right.rsplit("\\", 1)[-1]
                inventory.targets[name] = target
                if m := FILE.match(name):
                    key = (m[1], m[2], m[3] or "", Path(name).suffix.lower())
                    if name not in inventory.entries[key]:
                        inventory.entries[key].append(name)
                inventory.formats[name] = right.rsplit("\\", 1)[-1].rsplit(".", 1)[0].split("-", 1)[-1]
                continue
            if m := SLOT.match(line):
                if pending:
                    states[pending][int(m[1])] = Resource(m[3], m[2])
                continue
            if copy_rows and line.startswith("       ") and "hash=" in line:
                h = re.search(r"hash=([a-f0-9]+)", line)
                a = re.search(r"(?:resource|pResource)=(0x[0-9a-fA-F]+)", line)
                # Copy rows use 'src: resource=...' / 'dst: resource=...'.
                copy_rows.append(Resource(h[1], a[1] if a else ""))
                if len(copy_rows) == 3:
                    _, src, dst = copy_rows
                    source = current(src)
                    versions[dst.address or dst.hash] += 1
                    calls.append(Call(copy_id, len(calls), copy_kind, {}, {}, {}, [current(dst)], source, lineno))
                    copy_rows = []
                continue
            if not (m := API.match(line)):
                continue
            call_id, op, body, tail = m.groups()
            args = {k: v.strip() for k, v in ARGS.findall(body)}
            pending = None
            if op in GROUPS:
                pending = GROUPS[op]
                start = int(args.get("StartSlot", 0))
                count = int(args.get("NumBuffers", args.get("NumViews", args.get("NumUAVs", 0))))
                if pending == "so":
                    states[pending].clear()
                else:
                    for slot in range(start, start + count):
                        states[pending].pop(slot, None)
            elif op in ("VSSetShader", "PSSetShader", "CSSetShader"):
                h = re.search(r"hash=([a-f0-9]+)", tail)
                shaders[op[:2].lower()] = h[1] if h else ""
            elif op == "IASetIndexBuffer":
                h = re.search(r"hash=([a-f0-9]+)", tail)
                ib = Resource(h[1], args.get("pIndexBuffer", "")) if h else None
                ib_args = args
            elif op in ("CopyResource", "CopySubresourceRegion", "CopySubresourceRegion1"):
                copy_rows = [True]
                copy_id = call_id
                copy_kind = op
            elif op in ("Unmap", "UpdateSubresource", "UpdateSubresource1"):
                address = args.get("pResource", args.get("pDstResource", ""))
                if address:
                    versions[address] += 1
            elif op.startswith("Draw") or op.startswith("Dispatch"):
                b = snapshot()
                output_group = "u" if op.startswith("Dispatch") else "so"
                outputs = []
                for r in b[output_group].values():
                    versions[r.address or r.hash] += 1
                    outputs.append(current(r))
                calls.append(Call(call_id, len(calls), op, args, b, shaders.copy(), outputs, line=lineno))
    return calls
