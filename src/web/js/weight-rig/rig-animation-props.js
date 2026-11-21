import {
  Bone,
  BufferAttribute,
  BufferGeometry,
  DetachedBindMode,
  DoubleSide,
  Matrix4,
  MeshStandardNodeMaterial,
  Quaternion,
  Skeleton,
  SkinnedMesh,
  Uint16BufferAttribute,
  Vector3,
} from 'three';

// Game props (weapons) that the capture never drew. Extraction skins them in asset
// space, so clip bone transforms apply to them directly. They exist only while a clip
// is applied: hidden at rest, in manual posing and after Stop.
//
// Rigid props are skinned to rig bones. Animated props have their own skeleton: per
// paired clip, a track gives each prop bone's asset-space transform relative to its
// socket's rest pose, and the socket's clip transform is applied on top. An animated
// prop shows only while its paired animation runs.
const LAYOUT = 'prop-v1';
const TRACK_LAYOUT = 'prop-track-v1';
// Per vertex: f32 position/normal/uv (32 bytes), u16x4 joints, f32x4 weights; then u32 indices.
const VERTEX_BYTES = 56;
// Per track frame and prop bone: f32 xyz translation and xyzw quaternion.
const TRACK_FLOATS = 7;
const IDENTITY = new Matrix4();
const ONE = new Vector3(1, 1, 1);

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
  // Each prop gets a compact skeleton of only the (rig or prop) bones it uses.
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
  // Bones stay outside the scene graph; their world matrices are asset-space deltas,
  // and an identity bind keeps the prop vertices in asset space.
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

function disposeProp({ mesh, textured }) {
  mesh.removeFromParent();
  mesh.geometry.dispose();
  textured?.dispose();
  mesh.material.dispose();
  mesh.skeleton.dispose();
}

// Validated per-clip track views of an animated prop, or null when unusable.
function propTracks(descriptor, buffer) {
  const bones = descriptor.bone_count;
  if (!Number.isInteger(descriptor.socket) || !Number.isInteger(bones) || bones < 1) return null;
  const tracks = new Map();
  for (const [clip, track] of Object.entries(descriptor.tracks.clips || {})) {
    const { offset, frame_count: frames, duration } = track || {};
    const size = frames * bones * TRACK_FLOATS * 4;
    if (
      !Number.isInteger(offset) ||
      !Number.isInteger(frames) ||
      frames < 2 ||
      !(duration > 0) ||
      offset % 4 ||
      offset + size > buffer.byteLength
    )
      return null;
    tracks.set(clip, { values: new Float32Array(buffer, offset, size / 4), frames, duration });
  }
  return tracks.size ? tracks : null;
}

const SAMPLE = {
  translation: new Vector3(),
  next: new Vector3(),
  rotation: new Quaternion(),
  following: new Quaternion(),
  matrix: new Matrix4(),
};

// The prop bones' socket-relative transforms at a track time, set onto the skeleton.
function poseTrack(prop, track, socket, time) {
  const position = Math.max(0, Math.min(1, time / track.duration)) * (track.frames - 1);
  const first = Math.floor(position);
  const second = Math.min(first + 1, track.frames - 1);
  const weight = position - first;
  const bones = prop.animation.boneCount;
  prop.palette.forEach((bone, slot) => {
    const a = (first * bones + bone) * TRACK_FLOATS;
    const b = (second * bones + bone) * TRACK_FLOATS;
    const values = track.values;
    SAMPLE.translation.fromArray(values, a).lerp(SAMPLE.next.fromArray(values, b), weight);
    SAMPLE.rotation.fromArray(values, a + 3).slerp(SAMPLE.following.fromArray(values, b + 3), weight);
    SAMPLE.matrix.compose(SAMPLE.translation, SAMPLE.rotation, ONE);
    prop.mesh.skeleton.bones[slot].matrixWorld.multiplyMatrices(socket, SAMPLE.matrix);
  });
}

