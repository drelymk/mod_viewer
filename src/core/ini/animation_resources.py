"""Shared animation resource loading, shader recognition and layout validation."""

from functools import lru_cache
import os
import re

from .sections import ResourceTable


_COMPUTE_SHADER_RE = re.compile(r"^\s*cs\s*=\s*(\S+)\s*$", re.I)


def _resource_get(resources, name):
    return resources.get_ci(name)


def _read_resource_bytes(path, source):
    if not path:
        return None
    try:
        if source is not None and getattr(source, "virtual", False):
            return source.read_bytes(path)
        with open(path, "rb") as stream:
            return stream.read()
    except (OSError, TypeError, ValueError):
        return None


def _resource_size(path, source):
    if not path:
        return None
    try:
        return (source.size(path) if source is not None
                else os.path.getsize(path))
    except (OSError, TypeError, ValueError):
        return None


def _resource_path(mod_dir, filename, source):
    if not filename:
        return None
    if source is not None:
        return source.resolve_resource(filename)
    if mod_dir is None:
        return None
    from ..resource_paths import safe_resource_path
    return safe_resource_path(mod_dir, filename)


def _shader_path(mod_dir, ini_path, value, source):
    """Resolve a shader reference without introducing an alternate loader."""
    value = str(value).strip().strip('"')
    if not value:
        return None
    if source is not None and source.is_resource_reference(ini_path):
        ini_name = source.logical_path(ini_path)
        relative = os.path.normpath(os.path.join(os.path.dirname(ini_name), value))
        return source.resolve_resource(relative)
    if mod_dir is None:
        return None
    from ..resource_paths import safe_resource_path
    return safe_resource_path(mod_dir, value)


