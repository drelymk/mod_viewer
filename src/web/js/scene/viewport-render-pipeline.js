// WebGPU-native viewport post-processing for character AO and emission bloom.

import * as THREE from 'three/webgpu';
import { builtinAOContext, emissive, float, mix, mrt, normalView, output, packNormalToRGB, pass, sample, screenUV, uniform, unpackRGBToNormal } from 'three/tsl';
import { ao } from 'three/addons/tsl/display/GTAONode.js';
import { bloom } from 'three/addons/tsl/display/BloomNode.js';
import { computeModelBounds } from './model-bounds.js';
import { CHARACTER_AO_LAYER } from './viewer-layers.js';

const AO_RESOLUTION_SCALE = 0.5;
const AO_SAMPLES = 8;
const AO_RADIUS_FACTOR = 0.005;
const AO_THICKNESS_RADIUS_RATIO = 4;
const BLOOM_STRENGTH = 0.35;
const BLOOM_RADIUS = 0.2;
const BLOOM_THRESHOLD = 0;
const BLOOM_RESOLUTION_SCALE = 0.5;
const MIN_MODEL_SIZE = 0.001;
const MIN_AO_RADIUS = MIN_MODEL_SIZE * AO_RADIUS_FACTOR;
let nextPipelineId = 0;

function finitePositive(value) { return Number.isFinite(value) && value > 0; }
function readUniformValue(node, fallback = 0) {
  return Number.isFinite(node?.value) ? node.value : fallback;
}
function readResolution(node) {
  const value = node?.value;
  return value && Number.isFinite(value.x) && Number.isFinite(value.y)
    ? [value.x, value.y] : null;
}

