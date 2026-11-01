from collections import defaultdict
import hashlib
from pathlib import Path
from .profiles.zzz import material_roles, auxiliary
from .profiles.gimi import material_roles as gimi_material_roles
from .image_headers import image_metadata


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def _gimi_roles(inv, draw, images, color):
    if not inv.gimi_evidence or inv.zzz_evidence or not color:
        return {}, False
    by_slot = defaultdict(list)
    for slot, h, ext, name in images:
        if slot <= 2:
            by_slot[slot].append((ext != ".dds", ext != ".png", h, name))
    textures = []
    for slot, variants in by_slot.items():
        _, _, h, name = min(variants)
        resource = draw.bindings["ps-t"].get(slot)
        textures.append(dict(slot=slot, hash=h, resource=vars(resource) if resource else {},
                             format=inv.formats.get(name, "unknown"), contaminated="!" in name,
                             **image_metadata(inv.path(name))))
    roles = gimi_material_roles(textures, color, draw.shaders.get("ps", ""))
    diffuse = next((t for t in textures if roles.get(t["slot"]) == "Diffuse"), None)
    complete = diffuse is not None and len(textures) == 3 and all(
        t["format"] == ("BC7_UNORM_SRGB" if roles.get(t["slot"]) == "Diffuse" else "BC7_UNORM")
        and t["width"] >= diffuse["width"] / 4 and t["height"] >= diffuse["height"] / 4
        and (t["resource"] or {}).get("hash") == t["hash"] and not t["contaminated"]
        for t in textures)
    return roles, complete


def collect(inv, draws, context):
    """Select a material pass with color target evidence; retain payload identity per draw."""
    candidates = []
    for draw in draws:
        images = []
        for (call_id, slot, h, ext), names in inv.entries.items():
            if call_id != draw.id or not slot.startswith("ps-t") or ext not in (".dds", ".jpg", ".png"):
                continue
            for name in names:
                try:
                    inv.path(name)
                except FileNotFoundError:
                    continue
                images.append((int(slot[4:]), h, ext, name))
        color = any(k[0] == draw.id and k[1] == "o0" for k in inv.entries)
        if images:
            candidates.append((color, len({s for s, *_ in images}), draw, images))
    if not candidates:
        return []
    # Stable first material draw wins ties, rather than an early shadow pass.
    gimi_roles = {draw.id: _gimi_roles(inv, draw, images, color)
                  for color, _, draw, images in candidates}
    color, _, draw, images = max(candidates, key=lambda x: (x[0], bool(gimi_roles[x[2].id][0]), gimi_roles[x[2].id][1], x[1], -x[2].sequence))
    by_slot = defaultdict(list)
    for slot, h, ext, name in images:
        by_slot[slot].append((ext != ".dds", ext != ".png", h, name))
    result = []
    for slot, variants in sorted(by_slot.items()):
        _, _, h, name = min(variants)
        context.tick("Collecting material textures")
        fmt = inv.formats.get(name, "unknown")
        # No unverified slot-only semantic claims. Unknown roles remain importer-safe strings.
        role = f"TextureSlot{slot:02d}"
        result.append(dict(role=role, slot=slot, hash=h, file=name, sha256=digest(inv.path(name)),
                           format=fmt, draw=draw.id, shader=draw.shaders.get("ps", ""),
                           resource=vars(draw.bindings["ps-t"][slot]) if slot in draw.bindings["ps-t"] else None,
                           contaminated="!" in name, role_confidence="unknown", **image_metadata(inv.path(name))))
    roles = material_roles(result, color, draw.shaders.get("ps", "")) if inv.zzz_evidence else gimi_roles[draw.id][0]
    pass_kind = ("outline" if roles.get(2) == "Diffuse" else "color" if roles.get(3) == "Diffuse" else "unknown") if inv.zzz_evidence else "color" if roles else "unknown"
    for t in result:
        t["pass_kind"] = pass_kind
        if t["slot"] in roles:
            t["role"] = roles[t["slot"]]
            t["role_confidence"] = "inferred_zzz_pass_format_bindings" if inv.zzz_evidence else "inferred_gimi_pass_format_bindings"
        t["auxiliary"] = t["slot"] not in roles and auxiliary(t)
        if inv.zzz_evidence and roles.get(3) == "Diffuse" and t["slot"] == 4 and t["format"] == "R16G16B16A16_FLOAT":
            t.update(auxiliary=True, role_confidence="unsupported_face_normal_encoding",
                     role_note="Floating-point face shader resource is not validated as a tangent-space normal map; retained in the report and optional debug assets.")
    # A hash is not a payload identity. Retain alternate dynamic snapshots from
    # comparable material passes in assets rather than silently choosing by hash.
    preferred = {t["slot"]: t for t in result}
    identities = {(t["slot"], t["sha256"]) for t in result}
    alternates = defaultdict(int)
    for candidate_color, _, candidate, images in candidates:
        if candidate.id == draw.id or candidate_color != color or candidate.shaders.get("ps", "") != draw.shaders.get("ps", ""):
            continue
        for slot, h, ext, name in sorted(images):
            main = preferred.get(slot)
            if not main or h != main["hash"] or ext != Path(main["file"]).suffix:
                continue
            identity = digest(inv.path(name))
            if (slot, identity) in identities:
                continue
            identities.add((slot, identity))
            alternates[slot] += 1
            alternate = dict(main)
            alternate.update(file=name, sha256=identity, draw=candidate.id,
                             role=f"TextureSlot{slot:02d}Variant{alternates[slot]:03d}",
                             role_confidence="dynamic_alternate", auxiliary=True,
                             resource=vars(candidate.bindings["ps-t"][slot]) if slot in candidate.bindings["ps-t"] else None,
                             contaminated="!" in name, **image_metadata(inv.path(name)))
            result.append(alternate)
    # Decode only during extraction, after all reliable signatures and exclusions.
    from .pixel_roles import apply_fallback
    apply_fallback(inv, result, color, context)
    return result
