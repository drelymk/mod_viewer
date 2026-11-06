from dataclasses import asdict
from collections import defaultdict
import hashlib
import struct
import time
from .capture import Inventory, parse_log
from .core import Analysis, Component, Part, Context, Unsupported
from .geometry import decode_component, stream_layout
from .textures import collect, digest
from .budget import ResourceLimit


def trace(resource, writers):
    chain = []
    seen = set()
    while resource and resource.key in writers and resource.key not in seen:
        seen.add(resource.key)
        call = writers[resource.key]
        chain.append(call)
        if call.kind != "CopyResource":
            return call, chain
        resource = call.source
    return None, chain


def _surface_overlap_groups(components):
    outlines, colors = [], []
    for c in components:
        if c.status != "included":
            continue
        if c.parts and all(
            any(t.get("pass_kind") == "outline" for t in p.textures)
            and not any(t.get("pass_kind") == "color" for t in p.textures) for p in c.parts):
            outlines.append(c)
        if any(t.get("pass_kind") == "color" for p in c.parts for t in p.textures):
            colors.append(c)
    return outlines, colors


def _surface_signatures(mesh):
    surfaces = set()
    for part, indices in zip(mesh.component.parts, mesh.indices):
        for j in range(0, len(indices), 3):
            points = sorted(mesh.vertices[index + part.base_vertex][0] for index in indices[j:j + 3])
            surfaces.add(hashlib.sha256(struct.pack("<9f", *(v for point in points for v in point))).digest())
    return surfaces


def classify_surface_overlaps(outlines, colors, surface_signatures):
    """Exclude only validated outline duplicates of a supported color pass."""
    for c in outlines:
        signatures = surface_signatures.get(c.id, set())
        if not signatures:
            continue
        for other in colors:
            other_signatures = surface_signatures.get(other.id, set())
            if other.status != "included" or not signatures <= other_signatures:
                continue
            c.provenance["duplicate_outline_pass"] = True
            c.provenance["surface_overlap"] = dict(component=other.id, triangle_signatures=len(signatures),
                evidence="Exact triangle-position subset of a color-pass component; only outline material bindings captured")
            c.diagnostics.append(f"Duplicate outline pass over {other.name}; excluded by default to avoid overlapping solid surfaces. It can be included manually.")
            break


def morph_trace(position, producer, writers, inv):
    latest, chain = trace(position, writers)
    if not latest or not latest.kind.startswith("Dispatch"):
        return {"copy_chain": [x.id for x in chain]}
    # Walk only writes on this specific resource version back to the initial copy.
    identity = latest.outputs[0].address or latest.outputs[0].hash
    dispatches = []
    for seq in range(latest.sequence, -1, -1):
        call = writers.get(("sequence", seq))
        if not call:
            continue
        if not any((r.address or r.hash) == identity for r in call.outputs):
            continue
        if call.kind == "CopyResource":
            break
        if not call.kind.startswith("Dispatch"):
            continue
        resources, cb = call.bindings["cs-t"], call.bindings["cs-cb"]
        try:
            if 0 not in resources or 0 not in cb:
                raise Unsupported("Missing morph delta or constants")
            delta = inv.find(call.id, "cs-t0", resources[0])
            constant = inv.find(call.id, "cs-cb0", cb[0])
            dispatches.append(dict(draw=call.id, shader=call.shaders.get("cs", ""), delta=delta, constant=constant,
                                   delta_sha256=digest(inv.path(delta)), constant_sha256=digest(inv.path(constant))))
        except ResourceLimit:
            raise
        except (ValueError, FileNotFoundError):
            return {"status": "captured_expression", "dispatches": [{"delta": "missing", "constant": "missing"}],
                    "copy_chain": [x.id for x in chain]}
    return {"dispatches": list(reversed(dispatches)), "copy_chain": [x.id for x in chain]}


