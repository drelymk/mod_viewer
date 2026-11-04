"""Build-scoped geometry reuse across packing and material resolution."""

from dataclasses import dataclass, field

from .buffers import BufferStore
from .draw_call import _freeze


def draw_geometry_key(draw):
    """Project the authored inputs that affect packed vertex/index data."""
    return (draw.count, draw.start, draw.base, draw.ib_file, draw.index_size,
            draw.position_file, draw.position_stride, draw.texcoord_file,
            draw.texcoord_stride, draw.normal_source)


def animation_geometry_key(family):
    """Keep texture interpretation out of immutable animation byte identity."""
    if family is None:
        return None
    return _freeze((
        family["frame_start"], family["frame_end"],
        family.get("position_switching"), family.get("binding_frames"),
        tuple((frame, draw_geometry_key(draw))
              for frame, draw in sorted(family["draws"].items())),
    ))


@dataclass
class PackedVertexMapping:
    """The source mapping retained after vertex bytes enter the shared blob."""

    used_vertices: tuple[int, ...]


@dataclass
class MeshGeometryCache:
    """Owned by one load; discarded once its Asset phase finishes."""

    buffers: BufferStore
    sparse_shapes: dict = field(default_factory=dict)
    gimi_shared: dict = field(default_factory=dict)
    draws: dict = field(default_factory=dict)
    publications: dict = field(default_factory=dict)

    def remember(self, key, packed, animation, compute):
        if key not in self.draws:
            mapping = PackedVertexMapping(packed.used_vertices)
            self.publications[id(mapping)] = self.publications.pop(id(packed))
            self.draws[key] = (mapping, animation, compute)

    def publish(self, packed, geometry):
        key = id(packed)
        if key not in self.publications:
            entry = {"pos": geometry.add(packed.positions),
                     "idx": geometry.add(packed.indices)}
            if packed.texcoords is not None:
                entry["uv"] = geometry.add(packed.texcoords)
            if packed.normals is not None:
                entry["normal"] = geometry.add(packed.normals)
            if packed.shape_targets:
                targets = []
                for target in packed.shape_targets:
                    item = {"var": target.var,
                            "pos": geometry.add(target.positions)}
                    if target.mode:
                        item["mode"] = target.mode
                    if target.low_positions is not None:
                        item["low_pos"] = geometry.add(target.low_positions)
                    targets.append(item)
                entry["shape_targets"] = targets
            self.publications[key] = entry
        return dict(self.publications[key])
