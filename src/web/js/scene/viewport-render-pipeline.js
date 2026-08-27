// WebGPU-native viewport post-processing and character-only ambient occlusion.

import * as THREE from 'three/webgpu';
import {
  builtinAOContext,
  float,
  mix,
  mrt,
  normalView,
  packNormalToRGB,
  pass,
  sample,
  screenUV,
  uniform,
  unpackRGBToNormal,
} from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { computeModelBounds } from './model-bounds.js';
import { CHARACTER_AO_LAYER } from './viewer-layers.js';

const AO_DEFAULT_STRENGTH = 0.22;
const AO_RESOLUTION_SCALE = 0.5;
const AO_SAMPLES = 8;
const AO_RADIUS_FACTOR = 0.15;
const MIN_MODEL_SIZE = 0.001;
const MIN_AO_RADIUS = 0.001;
let nextPipelineId = 0;

function finitePositive(value) {
  return Number.isFinite(value) && value > 0;
}

function readUniformValue(node, fallback = 0) {
  return Number.isFinite(node?.value) ? node.value : fallback;
}

function readResolution(node) {
  const value = node?.value;
  if (!value || !Number.isFinite(value.x) || !Number.isFinite(value.y)) {
    return null;
  }
  return [value.x, value.y];
}

/** Create the one viewport render pipeline used by the scene renderer. */
export function createViewportRenderPipeline({ renderer, scene, camera }) {
  const aoLayers = new THREE.Layers();
  aoLayers.set(CHARACTER_AO_LAYER);

  const renderPipeline = new THREE.RenderPipeline(renderer);
  const pipelineId = ++nextPipelineId;
  // RenderPipeline evaluates scene passes from inside the fullscreen quad.
  // Keep the beauty pass on the camera owned by ArcballControls. Only the
  // character pre-pass needs an isolated camera because it changes layers.
  const prePassCamera = camera.clone();

  function syncCameraCoordinateSystem() {
    if (camera.coordinateSystem !== renderer.coordinateSystem) {
      camera.coordinateSystem = renderer.coordinateSystem;
      camera.updateProjectionMatrix();
    }
  }

  function syncPrePassCamera() {
    syncCameraCoordinateSystem();
    prePassCamera.copy(camera, false);
  }

  // The pre-pass renders only character meshes. Its packed normal attachment
  // is deliberately 8-bit; depth remains the pass's depth texture.
  const prePass = pass(scene, prePassCamera, { samples: 1 });
  prePass.setResolutionScale(AO_RESOLUTION_SCALE);
  prePass.name = 'Character AO pre-pass';
  prePass.transparent = false;
  prePass.setLayers(aoLayers);
  prePass.setMRT(mrt({ output: packNormalToRGB(normalView) }));
  const normalTexture = prePass.getTexture('output');
  normalTexture.type = THREE.UnsignedByteType;

  const prePassNormal = sample(uv =>
    unpackRGBToNormal(prePass.getTextureNode().sample(uv)));
  const prePassDepth = prePass.getTextureNode('depth');
  const aoPass = ao(prePassDepth, prePassNormal, camera);
  aoPass.resolutionScale = AO_RESOLUTION_SCALE;
  aoPass.samples.value = AO_SAMPLES;
  aoPass.useTemporalFiltering = false;

  const aoStrengthNode = uniform(AO_DEFAULT_STRENGTH);
  const aoSample = aoPass.getTextureNode().sample(screenUV).r;
  const effectiveAO = mix(float(1), aoSample, aoStrengthNode);

  // The beauty pass retains the whole presentation scene. AO enters through
  // the lighting context so direct light, shadows, rim and debug outputs are
  // not multiplied as a final screen-space color operation.
  const scenePass = pass(scene, camera);
  scenePass.name = 'Viewport beauty pass';
  scenePass.contextNode = builtinAOContext(effectiveAO);
  renderPipeline.outputNode = scenePass;

  let meshes = [];
  let modelSizeDirty = true;
  let modelSize = MIN_MODEL_SIZE;
  let enabled = false;
  let configuredStrength = AO_DEFAULT_STRENGTH;
  let suppressedByWireframe = false;
  let renderCount = 0;
  let directRenderCount = 0;
  let aoRenderCount = 0;

  function effectiveStrength() {
    return enabled && !suppressedByWireframe ? configuredStrength : 0;
  }

  function applyStrength() {
    aoStrengthNode.value = effectiveStrength();
  }

  function updateModelSize() {
    if (!modelSizeDirty) return;
    const bounds = computeModelBounds(meshes);
    if (bounds.isEmpty()) {
      modelSize = MIN_MODEL_SIZE;
    } else {
      const diagonal = bounds.getSize(new THREE.Vector3()).length();
      modelSize = finitePositive(diagonal) ? Math.max(diagonal, MIN_MODEL_SIZE)
        : MIN_MODEL_SIZE;
    }
    aoPass.radius.value = Math.max(modelSize * AO_RADIUS_FACTOR, MIN_AO_RADIUS);
    modelSizeDirty = false;
  }

  function invalidateGeometry() {
    modelSizeDirty = true;
  }

  function setMeshes(nextMeshes = []) {
    meshes = [...new Set(nextMeshes.filter(Boolean))];
    invalidateGeometry();
  }

  function adoptMeshes(nextMeshes = []) {
    const known = new Set(meshes);
    const added = nextMeshes.filter(mesh => mesh && !known.has(mesh));
    if (added.length) {
      meshes.push(...added);
      invalidateGeometry();
    }
    return added;
  }

  function forgetMeshes(nextMeshes = []) {
    const removed = new Set(nextMeshes);
    const before = meshes.length;
    meshes = meshes.filter(mesh => !removed.has(mesh));
    if (meshes.length !== before) invalidateGeometry();
  }

  function reset() {
    meshes = [];
    modelSize = MIN_MODEL_SIZE;
    modelSizeDirty = false;
    aoPass.radius.value = MIN_AO_RADIUS;
  }

  function setAmbientOcclusionEnabled(value) {
    enabled = !!value;
    applyStrength();
    return enabled;
  }

  function setAmbientOcclusionStrength(value) {
    if (!Number.isFinite(value)) return false;
    configuredStrength = THREE.MathUtils.clamp(value, 0, 1);
    applyStrength();
    return true;
  }

  function setAmbientOcclusionSuppressedByWireframe(value) {
    suppressedByWireframe = !!value;
    applyStrength();
  }

  function render() {
    const useAmbientOcclusion = effectiveStrength() > 0;
    if (useAmbientOcclusion) {
      updateModelSize();
      syncPrePassCamera();
    } else {
      syncCameraCoordinateSystem();
    }
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    try {
      if (useAmbientOcclusion) {
        renderPipeline.render();
        aoRenderCount += 1;
      } else {
        renderer.render(scene, camera);
        directRenderCount += 1;
      }
    } finally {
      // RenderPipeline temporarily switches to working color space while its
      // internal passes execute. Keep the renderer contract visible to the
      // rest of the viewer after the pipeline returns as well.
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.NoToneMapping;
    }
    renderCount += 1;
  }

  function getDebugState() {
    return {
      enabled,
      strength: configuredStrength,
      effectiveStrength: effectiveStrength(),
      suppressedByWireframe,
      pipelineId,
      resolutionScale: aoPass.resolutionScale,
      resolution: readResolution(aoPass.resolution),
      samples: readUniformValue(aoPass.samples),
      radius: readUniformValue(aoPass.radius),
      modelSize,
      renderCount,
      directRenderCount,
      aoRenderCount,
      pipelineNeedsUpdate: renderPipeline.needsUpdate,
      hasRenderPipeline: !!renderPipeline,
      hasPrePass: !!prePass,
      hasGTAO: !!aoPass,
      temporalFiltering: aoPass.useTemporalFiltering === true,
      prePassLayerMask: prePass.getLayers()?.mask ?? 0,
      prePassSamples: prePass.renderTarget?.samples ?? 0,
      prePassResolutionScale: prePass.getResolutionScale(),
      beautyCameraIsSource: scenePass.camera === camera,
      prePassCameraIsClone: prePass.camera !== camera,
      cameraCoordinateSystem: camera.coordinateSystem,
      rendererCoordinateSystem: renderer.coordinateSystem,
      characterAOLayer: CHARACTER_AO_LAYER,
    };
  }

  function dispose() {
    aoPass.dispose?.();
    renderPipeline.dispose?.();
    prePass.renderTarget?.dispose?.();
    scenePass.renderTarget?.dispose?.();
  }

  applyStrength();

  return {
    render,
    setMeshes,
    adoptMeshes,
    forgetMeshes,
    invalidateGeometry,
    reset,
    isAmbientOcclusionEnabled: () => enabled,
    setAmbientOcclusionEnabled,
    setAmbientOcclusionStrength,
    setAmbientOcclusionSuppressedByWireframe,
    getDebugState,
    dispose,
  };
}