def _strip_hlsl_comments(text):
    text = re.sub(r"//[^\r\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


_NUMTHREADS_RE = re.compile(
    r"\[\s*numthreads\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)\s*\]",
    re.I)


def _identify_shape_weight_operation(
        text, expected_ini_input, *, require_delta_application):
    """Recognize supported weights only when they use the supplied input."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    compact = re.sub(r"\s+", "", source).lower()
    slot, component = expected_ini_input
    aliases = {name.lower() for name in re.findall(
        rf"^\s*#define\s+([a-z_]\w*)\s+IniParams\s*\[\s*"
        rf"{int(slot)}\s*\]\s*\.\s*{re.escape(component)}\b",
        source, re.I | re.M)}
    phase_names = set(aliases)
    for value, source_name in re.findall(
            r"(?:float|half)([a-z_]\w*)=float\(([a-z_]\w*)\)",
            compact, re.I):
        if source_name in aliases:
            phase_names.add(value)

    sine_formula = re.compile(
        r"0\.5f?\*\(sin\((?P<phase>[a-z_]\w*)\*30(?:\.0*)?f?\)"
        r"\+1(?:\.0*)?f?\)", re.I)
    for match in sine_formula.finditer(compact):
        if match.group("phase") not in phase_names:
            continue
        formula = match.group(0)
        weight_names = set(re.findall(
            rf"(?:float|half)([a-z_]\w*)=\(*{re.escape(formula)}\)*",
            compact))
        if not require_delta_application and weight_names:
            return {
                "kind": "sine", "scale": 30.0,
                "amplitude": 0.5, "offset": 0.5,
            }
        expressions = [re.escape(name) for name in weight_names]
        expressions.append(rf"\(?{re.escape(formula)}\)?")
        if all(any(re.search(
                rf"diff\.{axis}\*{expression}(?:\W|$)", compact)
                for expression in expressions)
               for axis in ("position", "normal")):
            return {
                "kind": "sine", "scale": 30.0,
                "amplitude": 0.5, "offset": 0.5,
            }

    if require_delta_application:
        for alias in phase_names:
            if all(re.search(
                    rf"diff\.{axis}\*{re.escape(alias)}(?:\W|$)", compact)
                   for axis in ("position", "normal")):
                return {"kind": "linear"}
    return None


def _pose_kernel_signature(body, names, index_field="indicies"):
    """Normalize one known operation sequence, without interpreting HLSL."""
    body = re.sub(r"\((int|uint)\)\s*(\w+)", r"int(\2)", body)
    # The only branch in this adapter protects quaternion normalization.
    body = re.sub(r"if\s*\(([^{};]+)\)\s*\{\s*([^{}]+;)\s*\}", r"if(\1)\2", body)
    locals_by_name = {}
    pattern = r"(?<!\w)(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?[fF]?|\b[a-zA-Z_]\w*\b"
    def token(match):
        value = match.group()
        if value[0].isdigit() or value[0] == ".":
            return format(float(value.rstrip("fF")), ".12g")
        if match.start() and body[:match.start()].rstrip().endswith("."):
            return "indicies" if value.lower() == index_field.lower() else value.lower()
        if value in names:
            return names[value]
        if re.fullmatch(r"(?:u?int|float)[1-4]?", value):
            return value.removeprefix("u") if value.startswith("uint") else value
        if value in {"frac", "sign",
                     "dot", "length", "normalize", "if"}:
            return value
        return locals_by_name.setdefault(value, f"local{len(locals_by_name)}")
    return re.sub(r"\s+", "", re.sub(pattern, token, body))


@lru_cache(maxsize=2)
def _pose_kernel_contract(coordinate_transform):
    """The shared unrolled DLB adapter: four influences and two adjacent frames."""
    axes = ("x", "-z", "y") if coordinate_transform == "swap_yz_negate" else ("x", "y", "z")
    def components(name, fields):
        return ",".join(("-" if axis.startswith("-") else "") + name + "." + axis[-1]
                        for axis in fields)
    body = ["uint i=thread.x", "Blend b=blend[i]", "Vertex v=base[i]",
            f"float4 pos=float4({components('v.position', axes)},1)",
            f"float4 normal=float4({components('v.normal', axes)},0)",
            "int frame=int(time)", "float inter=frac(time)", "float inter_prev=1-inter", "int count=int(bones)",
            "int4 prev=frame*count+b.indicies", "int4 next=(frame+1)*count+b.indicies"]
    for frame in ("prev", "next"):
        body.extend(f"Pose {frame}{index}=pose[{frame}.{axis}]" for index, axis in enumerate("xyzw"))
    body.append("float4 weights=b.weights")
    for name, field in (("scale", "S"), ("bias", "T")):
        sums = ["+".join(f"{frame}{index}.{field}*weights.{axis}"
                        for index, axis in enumerate("xyzw")) for frame in ("prev", "next")]
        body.append(f"float3 {name}=({sums[0]})*inter_prev+({sums[1]})*inter")
    body.extend(["pos.xyz=pos.xyz*scale+bias",
                 "float4 qr=prev0.QR*weights.x*inter_prev", "float4 qd=prev0.QD*weights.x*inter_prev"])
    terms = [(frame, index, axis, alpha)
             for frame, alpha in (("prev", "inter_prev"), ("next", "inter"))
             for index, axis in enumerate("xyzw") if (frame, index) != ("prev", 0)]
    body.extend(f"float sign_{frame}{index}=sign(dot(prev0.QR,{frame}{index}.QR))"
                for frame, index, _, _ in terms)
    for frame, index, axis, alpha in terms:
        body.extend(f"{name}+={frame}{index}.{field}*weights.{axis}*{alpha}*sign_{frame}{index}"
                    for name, field in (("qr", "QR"), ("qd", "QD")))
    body.extend(["float size=length(qr)", "if(size<1e-6)size=1e-6", "qr/=size", "qd/=size",
                 "float qx=qr.x,qy=qr.y,qz=qr.z,qw=qr.w", "float dx=qd.x,dy=qd.y,dz=qd.z,dw=qd.w",
                 "float m00=1-2*qy*qy-2*qz*qz", "float m10=2*(qx*qy+qw*qz)", "float m20=2*(qx*qz-qw*qy)",
                 "float t0=2*(-dw*qx+dx*qw-dy*qz+dz*qy)",
                 "float m01=2*(qx*qy-qw*qz)", "float m11=1-2*qx*qx-2*qz*qz", "float m21=2*(qy*qz+qw*qx)",
                 "float t1=2*(-dw*qy+dx*qz+dy*qw-dz*qx)",
                 "float m02=2*(qx*qz+qw*qy)", "float m12=2*(qy*qz-qw*qx)", "float m22=1-2*qx*qx-2*qy*qy",
                 "float t2=2*(-dw*qz-dx*qy+dy*qx+dz*qw)"])
    for result, vector, w in (("pos_result", "pos", 1), ("normal_result", "normal", 0)):
        body.append("float4 " + result)
        for row, axis in enumerate("xyz"):
            terms = "+".join(f"m{row}{col}*{vector}.{component}"
                             for col, component in enumerate("xyz"))
            body.append(f"{result}.{axis}={terms}" + (f"+t{row}*pos.w" if w else ""))
        body.append(f"{result}.w={w}")
    output_axes = ("x", "z", "-y") if coordinate_transform == "swap_yz_negate" else axes
    for result, field in (("pos_result", "position"), ("normal_result", "normal")):
        vector = f"float3({components(result, output_axes)})"
        body.append(f"output[i].{field}=" + (f"normalize({vector})" if field == "normal" else vector))
    return _pose_kernel_signature(";".join(body) + ";", {
        "base": "buffer50", "blend": "buffer51", "pose": "buffer52",
        "output": "buffer5", "thread": "thread", "time": "phase88", "bones": "phase89",
        "Vertex": "vertex", "Blend": "blend", "Pose": "pose"})


def _pose_shader_signature(source, buffers, index_field):
    match = re.search(r"\bvoid\s+main\s*\(\s*uint3\s+(\w+)\s*:\s*SV_DispatchThreadID\s*\)\s*\{", source)
    if match is None:
        return None
    depth, end = 1, match.end()
    while end < len(source) and depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    if depth:
        return None
    names = {buffer[2]: f"buffer{slot}" for slot, buffer in buffers.items()}
    names.update({buffers[slot][1]: kind for slot, kind in (
        (5, "vertex"), (50, "vertex"), (51, "blend"), (52, "pose"))})
    names[match.group(1)] = "thread"
    aliases = re.findall(r"^\s*#define\s+(\w+)\s+IniParams\s*\[\s*(88|89)\s*\]\s*\.\s*x\s*$", source, re.M)
    # Only unused single-line object macros can be ignored without preprocessing.
    unused_defines = sum(
        name not in dict(aliases) and len(re.findall(rf"\b{re.escape(name)}\b", source)) == 1
        for name in re.findall(
            r"^[ \t]*#define[ \t]+([A-Za-z_]\w*)(?:[ \t]+[^\\\r\n]*)?\r?$", source, re.M))
    if (len(re.findall(r"^\s*#", source, re.M)) != len(aliases) + unused_defines
            or len(dict(aliases)) != len(aliases)
            or {slot for _, slot in aliases} != {"88", "89"}
            or not re.search(r"Texture1D\s*<\s*float4\s*>\s*IniParams\s*:\s*register\s*\(\s*t120\s*\)", source)):
        return None
    for alias, slot in aliases:
        names[alias] = f"phase{slot}"
    return _pose_kernel_signature(source[match.end():end - 1], names, index_field)


def _identify_pose_transform(source):
    """Match the fixed vertex/blend/pose adapter, including its output writes."""
    buffers = {int(slot): (kind.lower(), struct_name, name, register.lower())
               for kind, struct_name, name, register, slot in re.findall(
                   r"\b(RWStructuredBuffer|StructuredBuffer)\s*<\s*(\w+)\s*>"
                   r"\s*(\w+)\s*:\s*register\s*\(\s*([tu])(\d+)\s*\)",
                   source, re.I)}
    layouts = {}
    fields = re.compile(r"\b(\w+)\s+(\w+)\s*;")
    for name, body in re.findall(r"\bstruct\s+(\w+)\s*\{([^}]*)\}", source, re.I | re.S):
        if not fields.sub("", body).strip():
            layouts[name] = [(kind.lower(), field.lower())
                             for kind, field in fields.findall(body)]
    expected = {
        5: [("float3", "position"), ("float3", "normal"), ("float4", "tangent")],
        50: [("float3", "position"), ("float3", "normal"), ("float4", "tangent")],
        52: [("float3", "s"), ("float3", "t"), ("float4", "qr"), ("float4", "qd")],
    }
    if not {5, 50, 51, 52} <= buffers.keys():
        return None
    if any(layouts.get(buffers[slot][1]) != fields
           for slot, fields in expected.items()):
        return None
    blend = layouts.get(buffers[51][1], ())
    if (len(blend) != 2 or blend[0] != ("float4", "weights")
            or blend[1][0] not in {"int4", "uint4"}
            or (buffers[5][0], buffers[5][3]) != ("rwstructuredbuffer", "u")
            or any((buffers[slot][0], buffers[slot][3]) != ("structuredbuffer", "t")
                   for slot in (50, 51, 52))):
        return None
    if not all(re.search(
            rf"\b{re.escape(buffers[slot][2])}\s*\[", source)
               for slot in (50, 51, 52)):
        return None
    signature = _pose_shader_signature(source, buffers, blend[1][1])
    for transform in ("identity", "swap_yz_negate"):
        if signature == _pose_kernel_contract(transform):
            return transform
    return None


def _identify_compute_shader(text):
    """Read the small adapter surface needed by GIMI compute shaders."""
    if not text:
        return None
    source = _strip_hlsl_comments(text)
    match = _NUMTHREADS_RE.search(source)
    if match is None:
        return None
    threads = int(match.group(1))
    if threads <= 0 or tuple(map(int, match.groups()[1:])) != (1, 1):
        return None
    weight_operation = _identify_shape_weight_operation(
        source, (88, "x"), require_delta_application=True)
    coordinate_transform = "identity" if weight_operation is not None else _identify_pose_transform(source)
    kind = "shape" if weight_operation is not None else ("pose" if coordinate_transform else None)
    if kind is None:
        return None
    return {
        "kind": kind,
        "threads": threads,
        "coordinate_transform": coordinate_transform,
        "weight_operation": weight_operation,
    }


def _resolved_resource(resources, copy_sources, name, visiting=None):
    info = _resource_get(resources, name)
    if info.get("filename"):
        return info
    key = str(name).casefold()
    visiting = set(visiting or ())
    if key in visiting:
        return {}
    visiting.add(key)
    for candidate in copy_sources.get(key, ()):
        resolved = _resolved_resource(resources, copy_sources, candidate, visiting)
        if resolved.get("filename"):
            return resolved
    return {}


class _AnimationResources:
    """Read and resolve immutable inputs once within one INI discovery."""

    def __init__(self, mod_dir, ini_path, source, resources=None, copy_sources=None):
        self.mod_dir, self.ini_path, self.source = mod_dir, ini_path, source
        self.resources = ResourceTable.normalize(resources)
        self.copy_sources = copy_sources
        self._resolved, self._sizes, self._texts, self._adapters = {}, {}, {}, {}

    def resource(self, name):
        key = str(name)
        if key not in self._resolved:
            self._resolved[key] = _resolved_resource(
                self.resources, self.copy_sources, name)
        return self._resolved[key]

    def size(self, filename):
        if filename not in self._sizes:
            path = _resource_path(self.mod_dir, filename, self.source)
            self._sizes[filename] = _resource_size(path, self.source)
        return self._sizes[filename]

    def shader_text(self, value):
        path = _shader_path(self.mod_dir, self.ini_path, value, self.source)
        if path not in self._texts:
            data = _read_resource_bytes(path, self.source)
            self._texts[path] = data.decode("utf-8", errors="ignore") if data else None
        return self._texts[path]

    def shader(self, value):
        if value not in self._adapters:
            self._adapters[value] = _identify_compute_shader(self.shader_text(value))
        return self._adapters[value]


def _validate_compute_layout(inputs, shape_passes, pose=None, *, bone_count=None):
    """Validate shared GIMI layouts before exposing a descriptor."""
    base_resource = (shape_passes[0]["base_resource"] if shape_passes
                     else pose["base_resource"])
    base_info = inputs.resource(base_resource)
    if base_info.get("stride") != 40:
        return None
    base_size = inputs.size(base_info.get("filename"))
    if base_size is None or base_size % 40:
        return None
    vertex_count = base_size // 40
    if vertex_count <= 0:
        return None
    for item in shape_passes:
        info = inputs.resource(item["target_resource"])
        if (info.get("stride") != 40
                or str(item["base_resource"]).casefold() != str(base_resource).casefold()):
            return None
        target_size = inputs.size(info.get("filename"))
        if target_size is None or target_size % 40:
            return None
    result = {
        "base_file": base_info["filename"],
        "vertex_count": vertex_count,
    }
    if pose is None:
        return result
    blend_info = inputs.resource(pose["blend_resource"])
    pose_info = inputs.resource(pose["pose_resource"])
    if (blend_info.get("stride") != 32 or pose_info.get("stride") != 56
            or not bone_count):
        return None
    blend_size = inputs.size(blend_info.get("filename"))
    pose_size = inputs.size(pose_info.get("filename"))
    if (blend_size != vertex_count * 32
            or pose_size is None or pose_size % (bone_count * 56)):
        return None
    frame_count = pose_size // (bone_count * 56)
    if frame_count < 2:
        return None
    result.update({
        "blend_file": blend_info["filename"],
        "pose_file": pose_info["filename"],
        "frame_count": frame_count,
    })
    return result
