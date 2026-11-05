"""Generated GIMI face streams, matrices and optional capture omissions."""

import copy
import math
import shutil
import struct

from PIL import Image

from app.assets.extractor.textures import digest
from tests.support.extraction_data import header


def _write_matrices(dump, log, stream, positions, eyes, case):
    """Generate shifted CB slots, versioned bindings and a padded SO allocation."""
    if case == "missing":
        return
    native = ((0, -1, 0, 20), (1, 0, 0, -30), (0, 0, 1, 5), (0, 0, 0, 1))
    native_inverse = ((0, 1, 0, 30), (-1, 0, 0, 20), (0, 0, 1, -5), (0, 0, 0, 1))
    face = ((1, 0, 0, 20), (0, 0, -1, -26), (0, 1, 0, 9), (0, 0, 0, 1))
    face_inverse = ((1, 0, 0, -20), (0, 0, 1, -9), (0, -1, 0, -26), (0, 0, 0, 1))
    palette = [(1, 0, 0, 4, 0, 1, 0, -2, 0, 0, 1, 1)] * 4
    payload = []
    for ordinal, vertex in enumerate(eyes.vertices):
        x, y, z = vertex[0]
        extra = 1 if case == "conflicting" and ordinal == 0 else 0
        payload.append(struct.pack("<6f", x + 4, y - 2, z + 1 + extra, *vertex[1]))
    if case == "conflicting":
        palette[0] = (1, 0, 0, 4, 0, 1, 0, -2, 0, 0, 1, 2)
        blend = dump / "000001-vb1=22222222.buf"
        data = bytearray(blend.read_bytes())
        struct.pack_into("<I", data, 16, 0)
        blend.write_bytes(data)
    if case == "bad_palette":
        palette[3] = (1, 0, 0, 8, 0, 1, 0, -2, 0, 0, 1, 1)
    if case == "nonfinite":
        palette[3] = (math.nan,) + palette[3][1:]
    (dump / "000001-vs-t0=44444444.buf").write_bytes(b"".join(struct.pack("<12f", *row) for row in palette))
    stream("000002", 0, "33333333", b"".join(payload) + bytes(48),
           positions, 24, len(eyes.vertices), "trianglelist")
    for i, text in enumerate(log):
        text = text.replace("000001 Draw(", "000001 VSSetShaderResources(StartSlot:0, NumViews:1)\n"
                            "       0: resource=0xb000 hash=44444444\n000001 Draw(")
        for call, slot in (("000002", 2), ("000003", 3), ("000004", 5), ("000005", 1)):
            if call + " DrawIndexed(" not in text:
                continue
            matrix, inverse = (native, native_inverse) if call == "000002" else (face, face_inverse)
            if case == "unsupported_scale" and call == "000002":
                matrix = ((1e-12, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
                inverse = ((1e12, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
            values = [block[i][j] for block in (matrix, inverse)
                      for j in range(4) for i in range(4)] + [0] * 8
            if case == "bad_inverse" and call == "000004":
                values[16] += 2
            (dump / f"{call}-vs-cb{slot}=55555555.buf").write_bytes(struct.pack("<40f", *values))
            binding = "" if case == "unbound" and call == "000004" else (
                f"       {slot}: resource=0xa000 hash=55555555\n")
            prefix = (f"{call} Unmap(pResource:0xa000, Subresource:0)\n"
                      f"{call} VSSetConstantBuffers(StartSlot:0, NumBuffers:8)\n" + binding)
            text = text.replace(call + " DrawIndexed(", prefix + call + " DrawIndexed(")
        log[i] = text


def make_face_capture(parent, meshes, placement_case="valid"):
    dump = parent / "capture"
    dump.mkdir()
    material = copy.deepcopy(meshes[0].component.parts[0].textures)
    for i, texture in enumerate(material):
        path = dump / f"material-{i}.png"
        Image.new("RGB", (16, 16), (100 + i * 20, 80, 60)).save(path)
        texture.update(file=path.name, sha256=digest(path))
    log = ["000001 3DMigoto [ShaderRegex\\GIMIv1\\EnableTextureOverrides]\n"]

    def stream(call, slot, identity, payload, fields, stride, count, topology):
        path = dump / f"{call}-vb{slot}={identity}.buf"
        path.write_bytes(payload)
        path.with_suffix(".txt").write_text(header(stride, slot, fields).replace(
            "vertex count: 4", f"vertex count: {count}").replace("pointlist", topology))

    positions = [("POSITION", 0, "R32G32B32_FLOAT", 0), ("NORMAL", 0, "R32G32B32_FLOAT", 12)]
    # The native reference omits tangents and uses a separate four-influence stream.
    eyes = meshes[0]
    count = len(eyes.vertices)
    stream("000001", 0, "11111111", b"".join(struct.pack("<6f", *v[0], *v[1]) for v in eyes.vertices),
           positions, 24, count, "pointlist")
    stream("000001", 1, "22222222", struct.pack("<4f4I", 1, 0, 0, 0, 3, 0, 0, 0) * count,
           [("BLENDWEIGHT", 0, "R32G32B32A32_FLOAT", 0), ("BLENDINDICES", 0, "R32G32B32A32_SINT", 16)],
           32, count, "pointlist")
    log.append("""000001 SOSetTargets(NumBuffers:1)
       0: resource=0x9000 hash=33333333
000001 IASetVertexBuffers(StartSlot:0, NumBuffers:2)
       0: resource=0x1000 hash=11111111
       1: resource=0x2000 hash=22222222
000001 Draw(VertexCount:%d, StartVertexLocation:0)
000002 SOSetTargets(NumBuffers:0)
""" % count)
    for ordinal, mesh in enumerate(meshes, 2):
        call = f"{ordinal:06d}"
        vertex_count = len(mesh.vertices)
        identity = ordinal
        geometry_hash = f"{identity * 16:08x}"
        if ordinal > 2:
            stream(call, 0, geometry_hash, b"".join(struct.pack("<6f", *v[0], *v[1]) for v in mesh.vertices),
                   positions, 24, vertex_count, "trianglelist")
        stream(call, 1, f"{identity * 16 + 1:08x}", b"".join(struct.pack("<2f", *v[2]) for v in mesh.vertices),
               [("TEXCOORD", 0, "R32G32_FLOAT", 0)], 8, vertex_count, "trianglelist")
        ib = dump / f"{call}-ib={mesh.component.ib}.txt"
        indices = mesh.indices[0]
        ib.write_text(f"first index: 0\nindex count: {len(indices)}\ntopology: trianglelist\nformat: R32_UINT\n\n")
        ib.with_suffix(".buf").write_bytes(struct.pack("<" + "I" * len(indices), *indices))
        binding = "resource=0x9000 hash=33333333" if ordinal == 2 else f"resource=0x{identity * 16:x} hash={geometry_hash}"
        log.append(f"{call} IASetVertexBuffers(StartSlot:0, NumBuffers:2)\n"
                   f"       0: {binding}\n"
                   f"       1: resource=0x{identity * 16 + 1:x} hash={identity * 16 + 1:08x}\n"
                   f"{call} IASetIndexBuffer(pIndexBuffer:0x{identity * 16 + 2:x}, Format:42, Offset:0) hash={mesh.component.ib}\n"
                   f"{call} DrawIndexed(IndexCount:{len(indices)}, StartIndexLocation:0, BaseVertexLocation:0)\n")
    _write_matrices(dump, log, stream, positions, eyes, placement_case)
    if placement_case == "dedup":
        source = dump / "000003-vs-cb3=55555555.buf"
        (dump / "deduped").mkdir()
        source.rename(dump / "deduped" / "captured-object.buf")
        log.append("000005 3DMigoto Dumping Buffer C:\\capture\\" + source.name
                   + " -> C:\\capture\\deduped\\captured-object.buf\n")
    (dump / "log.txt").write_text("".join(log))
    if placement_case == "missing_first_snapshot":
        for path in list(dump.glob("000002-*")):
            shutil.copyfile(path, dump / ("000006-" + path.name[7:]))
        with (dump / "log.txt").open("a") as stream:
            stream.write(f"""000006 IASetVertexBuffers(StartSlot:0, NumBuffers:2)
       0: resource=0x9000 hash=33333333
       1: resource=0x21 hash=00000021
000006 IASetIndexBuffer(pIndexBuffer:0x22, Format:42, Offset:0) hash={eyes.component.ib}
000006 VSSetConstantBuffers(StartSlot:0, NumBuffers:8)
       2: resource=0xa000 hash=55555555
000006 DrawIndexed(IndexCount:{len(eyes.indices[0])}, StartIndexLocation:0, BaseVertexLocation:0)
""")
        for path in dump.glob("000002-vb0=*"):
            path.unlink()
    return dump, material
