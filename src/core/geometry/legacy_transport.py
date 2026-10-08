"""External flat-payload compatibility; the app uses binary geometry only."""

import base64


def flat_mesh_payload(built, *, encode_geometry=True):
    """Adapt a binary mesh result for scripts using ``build_mesh_payload``.

    Geometry references, including nested animation and shape streams, are
    converted only here. Passing a caller-owned blob retains binary references.
    """
    def project(value):
        if isinstance(value, dict):
            if encode_geometry and set(value) == {"offset", "length"}:
                start = value["offset"]
                raw = built.geometry.data[start:start + value["length"]]
                return base64.b64encode(raw).decode("ascii")
            return {key: project(item) for key, item in value.items()}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    payload = project(built.meshes)
    payload["__textures__"] = built.textures
    return payload