def analyze(dump, context=None):
    ctx = context or Context()
    started = time.perf_counter()
    inv = Inventory(dump, ctx)
    calls = parse_log(inv, ctx)
    writers = {}
    grouped = {}
    unsupported = defaultdict(list)
    for draw in calls:
        ctx.tick("Discovering mesh components", draw.sequence, len(calls))
        if not draw.kind.startswith("DrawIndexed"):
            for output in draw.outputs:
                writers[output.key] = draw
            writers[("sequence", draw.sequence)] = draw
            continue
        ib, vbs = draw.bindings["ib"], draw.bindings["vb"]
        if not ib or 0 not in vbs:
            continue
        producer, chain = trace(vbs[0], writers)
        try:
            direct = producer is None and not chain
            attribute_skinning = False
            if direct:
                p = inv.find(draw.id, "vb0", vbs[0])
                if 1 not in vbs:
                    raise Unsupported("Missing UV stream")
                uvcall, uvslot, uv = draw, 1, vbs[1]
                sources = {"position": dict(file=p, slot=0, hash=vbs[0].hash, resource=asdict(vbs[0]))}
                _, elements = inv.header(p.replace(".buf", ".txt"))
                attribute_skinning = {1, 2, 4} <= vbs.keys() and any(
                    e.get("SemanticName") == "ATTRIBUTE" and e.get("SemanticIndex") == "0"
                    and e.get("InputSlot") == "0" for e in elements)
                if attribute_skinning:
                    uvslot, uv = 2, vbs[2]
                    for role, slot in (("normal", 1), ("blend", 4)):
                        r = vbs[slot]
                        sources[role] = dict(file=inv.find(draw.id, f"vb{slot}", r), slot=slot,
                                             hash=r.hash, resource=asdict(r))
            else:
                if not producer or producer.kind != "Draw" or not producer.outputs:
                    raise Unsupported("No supported stream-output skinning producer")
                pvb = producer.bindings["vb"]
                p = inv.find(producer.id, "vb0", pvb[0])
                # Identify blend streams by validated byte ratio across posing paths.
                psize = inv.path(p).stat().st_size
                position_stride, _, _, _ = stream_layout(inv, p, 0, "position")
                options = []
                for slot, r in pvb.items():
                    if slot == 0:
                        continue
                    f = inv.find(producer.id, "vb" + str(slot), r)
                    h, _ = inv.header(f.replace(".buf", ".txt"))
                    stride = int(h.get("stride", 0))
                    if stride in (4, 16, 32) and inv.path(f).stat().st_size * position_stride == psize * stride:
                        options.append((slot, r, f))
                if len(options) != 1:
                    raise Unsupported("Ambiguous or missing pre-skinning blend stream")
                blend_slot, blend, b = options[0]
                if len(pvb) == 3 and blend_slot == 2:
                    uvcall, uvslot, uv = producer, 1, pvb[1]
                elif 1 in vbs:
                    uvcall, uvslot, uv = draw, 1, vbs[1]
                else:
                    raise Unsupported("Missing UV stream")
                sources = {"position": dict(file=p, slot=0, hash=pvb[0].hash, resource=asdict(pvb[0])),
                           "blend": dict(file=b, slot=blend_slot, hash=blend.hash, resource=asdict(blend))}
            t = inv.find(uvcall.id, f"vb{uvslot}", uv)
            sources["texcoord"] = dict(file=t, slot=uvslot, hash=uv.hash, resource=asdict(uv))
            ibfile = inv.find(draw.id, "ib", ib, ".txt")
            header, _ = inv.header(ibfile)
            if "first index" not in header:
                raise Unsupported("Index snapshot lacks draw-range metadata")
            first = int(draw.args.get("StartIndexLocation", header["first index"]))
            count = int(draw.args.get("IndexCount", draw.args.get("IndexCountPerInstance", header.get("index count", 0))))
            if first != int(header["first index"]) or count != int(header.get("index count", -1)):
                raise Unsupported("Index header and draw range disagree")
            if int(draw.args.get("InstanceCount", 1)) != 1:
                raise Unsupported("Instanced character grouping is ambiguous")
            if direct:
                # Direct passes can reuse resources under different input layouts.
                identity = []
                for role, source in sources.items():
                    path = inv.path(source["file"])
                    source["sha256"] = digest(path)
                    stride, elements, declared, first_vertex = stream_layout(
                        inv, source["file"], source["slot"],
                        "wwmi_" + role if attribute_skinning else "direct_position" if role == "position" else role)
                    # Valid subset counts describe the draw, not a different layout.
                    if (role == "texcoord" or attribute_skinning) and first_vertex == 0 and 0 <= declared <= path.stat().st_size // stride:
                        declared = 0
                    identity.append((source["resource"]["address"], source["resource"]["version"],
                                     source["sha256"], repr((stride, elements, declared, first_vertex))))
                source_key = (ib.key, "attribute_skinning" if attribute_skinning else "direct", tuple(identity))
            else:
                source_key = (ib.key, producer.sequence, uv.key)
            # Resource version + layout + content define components; never IB hash alone.
            if source_key not in grouped:
                for source in sources.values():
                    if "sha256" not in source:
                        source["sha256"] = digest(inv.path(source["file"]))
                origin = draw if direct else producer
                token_source = (source_key, [x["sha256"] for x in sources.values()]) if direct else (
                    ib.key, producer.id, [x["sha256"] for x in sources.values()])
                token = hashlib.sha256(repr(token_source).encode()).hexdigest()[:12]
                reason = ("Validated split ATTRIBUTE skinning streams" if attribute_skinning else
                          "Declared direct vertex streams; captured coordinates retained" if direct else "Validated stream-output skinning relationship")
                component = Component(token, "", ib.hash, "included", reason, sources, [],
                                      dict(producer="" if direct else producer.id, root_vs=origin.shaders.get("vs", ""), draw_vb=vbs[0].hash,
                                           producer_line=origin.line, draw_evidence=[], copy_chain=[x.id for x in chain]))
                if direct:
                    component.provenance["geometry_kind"] = "attribute_skinning" if attribute_skinning else "direct"
                else:
                    component.morph = morph_trace(pvb[0], producer, writers, inv)
                grouped[source_key] = (component, {})
            component, parts = grouped[source_key]
            base = int(draw.args.get("BaseVertexLocation", 0))
            offset = int(draw.bindings["ib_args"].get("Offset", 0))
            # Payload identity preserves collision/write differences across passes.
            ibdigest = digest(inv.path(ibfile.replace(".txt", ".buf")))
            key = (first, count, base, offset, ibdigest)
            if key not in parts:
                parts[key] = (Part(first, count, base, ibfile, [], index_offset=offset, sha256=ibdigest), [])
            part, draws = parts[key]
            part.draws.append(draw.id)
            draws.append(draw)
            component.provenance["draw_evidence"].append(dict(draw=draw.id, line=draw.line, ib=asdict(ib), vb0=asdict(vbs[0]),
                                                              shaders=draw.shaders, first=first, count=count, base_vertex=base, byte_offset=offset))
        except ResourceLimit:
            raise
        except (ValueError, OSError, KeyError) as error:
            unsupported[ib.hash].append((draw, str(error)))
    components = []
    for component, parts in grouped.values():
        for key, (part, draws) in sorted(parts.items()):
            part.textures = collect(inv, draws, ctx)
            if not part.textures:
                component.diagnostics.append(f"No available material textures for range {part.first}; geometry is exportable")
            for t in part.textures:
                if t.get("role_note"):
                    note = f"Range {part.first}: {t['role_note']}"
                    if note not in component.diagnostics:
                        component.diagnostics.append(note)
            component.parts.append(part)
        components.append(component)
    components.sort(key=lambda c: (min(int(x) for p in c.parts for x in p.draws), c.id))
    outlines, colors = _surface_overlap_groups(components)
    surface_ids = {c.id for c in (*outlines, *colors)} if outlines and colors else set()
    surface_signatures, decoded_meshes = {}, {}
    for i, c in enumerate(components, 1):
        c.name = f"Component{i:03d}"
        try:
            mesh = decode_component(inv, c, ctx)
            if inv.gimi_evidence and not inv.zzz_evidence and (
                c.provenance.get("geometry_kind") == "direct" or len(mesh.vertices) <= 4096
            ):
                decoded_meshes[c.id] = mesh
            if c.id in surface_ids:
                surface_signatures[c.id] = _surface_signatures(mesh)
        except ResourceLimit:
            raise
        except (ValueError, KeyError, OSError) as error:
            c.status = "unsupported"
            c.diagnostics.append(str(error))
    if surface_ids:
        classify_surface_overlaps(outlines, colors, surface_signatures)
    if inv.gimi_evidence and not inv.zzz_evidence:
        from .profiles.gimi_faces import classify_faces
        from .profiles.gimi_placement import placement_resolver, record_placements
        resolve = placement_resolver(inv, calls, components, ctx)
        components.extend(classify_faces(decoded_meshes, ctx, resolve_placement=resolve))
        record_placements(inv, calls, components, ctx, resolve=resolve)
    for component in components:
        if component.status == "included" and component.requires_review:
            component.reason = "Decodable direct geometry; target-asset membership is unproven"
            component.diagnostics.append("Direct geometry without proven asset membership is excluded by default; its source streams and ranges remain in the analysis for explicit internal selection.")
    for unresolved_index, (h, rows) in enumerate(sorted(unsupported.items()), 1):
        # Noncharacter evidence is deliberately narrow: missing producer remains visible.
        draws = [r[0] for r in rows]
        components.append(Component("unresolved-" + h, f"Unresolved candidate {unresolved_index}", h, "uncertain", rows[0][1], {}, [],
                                    dict(draws=[d.id for d in draws], shaders=[d.shaders for d in draws])))
    diagnostics = ["Validated stream-output skinning, split skinned ATTRIBUTE layouts and declared direct position/normal/UV streams are supported. ATTRIBUTE and direct streams retain their stored positions without shader deformation or placement conversion; other layouts remain unresolved.",
                   "Single-asset grouping is an assumption; this capture alone does not establish semantic identity or a complete skeleton.",
                   "Captured material signatures take priority over extraction-only pixel fallback. Ambiguous pixel evidence remains in the report; unclassified roles retain deterministic slot names. Auxiliary resources are separated."]
    if not any(c.status == "included" for c in components):
        diagnostics.append("No supported character geometry found. Export is unavailable.")
    needed_files = set()
    for c in components:
        for stream in c.streams.values():
            needed_files.update((stream["file"], stream["file"].replace(".buf", ".txt")))
        for part in c.parts:
            needed_files.update((part.ib_file, part.ib_file.replace(".txt", ".buf")))
            needed_files.update(t["file"] for t in part.textures)
        for d in c.morph.get("dispatches", []):
            needed_files.update((d["delta"], d["constant"]))
        needed_files.update(source["file"] for source in
                            c.provenance.get("gimi_face_placement", {}).get("sources", []))
    analysis = Analysis(str(inv.root), "stream-output", components, diagnostics,
                    dict(scan_seconds=time.perf_counter() - started, file_count=len(inv.files), logical_bytes=inv.logical_bytes,
                         event_count=len(calls), supported_components=sum(c.status == "included" for c in components),
                         dedup_targets={name: target for name, target in inv.targets.items() if name in needed_files and name not in inv.files}))
    analysis._capture_source = inv.source
    analysis._budget = inv.budget
    return analysis