/** Create the one viewport render pipeline used by the scene renderer. */
export function createViewportRenderPipeline({ renderer, scene, camera }) {
  const aoLayers = new THREE.Layers();
  aoLayers.set(CHARACTER_AO_LAYER);
  const renderPipeline = new THREE.RenderPipeline(renderer);
  const pipelineId = ++nextPipelineId;
  const prePassCamera = camera.clone();
  const syncCameraCoordinateSystem = () => {
    if (camera.coordinateSystem !== renderer.coordinateSystem) {
      camera.coordinateSystem = renderer.coordinateSystem;
      camera.updateProjectionMatrix();
    }
  };
  const syncPrePassCamera = () => {
    syncCameraCoordinateSystem();
    prePassCamera.copy(camera, false);
  };

  const prePass = pass(scene, prePassCamera, { samples: 1 });
  prePass.setResolutionScale(AO_RESOLUTION_SCALE);
  prePass.name = 'Character AO pre-pass';
  prePass.transparent = false;
  prePass.setLayers(aoLayers);
  prePass.setMRT(mrt({ output: packNormalToRGB(normalView) }));
  prePass.getTexture('output').type = THREE.UnsignedByteType;
  const prePassNormal = sample(uv =>
    unpackRGBToNormal(prePass.getTextureNode().sample(uv)));
  const aoPass = ao(prePass.getTextureNode('depth'), prePassNormal, camera);
  aoPass.resolutionScale = AO_RESOLUTION_SCALE;
  aoPass.samples.value = AO_SAMPLES;
  aoPass.useTemporalFiltering = false;
  const aoStrengthNode = uniform(0);
  const effectiveAO = mix(
    float(1), aoPass.getTextureNode().sample(screenUV).r, aoStrengthNode);

  // Each output graph has only the work it needs: bloom-only deliberately has
  // no AO context, so it cannot schedule GTAO's depth/normal pre-pass.
  const aoScenePass = pass(scene, camera);
  aoScenePass.name = 'Viewport AO beauty pass';
  aoScenePass.contextNode = builtinAOContext(effectiveAO);
  const bloomScenePass = pass(scene, camera);
  bloomScenePass.name = 'Viewport emission beauty pass';
  bloomScenePass.setMRT(mrt({ output, emissive }));
  const bloomPass = bloom(bloomScenePass.getTextureNode('emissive'),
    BLOOM_STRENGTH, BLOOM_RADIUS, BLOOM_THRESHOLD);
  bloomPass.setResolutionScale(BLOOM_RESOLUTION_SCALE);
  const bloomOutput = bloomScenePass.getTextureNode('output').add(bloomPass);
  const aoBloomScenePass = pass(scene, camera);
  aoBloomScenePass.name = 'Viewport AO emission beauty pass';
  aoBloomScenePass.contextNode = builtinAOContext(effectiveAO);
  aoBloomScenePass.setMRT(mrt({ output, emissive }));
  const aoBloomPass = bloom(aoBloomScenePass.getTextureNode('emissive'),
    BLOOM_STRENGTH, BLOOM_RADIUS, BLOOM_THRESHOLD);
  aoBloomPass.setResolutionScale(BLOOM_RESOLUTION_SCALE);
  const aoBloomOutput = aoBloomScenePass.getTextureNode('output').add(aoBloomPass);

  let meshes = [];
  let modelSizeDirty = true;
  let modelSize = MIN_MODEL_SIZE;
  let configuredStrength = 0;
  let bloomEnabled = false;
  let suppressedByWireframe = false;
  let bloomSuppressedByWireframe = false;
  let bloomSuppressedByDebug = false;
  let activeRenderMode = 'direct';
  let renderCount = 0;
  let directRenderCount = 0;
  let aoRenderCount = 0;
  let aoOnlyRenderCount = 0;
  let bloomOnlyRenderCount = 0;
  let aoBloomRenderCount = 0;

  const isAmbientOcclusionEnabled = () => configuredStrength > 0;
  const shouldRenderAO = () => isAmbientOcclusionEnabled() && !suppressedByWireframe;
  const shouldRenderBloom = () => bloomEnabled && !bloomSuppressedByWireframe
    && !bloomSuppressedByDebug;
  const renderMode = () => shouldRenderAO()
    ? (shouldRenderBloom() ? 'ao-bloom' : 'ao')
    : (shouldRenderBloom() ? 'bloom' : 'direct');
  const effectiveStrength = () => shouldRenderAO() ? configuredStrength : 0;
  const applyStrength = () => { aoStrengthNode.value = effectiveStrength(); };
  function configureRenderGraph() {
    const next = renderMode();
    if (next === activeRenderMode) return false;
    activeRenderMode = next;
    if (next === 'ao') renderPipeline.outputNode = aoScenePass;
    else if (next === 'bloom') renderPipeline.outputNode = bloomOutput;
    else if (next === 'ao-bloom') renderPipeline.outputNode = aoBloomOutput;
    if (next !== 'direct') renderPipeline.needsUpdate = true;
    return true;
  }
  function updateModelSize() {
    if (!modelSizeDirty) return;
    const bounds = computeModelBounds(meshes);
    if (bounds.isEmpty()) modelSize = MIN_MODEL_SIZE;
    else {
      const diagonal = bounds.getSize(new THREE.Vector3()).length();
      modelSize = finitePositive(diagonal) ? Math.max(diagonal, MIN_MODEL_SIZE)
        : MIN_MODEL_SIZE;
    }
    modelSizeDirty = false;
  }
  function updateSpatialParameters() {
    if (!isAmbientOcclusionEnabled()) {
      aoPass.radius.value = MIN_AO_RADIUS;
      aoPass.thickness.value = MIN_AO_RADIUS * AO_THICKNESS_RADIUS_RATIO;
      return;
    }
    updateModelSize();
    aoPass.radius.value = modelSize * AO_RADIUS_FACTOR;
    aoPass.thickness.value = aoPass.radius.value * AO_THICKNESS_RADIUS_RATIO;
  }
  const invalidateGeometry = () => { modelSizeDirty = true; };
  function setMeshes(nextMeshes = []) {
    meshes = [...new Set(nextMeshes.filter(Boolean))];
    invalidateGeometry();
  }
  function adoptMeshes(nextMeshes = []) {
    const known = new Set(meshes);
    const added = nextMeshes.filter(mesh => mesh && !known.has(mesh));
    if (added.length) { meshes.push(...added); invalidateGeometry(); }
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
    aoPass.thickness.value = MIN_AO_RADIUS * AO_THICKNESS_RADIUS_RATIO;
  }
  function setAmbientOcclusionStrength(value) {
    if (!Number.isFinite(value)) return false;
    const next = THREE.MathUtils.clamp(value, 0, 1);
    const changed = next !== configuredStrength;
    configuredStrength = next;
    updateSpatialParameters();
    applyStrength();
    configureRenderGraph();
    return changed;
  }
  function setAmbientOcclusionSuppressedByWireframe(value) {
    suppressedByWireframe = !!value;
    applyStrength();
    configureRenderGraph();
  }
  function setBloomEnabled(value) {
    const next = value === true;
    const changed = next !== bloomEnabled;
    bloomEnabled = next;
    configureRenderGraph();
    return changed;
  }
  function setBloomSuppressedByWireframe(value) {
    bloomSuppressedByWireframe = !!value;
    configureRenderGraph();
  }
  function setBloomSuppressedByDebug(value) {
    bloomSuppressedByDebug = !!value;
    configureRenderGraph();
  }
  function render() {
    const mode = renderMode();
    if (mode === 'ao' || mode === 'ao-bloom') {
      updateSpatialParameters();
      syncPrePassCamera();
    } else syncCameraCoordinateSystem();
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.NoToneMapping;
    try {
      if (mode === 'direct') {
        renderer.render(scene, camera);
        directRenderCount += 1;
      } else {
        renderPipeline.render();
        if (mode === 'ao') { aoRenderCount += 1; aoOnlyRenderCount += 1; }
        else if (mode === 'bloom') bloomOnlyRenderCount += 1;
        else { aoRenderCount += 1; aoBloomRenderCount += 1; }
      }
    } finally {
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.NoToneMapping;
    }
    renderCount += 1;
  }
  function getDebugState() {
    return {
      enabled: isAmbientOcclusionEnabled(), radiusFactor: AO_RADIUS_FACTOR,
      strength: configuredStrength, effectiveStrength: effectiveStrength(),
      suppressedByWireframe, bloomEnabled, bloomEffective: shouldRenderBloom(),
      bloomSuppressedByWireframe, bloomSuppressedByDebug,
      bloomStrength: BLOOM_STRENGTH, bloomRadius: BLOOM_RADIUS,
      bloomThreshold: BLOOM_THRESHOLD, bloomResolutionScale: bloomPass.getResolutionScale(),
      activeRenderMode, pipelineId, resolutionScale: aoPass.resolutionScale,
      resolution: readResolution(aoPass.resolution), samples: readUniformValue(aoPass.samples),
      radius: readUniformValue(aoPass.radius), thickness: readUniformValue(aoPass.thickness),
      modelSize, renderCount, directRenderCount, aoRenderCount, aoOnlyRenderCount,
      bloomOnlyRenderCount, aoBloomRenderCount, pipelineNeedsUpdate: renderPipeline.needsUpdate,
      hasRenderPipeline: !!renderPipeline, hasPrePass: !!prePass, hasGTAO: !!aoPass,
      hasBloom: !!bloomPass && !!aoBloomPass,
      temporalFiltering: aoPass.useTemporalFiltering === true,
      prePassLayerMask: prePass.getLayers()?.mask ?? 0,
      prePassSamples: prePass.renderTarget?.samples ?? 0,
      prePassResolutionScale: prePass.getResolutionScale(),
      beautyCameraIsSource: aoScenePass.camera === camera
        && bloomScenePass.camera === camera && aoBloomScenePass.camera === camera,
      prePassCameraIsClone: prePass.camera !== camera,
      cameraCoordinateSystem: camera.coordinateSystem,
      rendererCoordinateSystem: renderer.coordinateSystem,
      characterAOLayer: CHARACTER_AO_LAYER,
    };
  }
  function dispose() {
    aoPass.dispose?.(); bloomPass.dispose?.(); aoBloomPass.dispose?.();
    renderPipeline.dispose?.();
    prePass.renderTarget?.dispose?.(); aoScenePass.renderTarget?.dispose?.();
    bloomScenePass.renderTarget?.dispose?.();
    aoBloomScenePass.renderTarget?.dispose?.();
  }
  updateSpatialParameters();
  applyStrength();
  return {
    render, setMeshes, adoptMeshes, forgetMeshes, invalidateGeometry, reset,
    getAmbientOcclusionStrength: () => configuredStrength, isAmbientOcclusionEnabled,
    setAmbientOcclusionStrength, setAmbientOcclusionSuppressedByWireframe,
    getBloomEnabled: () => bloomEnabled, setBloomEnabled,
    setBloomSuppressedByWireframe, setBloomSuppressedByDebug, getDebugState, dispose,
  };
}
