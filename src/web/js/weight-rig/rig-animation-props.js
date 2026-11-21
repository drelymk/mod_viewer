import {
  Bone,
  BufferAttribute,
  BufferGeometry,
  DetachedBindMode,
  DoubleSide,
  Matrix4,
  MeshStandardNodeMaterial,
  Skeleton,
  SkinnedMesh,
  Uint16BufferAttribute,
} from 'three';

// Game props (weapons) that the capture never drew. Extraction skins them to the
// verified rig in asset space, so clip bone transforms apply to them directly. They
// exist only while a clip is applied: hidden at rest, in manual posing and after Stop.
const LAYOUT = 'prop-v1';
// Per vertex: f32 position/normal/uv (32 bytes), u16x4 rig joints, f32x4 weights; then u32 indices.
const VERTEX_BYTES = 56;
const IDENTITY = new Matrix4();

function propMesh(descriptor, buffer, boneCount, textured) {
  const vertices = descriptor.vertex_count;
  const indices = descriptor.index_count;
  const records = new Float32Array(buffer, 0, vertices * 8);
  const joints = new Uint16Array(buffer, vertices * 32, vertices * 4);
  const weights = new Float32Array(buffer, vertices * 40, vertices * 4);
  const triangles = new Uint32Array(buffer, vertices * VERTEX_BYTES, indices);
  const position = new Float32Array(vertices * 3);
  const normal = new Float32Array(vertices * 3);
  const uv = new Float32Array(vertices * 2);
  for (let vertex = 0; vertex < vertices; vertex++) {
    position.set(records.subarray(vertex * 8, vertex * 8 + 3), vertex * 3);
    normal.set(records.subarray(vertex * 8 + 3, vertex * 8 + 6), vertex * 3);
    uv.set(records.subarray(vertex * 8 + 6, vertex * 8 + 8), vertex * 2);
  }
  // Each prop gets a compact skeleton of only the rig bones it uses.
  const palette = [];
  const slots = new Map();
  const skinIndex = new Uint16Array(vertices * 4);
  for (let slot = 0; slot < joints.length; slot++) {
    // Unweighted slots contribute nothing; they reuse the first bone.
    if (!(weights[slot] > 0)) continue;
    const bone = joints[slot];
    if (bone >= boneCount) throw new Error('The prop does not match the loaded rig.');
    if (!slots.has(bone)) {
      slots.set(bone, palette.length);
      palette.push(bone);
    }
    skinIndex[slot] = slots.get(bone);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(position, 3));
  geometry.setAttribute('normal', new BufferAttribute(normal, 3));
  geometry.setAttribute('uv', new BufferAttribute(uv, 2));
  geometry.setAttribute('skinIndex', new Uint16BufferAttribute(skinIndex, 4));
  geometry.setAttribute('skinWeight', new BufferAttribute(new Float32Array(weights), 4));
  geometry.setIndex(new BufferAttribute(new Uint32Array(triangles), 1));
  // Props without proven textures stay a neutral untextured material.
  const material =
    textured?.material ||
    new MeshStandardNodeMaterial({ color: 0x9aa0a8, metalness: 0.35, roughness: 0.5, side: DoubleSide });
  // Bones stay outside the scene graph; their world matrices are the clip's asset-space
  // bone deltas, and an identity bind keeps the prop vertices in asset space.
  const bones = palette.map(() => {
    const bone = new Bone();
    bone.matrixAutoUpdate = false;
    bone.matrixWorldAutoUpdate = false;
    return bone;
  });
  const mesh = new SkinnedMesh(geometry, material);
  mesh.name = descriptor.name;
  mesh.userData.animationProp = true;
  mesh.bindMode = DetachedBindMode;
  mesh.bind(
    new Skeleton(
      bones,
      palette.map(() => new Matrix4()),
    ),
    new Matrix4(),
  );
  mesh.frustumCulled = false;
  mesh.matrixAutoUpdate = false;
  textured?.bind(mesh);
  return { mesh, palette, textured };
}

// `materialFor(descriptor, reference, session)` may return {material, bind(mesh), dispose()}
// for a prop with textures; null keeps the untextured material.
export function createAnimationProps({ loadProps, getScene, requestRender, materialFor = () => null }) {
  let rig = null;
  let generation = 0;
  let props = [];
  let reference = null;
  let transforms = null;
  function update() {
    const scene = getScene();
    for (const prop of props) {
      if (!transforms) {
        prop.mesh.removeFromParent();
        continue;
      }
      prop.palette.forEach((bone, slot) => {
        prop.mesh.skeleton.bones[slot].matrixWorld.copy(transforms.get(bone) || IDENTITY);
      });
      prop.mesh.skeleton.update();
      if (prop.mesh.parent !== scene) scene.add(prop.mesh);
    }
    requestRender();
  }
  function dispose() {
    generation += 1;
    for (const { mesh, textured } of props) {
      mesh.removeFromParent();
      mesh.geometry.dispose();
      textured?.dispose();
      mesh.material.dispose();
      mesh.skeleton.dispose();
    }
    props = [];
    rig = null;
    transforms = null;
  }
  async function load(target, current) {
    const response = await loadProps(target.animation_session);
    if (current !== generation) return;
    if (response?.status !== 'ok' || !Array.isArray(response.props)) return;
    const loaded = [];
    for (const descriptor of response.props) {
      const size = descriptor.vertex_count * VERTEX_BYTES + descriptor.index_count * 4;
      if (descriptor.layout !== LAYOUT || descriptor.data?.length !== size) continue;
      const download = await fetch(descriptor.data.url, { cache: 'no-store' });
      const buffer = download.ok ? await download.arrayBuffer() : null;
      if (current !== generation) return;
      if (buffer?.byteLength !== size) continue;
      const prop = propMesh(
        descriptor,
        buffer,
        target.bones.length,
        materialFor(descriptor, reference, target.animation_session),
      );
      // Follow the body's model transform (orientation, placement) every frame.
      prop.mesh.updateMatrixWorld = function updateMatrixWorld() {
        this.matrixWorld.copy(reference?.matrixWorld || IDENTITY);
      };
      loaded.push(prop);
    }
    props = loaded;
    update();
  }
  return {
    // Load the rig's props once; a different rig replaces them.
    prepare(target, referenceMesh) {
      reference = referenceMesh || null;
      if (target === rig) return;
      dispose();
      rig = target;
      if (!target?.animation_props?.length || !target.animation_session) return;
      const current = generation;
      // Props are optional; a failed load keeps the clip playing without them.
      load(target, current).catch(() => {});
    },
    apply(next) {
      transforms = next;
      update();
    },
    hide() {
      transforms = null;
      update();
    },
    dispose,
  };
}