// `materialFor(descriptor, reference, session)` may return {material, bind(mesh), dispose()}
// for a prop with textures; null keeps the untextured material.
export function createAnimationProps({ loadProps, getScene, requestRender, materialFor = () => null }) {
  let rig = null;
  let generation = 0;
  let props = [];
  let reference = null;
  let transforms = null;
  let playback = null;
  function visible(prop) {
    if (!transforms) return false;
    if (!prop.animation) return true;
    const track = playback && prop.animation.tracks.get(playback.clip);
    return !!track && playback.time <= track.duration + 1e-6;
  }
  function update() {
    const scene = getScene();
    for (const prop of props) {
      if (!visible(prop)) {
        prop.mesh.removeFromParent();
        continue;
      }
      if (prop.animation) {
        const socket = transforms.get(prop.animation.socket) || IDENTITY;
        poseTrack(prop, prop.animation.tracks.get(playback.clip), socket, playback.time);
      } else {
        prop.palette.forEach((bone, slot) => {
          prop.mesh.skeleton.bones[slot].matrixWorld.copy(transforms.get(bone) || IDENTITY);
        });
      }
      prop.mesh.skeleton.update();
      if (prop.mesh.parent !== scene) scene.add(prop.mesh);
    }
    requestRender();
  }
  function dispose() {
    generation += 1;
    props.forEach(disposeProp);
    props = [];
    rig = null;
    transforms = null;
    playback = null;
  }
  async function download(data, size) {
    if (data?.length !== size) return null;
    const response = await fetch(data.url, { cache: 'no-store' });
    const buffer = response.ok ? await response.arrayBuffer() : null;
    return buffer?.byteLength === size ? buffer : null;
  }
  async function build(target, descriptor, current) {
    const size = descriptor.vertex_count * VERTEX_BYTES + descriptor.index_count * 4;
    if (descriptor.layout !== LAYOUT) return null;
    const animated = !!descriptor.tracks;
    if (animated && descriptor.tracks.layout !== TRACK_LAYOUT) return null;
    const buffer = await download(descriptor.data, size);
    const trackBuffer = animated ? await download(descriptor.tracks, descriptor.tracks.length) : null;
    if (current !== generation || !buffer) return null;
    const tracks = animated && trackBuffer ? propTracks(descriptor, trackBuffer) : null;
    if (animated && (!tracks || descriptor.socket >= target.bones.length)) return null;
    const textured = materialFor(descriptor, reference, target.animation_session);
    let prop;
    try {
      prop = propMesh(descriptor, buffer, animated ? descriptor.bone_count : target.bones.length, textured);
    } catch (error) {
      textured?.dispose();
      textured?.material?.dispose();
      throw error;
    }
    if (animated) prop.animation = { socket: descriptor.socket, boneCount: descriptor.bone_count, tracks };
    // Follow the body's model transform (orientation, placement) every frame.
    prop.mesh.updateMatrixWorld = function updateMatrixWorld() {
      this.matrixWorld.copy(reference?.matrixWorld || IDENTITY);
    };
    return prop;
  }
  async function load(target, current) {
    const response = await loadProps(target.animation_session);
    if (current !== generation) return;
    if (response?.status !== 'ok' || !Array.isArray(response.props)) return;
    const loaded = [];
    try {
      for (const descriptor of response.props) {
        const prop = await build(target, descriptor, current);
        if (prop) loaded.push(prop);
        // An abandoned or failed load releases what it already built.
        if (current !== generation) throw new Error('The prop load was superseded.');
      }
    } catch (error) {
      loaded.forEach(disposeProp);
      throw error;
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
    // `current` is the {clip, time} the pose was sampled at; animated props sync with it.
    apply(next, current = null) {
      transforms = next;
      playback = current && typeof current.clip === 'string' && Number.isFinite(current.time) ? current : null;
      update();
    },
    hide() {
      transforms = null;
      playback = null;
      update();
    },
    dispose,
  };
}
