import struct


def header(stride, slot, elements):
    text = f"stride: {stride}\nfirst vertex: 0\nvertex count: 4\ntopology: pointlist\n"
    for i, (semantic, idx, fmt, offset) in enumerate(elements):
        text += f"element[{i}]:\n  SemanticName: {semantic}\n  SemanticIndex: {idx}\n  Format: {fmt}\n  InputSlot: {slot}\n  AlignedByteOffset: {offset}\n  InputSlotClass: per-vertex\n  InstanceDataStepRate: 0\n"
    return text + "\nvertex-data:\n"


def make_capture(tmp_path):
    root = tmp_path / "capture space Ω"
    root.mkdir()
    pos = b"".join(struct.pack("<10f", i, 0, 0, 0, 0, 1, 1, 0, 0, 1) for i in range(4))
    blend = struct.pack("<4I", 3, 3, 3, 3)
    uv = b"".join(struct.pack("<4B4e", 255, 128, 0, 255, 0, 0, 1, 1) for _ in range(4))
    for name, data, text in [
        ("000001-vb0=11111111-vs=aaaaaaaaaaaaaaaa", pos, header(40, 0, [("POSITION",0,"R32G32B32_FLOAT",0),("NORMAL",0,"R32G32B32_FLOAT",12),("TANGENT",0,"R32G32B32A32_FLOAT",24)])),
        ("000001-vb1=22222222-vs=aaaaaaaaaaaaaaaa", uv, header(12, 1, [("COLOR",0,"R8G8B8A8_UNORM",0),("TEXCOORD",0,"R16G16_FLOAT",4),("TEXCOORD",1,"R16G16_FLOAT",8)])),
        ("000001-vb2=33333333-vs=aaaaaaaaaaaaaaaa", blend, header(4, 2, [("BLENDINDICES",0,"R32_UINT",0)]))]:
        (root / (name + ".buf")).write_bytes(data)
        (root / (name + ".txt")).write_text(text)
    for call_id in ("000002", "000003"):
        name = call_id + "-ib=44444444-vs=bbbbbbbbbbbbbbbb"
        (root / (name + ".buf")).write_bytes(struct.pack("<6H", 3, 3, 3, 0, 1, 2))
        (root / (name + ".txt")).write_text("byte offset: 0\nfirst index: 3\nindex count: 3\ntopology: trianglelist\nformat: DXGI_FORMAT_R16_UINT\n\n0 1 2\n")
    (root / "log.txt").write_text("""analyse_options: 63d
000001 3DMigoto [ShaderRegex\\ZZMIv1\\EnableTextureOverrides]
000001 SOSetTargets(NumBuffers:1)
       0: resource=0x50 hash=55555555
000001 IASetVertexBuffers(StartSlot:0, NumBuffers:3)
       0: resource=0x10 hash=11111111
       1: resource=0x20 hash=22222222
       2: resource=0x30 hash=33333333
000001 VSSetShader(pVertexShader:0x1) hash=aaaaaaaaaaaaaaaa
000001 Draw(VertexCount:4, StartVertexLocation:0)
000002 SOSetTargets(NumBuffers:0)
000002 IASetVertexBuffers(StartSlot:0, NumBuffers:1)
       0: resource=0x50 hash=55555555
000002 IASetIndexBuffer(pIndexBuffer:0x40, Format:57, Offset:0) hash=44444444
000002 VSSetShader(pVertexShader:0x2) hash=bbbbbbbbbbbbbbbb
000002 DrawIndexed(IndexCount:3, StartIndexLocation:3, BaseVertexLocation:1)
000003 DrawIndexed(IndexCount:3, StartIndexLocation:3, BaseVertexLocation:1)
""", encoding="utf-8")
    return root
