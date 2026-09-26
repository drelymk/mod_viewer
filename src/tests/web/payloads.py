"""Generated inputs with opaque identities and no external corpus data."""

import base64
import io
import struct

from PIL import Image


def model_payload(count=1):
    blob = bytearray()

    def append(kind, values):
        offset = len(blob)
        blob.extend(struct.pack(f"<{len(values)}{kind}", *values))
        return {"offset": offset, "length": len(blob) - offset}

    meshes = {}
    for index in range(count):
        meshes[f"mesh-{index:02d}"] = {
            "component": f"component-{index:02d}",
            "drawindexed": [3, 0, 0],
            "pos": append('f', [0, 0, 0, 1, 0, 0, 0, 1, 0]),
            "uv": append('f', [0, 0, 1, 0, 0, 1]),
            "idx": append('I', [0, 1, 2]),
            "conditions": [],
            "sources": [{"ini": "source-01.ini", "line": 1,
                         "section": "TextureOverrideFixture"}],
        }
    return {
        "meshes": meshes,
        "_fixture_blob": bytes(blob),
        "textures": {}, "texture_pools": {},
        "controls": {"toggles": {}, "menu": {}, "present": {"target_inis": []}},
        "state": {"rules": [], "defaults": {}},
        "metadata": {"mesh_names": {}, "material_profiles": {}},
    }


def solid_texture(rgb):
    with Image.new('RGB', (4, 4), rgb) as image:
        output = io.BytesIO()
        image.save(output, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')


def append_stream(payload, kind, values):
    offset = len(payload['_fixture_blob'])
    data = struct.pack(f"<{len(values)}{kind}", *values)
    payload['_fixture_blob'] += data
    return {'offset': offset, 'length': len(data)}


def controlled_payload(count=1):
    payload = model_payload(count)
    payload['controls']['toggles'] = {'KeyFixture': {
        'name': 'control-01', 'ini': 'source-01.ini', 'section': 'KeyFixture', 'wired': True,
        'vars': [{'var': 'input01', 'default': '0', 'values': ['0', '1']}],
    }}
    payload['state']['defaults'] = {'input01': '0'}
    for mesh in payload['meshes'].values():
        mesh['conditions'] = [[{'var': 'input01', 'value': '0', 'negate': False}]]
    return payload


def textured_payload(count=1, extension='png'):
    payload = model_payload(count)
    keys = [f'diffuse::texture-{index:02d}.{extension}' for index in (1, 2)]
    payload['textures'] = {keys[0]: solid_texture((240, 24, 24)), keys[1]: solid_texture((24, 24, 240))}
    payload['texture_pools'] = {'pool-01': [{'tex_key': key, 'label': f'texture-{index:02d}'}
                                         for index, key in enumerate(keys, 1)]}
    for mesh in payload['meshes'].values():
        mesh.update(tex_key=keys[0], texture_pool_id='pool-01')
    return payload
