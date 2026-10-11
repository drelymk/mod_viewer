// First direct-skinning candidate: spatial segment evidence is averaged within
// exact authored sources, then transferred through their vertex weights.
import * as THREE from 'three';
import { buildHumanoidDriverFrames } from './humanoid-rig-binding.js';
import { isHumanoidMeshDisplayed } from './humanoid-geometry-fit.js';
import { readSkinningEntryArrays, skinningSourceDescriptorForEntry } from './skinning-runtime.js';
import { createWorkBudget } from './cooperative-scheduler.js';
import { applyWeightedNormalDeformationInto, applyWeightedTransformDeformationInto } from './weight-deformation.js';
import { resumeAnimatedMesh } from '../mesh/animation-runtime.js';

const MOVING_DRIVERS = ['left_upper_arm', 'left_lower_arm'];
// One compact spatial kernel is used for every segment and every model.
const SUPPORT_RADIUS_HEIGHT = 0.06;

function segmentDistanceSquared(point, start, end) {
  const direction = end.clone().sub(start);
  if (!direction.lengthSq()) return point.distanceToSquared(start);
  const t = THREE.MathUtils.clamp(point.clone().sub(start).dot(direction) / direction.lengthSq(), 0, 1);
  return point.distanceToSquared(start.clone().addScaledVector(direction, t));
}

function spatialWeights(point, drivers, radiusSquared) {
  const weights = [0, 0, 0];
  for (const driver of drivers.values()) {
    const near = Math.max(0, 1 - segmentDistanceSquared(point, driver.start, driver.end) / radiusSquared);
    const index = MOVING_DRIVERS.indexOf(driver.id) + 1;
    weights[index] += near * near;
  }
  const total = weights.reduce((sum, value) => sum + value, 0);
  return total > 0 ? weights.map((value) => value / total) : [1, 0, 0];
}

