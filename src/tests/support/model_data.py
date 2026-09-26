"""Small, fresh synthetic model inputs shared by archive and load tests."""

import struct


def basic_model_ini():
    """Return the ordinary three-vertex component used by archive tests."""
    return (
        "[TextureOverrideComponent01Position]\n"
        "vb0 = ResourceComponent01Position\n"
        "[TextureOverrideComponent01Texcoord]\n"
        "vb1 = ResourceComponent01Texcoord\n"
        "[TextureOverrideComponent01]\n"
        "ib = ResourceComponent01IB\n"
        "drawindexed = 3, 0, 0\n"
        "[ResourceComponent01Position]\n"
        "filename = p.buf\n"
        "stride = 12\n"
        "[ResourceComponent01Texcoord]\n"
        "filename = t.buf\n"
        "stride = 8\n"
        "[ResourceComponent01IB]\n"
        "filename = i.buf\n"
        "format = R32_UINT\n"
    )


def triangle_geometry():
    """Return fresh bytes for the position, UV and index resources."""
    return {
        "i.buf": struct.pack("<3I", 0, 1, 2),
        "p.buf": struct.pack(
            "<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0),
        "t.buf": struct.pack("<6f", 0, 0, 1, 0, 0, 1),
    }


def standard_component_resources(*, position_file="component01-position.buf",
                                 texcoord_file="component01-texcoord.buf",
                                 ib_file="component01.ib", position_stride=12,
                                 texcoord_stride=8,
                                 index_format="DXGI_FORMAT_R32_UINT"):
    """Return only ordinary resource declarations; callers own draw logic."""
    return (
        "\n[ResourceComponent01IB]\n"
        f"filename = {ib_file}\nformat = {index_format}\n"
        "\n[ResourceComponent01Position]\n"
        f"filename = {position_file}\nstride = {position_stride}\n"
        "\n[ResourceComponent01Texcoord]\n"
        f"filename = {texcoord_file}\nstride = {texcoord_stride}\n"
    )
