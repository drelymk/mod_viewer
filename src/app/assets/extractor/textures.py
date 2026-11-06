from collections import defaultdict
import hashlib
import re
from .profiles.zzz import material_evidence as zzz_material_evidence, auxiliary
from .profiles.gimi import material_evidence as gimi_material_evidence
from .profiles.wwmi import material_evidence as wwmi_material_evidence
from .image_headers import image_metadata
from .capture import FILE


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def _image_record(inv, name):
    path = inv.path(name)
    stat = path.stat()
    key = ((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
           if getattr(stat, "st_ino", 0) else str(path))
    cache = getattr(inv, "_texture_records", None)
    if cache is None:
        cache = inv._texture_records = {}
    if key not in cache:
        cache[key] = dict(sha256=digest(path), **image_metadata(path))
    return cache[key]


def _captured_roles(inv, draw, images, color):
    if not inv.zzz_evidence and (not color or not (inv.gimi_evidence or inv.wwmi_evidence)):
        return {}, 0
    by_slot = defaultdict(list)
    for slot, h, ext, name in images:
        by_slot[slot].append((ext != ".dds", ext != ".png", h, name))
    textures = []
    for slot, variants in by_slot.items():
        _, _, h, name = min(variants)
        resource = draw.bindings["ps-t"].get(slot)
        textures.append(dict(slot=slot, hash=h, resource=vars(resource) if resource else {},
                             format=inv.formats.get(name, "unknown"), contaminated="!" in name,
                             **_image_record(inv, name)))
    if inv.zzz_evidence:
        return zzz_material_evidence(textures, color, draw.shaders.get("ps", ""))
    if not inv.gimi_evidence:
        return wwmi_material_evidence(textures, color, draw.shaders.get("ps", ""))
    return gimi_material_evidence(textures, color, draw.shaders.get("ps", ""))


def _texture(inv, draw, slot, identity, name):
    record = dict(role=f"TextureSlot{slot:02d}", slot=slot, hash=identity, file=name,
                  format=inv.formats.get(name, "unknown"), draw=draw.id,
                  shader=draw.shaders.get("ps", ""), vertex_shader=draw.shaders.get("vs", ""),
                  resource=vars(draw.bindings["ps-t"][slot]) if slot in draw.bindings["ps-t"] else None,
                  contaminated="!" in name, role_confidence="unknown", **_image_record(inv, name))
    if identity and (entry := FILE.match(name)) and not entry[3]:
        record["hash_source"] = "logged_dedup"
    return record


def collect(inv, draws, context):
    """Keep the chosen pass's textures independently of role inference and report all passes."""
    candidates = []
    for draw in draws:
        images = []
        entries = inv.entries.get(draw.id, {})
        for (slot, h, ext), names in entries.items():
            if not slot.startswith("ps-t") or ext not in (".dds", ".jpg", ".jpeg", ".png"):
                continue
            for name in names:
                try:
                    inv.path(name)
                except FileNotFoundError:
                    continue
                identity = h
                if not identity:
                    # Replacement snapshots may omit their hash in the draw filename.
                    # Only the exact logged payload alias supplies that missing identity;
                    # an earlier slot binding can still describe the replaced resource.
                    target = inv.targets.get(name, "")
                    match = re.fullmatch(r"deduped/([a-f0-9]{8})-[^/\\.]+" + re.escape(ext),
                                         target, re.IGNORECASE)
                    if match:
                        identity = match[1].lower()
                images.append((int(slot[4:]), identity, ext, name))
        color = any(slot == "o0" for slot, _hash, _ext in entries)
        if images:
            candidates.append((color, draw, images))
    if not candidates:
        return []
    # Stable first material draw wins ties, rather than an early shadow pass.
    captured_roles = {draw.id: _captured_roles(inv, draw, images, color)
                  for color, draw, images in candidates}
    color, draw, images = max(candidates, key=lambda x: (x[0], bool(captured_roles[x[1].id][0]), captured_roles[x[1].id][1], -x[1].sequence))
    by_slot = defaultdict(list)
    for slot, h, ext, name in images:
        by_slot[slot].append((ext != ".dds", ext != ".png", h, name))
    result = []
    for slot, variants in sorted(by_slot.items()):
        _, _, h, name = min(variants)
        context.tick("Collecting material textures")
        result.append(_texture(inv, draw, slot, h, name))
    roles = captured_roles[draw.id][0]
    pass_kind = ("outline" if roles.get(2) == "Diffuse" else "color" if roles.get(3) == "Diffuse" else "unknown") if inv.zzz_evidence else "color" if roles else "unknown"
    for t in result:
        t["pass_kind"] = pass_kind
        if t["slot"] in roles:
            t["role"] = roles[t["slot"]]
            profile = "zzz" if inv.zzz_evidence else "gimi" if inv.gimi_evidence else "wwmi"
            t["role_confidence"] = f"inferred_{profile}_pass_format_bindings"
            if inv.gimi_evidence and not inv.zzz_evidence and captured_roles[draw.id][1] == 2:
                t["role_note"] = "Base iris diffuse identified by the complete layered GIMI eye material family."
        t["auxiliary"] = t["slot"] not in roles and auxiliary(t)
        if inv.zzz_evidence and roles.get(3) == "Diffuse" and t["slot"] == 4 and t["format"] == "R16G16B16A16_FLOAT":
            t.update(auxiliary=True, role_confidence="unsupported_face_normal_encoding",
                     role_note="Floating-point face shader resource is not validated as a tangent-space normal map; retained as a captured candidate without an automatic role.")
    # Inference changes automatic roles, never captured-pass membership.
    from .pixel_roles import apply_fallback
    apply_fallback(inv, result, color, context)
    for texture in result:
        texture["captured_pass"] = True
    preferred = {t["slot"]: t for t in result}
    alternates = defaultdict(int)
    for candidate_color, candidate, images in candidates:
        variants = defaultdict(list)
        for slot, h, ext, name in images:
            variants[slot, h].append((ext != ".dds", ext != ".png", name))
        for (slot, h), names in sorted(variants.items()):
            _, _, name = min(names)
            if candidate.id == draw.id and any(t["file"] == name for t in result):
                continue
            main = preferred.get(slot)
            alternates[slot] += 1
            alternate = _texture(inv, candidate, slot, h, name)
            dynamic = (main and h == main["hash"] and alternate["sha256"] != main["sha256"]
                       and candidate_color == color and candidate.shaders.get("ps", "") == draw.shaders.get("ps", ""))
            alternate.update(role=f"TextureSlot{slot:02d}Variant{alternates[slot]:03d}",
                             role_confidence="dynamic_alternate" if dynamic else "captured_other_pass",
                             auxiliary=True, captured_pass=candidate.id == draw.id,
                             role_note="Captured binding retains its source pass; automatic roles keep the preferred snapshot.")
            result.append(alternate)
    return result