/** Build cached per-vertex driver weights; authored IDs never define the skeleton. */
export async function buildGeometryArmSkinning({
  rig,
  meshes,
  authoredByMesh = new Map(),
  useAuthored = true,
  isCurrent = () => true,
  invalidate = () => {},
} = {}) {
  const started = performance.now();
  const budget = createWorkBudget();
  const drivers = buildHumanoidDriverFrames(rig);
  if (!rig?.available || MOVING_DRIVERS.some((id) => !drivers.has(id))) throw new Error('No usable rest arm.');
  const radiusSquared = (rig.frame.height * SUPPORT_RADIUS_HEIGHT) ** 2;
  const displayed = meshes.filter(isHumanoidMeshDisplayed);
  displayed[0]?.updateWorldMatrix(true, false);
  const inverseModel = displayed[0]?.matrixWorld.clone().invert() || new THREE.Matrix4();
  const members = [];
  const distributions = new Map();
  const point = new THREE.Vector3();
  const edge = new THREE.Vector3();
  const cross = new THREE.Vector3();
  for (const mesh of displayed) {
    const rest = mesh.userData?.humanoidRestPositions || mesh.userData?.basePositions;
    if (!rest || rest.length !== mesh.geometry.attributes.position.array.length) continue;
    const entry = authoredByMesh.get(mesh);
    const sourceKey = skinningSourceDescriptorForEntry(entry)?.sourceKey;
    if (useAuthored && entry && !sourceKey) {
      throw new Error('Authored source identity is unavailable.');
    }
    const skin = entry ? readSkinningEntryArrays(mesh, entry, entry.buffer) : null;
    mesh.updateWorldMatrix(true, false);
    const toModel = mesh.userData.humanoidRestMatrix?.clone() || inverseModel.clone().multiply(mesh.matrixWorld);
    const vertexCount = rest.length / 3;
    const modelPositions = new Float32Array(rest.length);
    const geometryWeights = new Float32Array(vertexCount * 3);
    const area = new Float64Array(vertexCount);
    for (let vertex = 0; vertex < vertexCount; vertex += 1) {
      point.fromArray(rest, vertex * 3).applyMatrix4(toModel);
      point.toArray(modelPositions, vertex * 3);
      geometryWeights.set(spatialWeights(point, drivers, radiusSquared), vertex * 3);
      if (vertex % 256 === 0) {
        await budget.checkpoint();
        if (!isCurrent()) return null;
      }
    }
    const indices = mesh.geometry.index?.array;
    const count = indices?.length ?? vertexCount;
    const start = mesh.geometry.drawRange.start;
    const end = Math.min(count, start + mesh.geometry.drawRange.count);
    const displayedVertices = new Set();
    for (let i = start; i + 2 < end; i += 3) {
      const vertices = [0, 1, 2].map((corner) => (indices ? indices[i + corner] : i + corner));
      const a = point.fromArray(modelPositions, vertices[0] * 3);
      edge.fromArray(modelPositions, vertices[1] * 3).sub(a);
      cross.fromArray(modelPositions, vertices[2] * 3).sub(a);
      const mass = edge.cross(cross).length() / 6;
      for (const vertex of vertices) {
        displayedVertices.add(vertex);
        area[vertex] += mass;
      }
      if (i % 768 === 0) {
        await budget.checkpoint();
        if (!isCurrent()) return null;
      }
    }
    if (useAuthored && skin) {
      let source = distributions.get(sourceKey);
      if (!source) distributions.set(sourceKey, (source = new Map()));
      for (const vertex of displayedVertices) {
        for (let slot = 0; slot < skin.influenceCount; slot += 1) {
          const offset = vertex * skin.influenceCount + slot;
          const weight = skin.weights[offset];
          if (!Number.isFinite(weight) || weight <= 0) continue;
          const bone = skin.indices[offset];
          let distribution = source.get(bone);
          if (!distribution) source.set(bone, (distribution = [0, 0, 0, 0]));
          const mass = area[vertex] * weight;
          distribution[3] += mass;
          for (let driver = 0; driver < 3; driver += 1) {
            distribution[driver] += mass * geometryWeights[vertex * 3 + driver];
          }
        }
        if (vertex % 256 === 0) {
          await budget.checkpoint();
          if (!isCurrent()) return null;
        }
      }
    }
    members.push({ mesh, rest: new Float32Array(rest), toModel, skin, sourceKey, geometryWeights, displayedVertices });
  }
  for (const source of distributions.values()) {
    for (const distribution of source.values()) {
      if (distribution[3] > 0) for (let i = 0; i < 3; i += 1) distribution[i] /= distribution[3];
    }
  }
  let activeVertexCount = 0;
  for (const member of members) {
    const { mesh, skin, sourceKey, geometryWeights, displayedVertices } = member;
    const vertexCount = member.rest.length / 3;
    const weights = new Float32Array(vertexCount * 3);
    const indices = new Uint32Array(vertexCount * 3);
    const active = [];
    for (const vertex of displayedVertices) {
      const values = useAuthored && skin ? [0, 0, 0] : [...geometryWeights.subarray(vertex * 3, vertex * 3 + 3)];
      if (useAuthored && skin) {
        for (let slot = 0; slot < skin.influenceCount; slot += 1) {
          const offset = vertex * skin.influenceCount + slot;
          const authoredWeight = skin.weights[offset];
          const distribution = distributions.get(sourceKey).get(skin.indices[offset]);
          if (!Number.isFinite(authoredWeight) || authoredWeight <= 0 || !distribution?.[3]) continue;
          for (let driver = 0; driver < 3; driver += 1) values[driver] += authoredWeight * distribution[driver];
        }
      }
      const total = values.reduce((sum, value) => sum + value, 0);
      weights.set(total > 0 ? values.map((value) => value / total) : [1, 0, 0], vertex * 3);
      indices.set([0, 1, 2], vertex * 3);
      if (weights[vertex * 3 + 1] + weights[vertex * 3 + 2] > 0) active.push(vertex);
      if (vertex % 256 === 0) {
        await budget.checkpoint();
        if (!isCurrent()) return null;
      }
    }
    member.weights = weights;
    member.indices = indices;
    member.active = Uint32Array.from(active);
    activeVertexCount += active.length;
    const normal = mesh.geometry.attributes.normal;
    member.normals =
      normal &&
      new Float32Array(
        mesh.userData.baseNormals?.length === normal.array.length ? mesh.userData.baseNormals : normal.array,
      );
    if (member.normals) {
      member.modelNormals = new Float32Array(member.normals.length);
      member.posedNormals = new Float32Array(member.normals.length);
      member.normalToLocal = new THREE.Matrix3().getNormalMatrix(member.toModel.clone().invert());
      const normalToModel = new THREE.Matrix3().getNormalMatrix(member.toModel);
      for (const vertex of member.active) {
        point
          .fromArray(member.normals, vertex * 3)
          .applyMatrix3(normalToModel)
          .normalize()
          .toArray(member.modelNormals, vertex * 3);
      }
    }
    member.bounds = { box: mesh.geometry.boundingBox?.clone(), sphere: mesh.geometry.boundingSphere?.clone() };
    member.frustumCulled = mesh.frustumCulled;
    member.animationSuspended = mesh.userData.animationSuspended;
  }
  if (!isCurrent()) return null;
  let owning = false;
  let boundsTimer = null;
  const diagnostics = {
    bindingMs: performance.now() - started,
    activeVertexCount,
    sourceCount: distributions.size,
    ...budget.getStats(),
  };
  const restore = (member) => {
    const { mesh } = member;
    mesh.geometry.attributes.position.array.set(member.rest);
    mesh.geometry.attributes.position.needsUpdate = true;
    if (member.normals) {
      mesh.geometry.attributes.normal.array.set(member.normals);
      mesh.geometry.attributes.normal.needsUpdate = true;
    }
    mesh.geometry.boundingBox = member.bounds.box?.clone() || null;
    mesh.geometry.boundingSphere = member.bounds.sphere?.clone() || null;
    mesh.frustumCulled = member.frustumCulled;
  };
  function reset({ preserveMesh = null, release = false } = {}) {
    clearTimeout(boundsTimer);
    if (!owning) return;
    for (const member of members.filter((item) => item.active.length)) {
      if (member.mesh !== preserveMesh) restore(member);
      if (release) {
        if (member.animationSuspended === undefined) delete member.mesh.userData.animationSuspended;
        else member.mesh.userData.animationSuspended = member.animationSuspended;
        if (!member.animationSuspended && member.mesh !== preserveMesh) resumeAnimatedMesh(member.mesh);
      }
    }
    if (release) owning = false;
    invalidate();
  }
  return {
    members,
    diagnostics,
    reset,
    dispose: (options = {}) => reset({ ...options, release: true }),
    apply(posedControls = {}) {
      const started = performance.now();
      const atRest = ['leftShoulder', 'leftElbow', 'leftHand'].every((key) => {
        const value = posedControls[key]?.position || posedControls[key];
        const position = value?.isVector3 ? value.toArray() : value;
        return !position || position.every((value, i) => value === rig.controls[key].position[i]);
      });
      if (atRest) {
        reset();
        return;
      }
      const posed = buildHumanoidDriverFrames(rig, posedControls);
      for (const member of members.filter((item) => item.active.length)) {
        const { mesh, toModel } = member;
        if (!owning) restore(member);
        mesh.userData.animationSuspended = true;
        mesh.frustumCulled = false;
        const transforms = new Map([[0, new THREE.Matrix4()]]);
        const rotations = new Map([[0, new THREE.Quaternion()]]);
        MOVING_DRIVERS.forEach((id, index) => {
          const delta = posed.get(id).matrix.clone().multiply(drivers.get(id).matrix.clone().invert());
          const local = toModel.clone().invert().multiply(delta).multiply(toModel);
          transforms.set(index + 1, local);
          rotations.set(index + 1, new THREE.Quaternion().setFromRotationMatrix(delta).normalize());
        });
        applyWeightedTransformDeformationInto(
          mesh.geometry.attributes.position.array,
          member.rest,
          member.indices,
          member.weights,
          3,
          transforms,
          member.active,
        );
        mesh.geometry.attributes.position.needsUpdate = true;
        if (member.normals) {
          applyWeightedNormalDeformationInto(
            member.posedNormals,
            member.modelNormals,
            member.indices,
            member.weights,
            3,
            rotations,
            member.active,
          );
          for (const vertex of member.active) {
            point
              .fromArray(member.posedNormals, vertex * 3)
              .applyMatrix3(member.normalToLocal)
              .normalize()
              .toArray(mesh.geometry.attributes.normal.array, vertex * 3);
          }
          mesh.geometry.attributes.normal.needsUpdate = true;
        }
      }
      owning = true;
      clearTimeout(boundsTimer);
      boundsTimer = setTimeout(() => {
        for (const member of members.filter((item) => item.active.length)) {
          member.mesh.geometry.computeBoundingBox();
          member.mesh.geometry.computeBoundingSphere();
        }
      }, 100);
      diagnostics.lastPoseMs = performance.now() - started;
      invalidate();
    },
  };
}
