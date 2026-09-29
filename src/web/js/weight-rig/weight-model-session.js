// Owns model-wide Weight loading, state and selection. SkinningRuntime
// supplies per-mesh installation and deformation mechanics.

import * as THREE from 'three';
import { createWeightPickController } from '../scene/weight-pick-controller.js';
import { computeModelBounds } from '../scene/model-bounds.js';
import { sampleSkinningAtIntersection } from './weight-selection.js';
import { aggregateModelWeightBoneStats } from './weight-runtime.js';
import { weightRigStatus } from './weight-rig-status.js';
import { createWorkBudget } from './cooperative-scheduler.js';

function clockNow() {
  return typeof globalThis.performance?.now === 'function' ? globalThis.performance.now() : Date.now();
}

export function createWeightPickingSession({
  modelWeightState,
  states,
  knownMeshes,
  canvas,
  camera,
  controls,
  notifyChanged,
  requestRender,
} = {}) {
  function getMeshes() {
    return [...knownMeshes].filter((mesh) => mesh?.userData?.assetFill !== true);
  }

  function pickRadiusWorld() {
    const box = computeModelBounds(getMeshes());
    if (box.isEmpty()) return 0.0001;
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const radius = Number(sphere.radius);
    return Number.isFinite(radius) && radius > 0 ? Math.max(radius * 0.02, 0.000001) : 0.0001;
  }

  function sampleAtIntersection(intersection) {
    const mesh = intersection?.object;
    const state = states.get(mesh);
    if (!state?.loaded || !state.skinningSourceKey) return null;
    return sampleSkinningAtIntersection(intersection, mesh, state, { radius: pickRadiusWorld() });
  }

  function clearPickedPoint({ notify = true } = {}) {
    if (
      !modelWeightState.pickedPoint &&
      modelWeightState.pickerViewMode === 'all' &&
      !modelWeightState.pickStatus &&
      !picker.isEnabled()
    )
      return false;
    if (picker.isEnabled()) picker.cancel();
    modelWeightState.pickedPoint = null;
    modelWeightState.pickerViewMode = 'all';
    modelWeightState.pickStatus = '';
    modelWeightState.picking = false;
    if (notify) notifyChanged();
    return true;
  }

  function handlePickedIntersection(intersection) {
    if (!intersection) {
      modelWeightState.pickStatus = weightRigStatus('weightRig.status.noSurfacePicked');
      notifyChanged();
      return null;
    }
    const sampled = sampleAtIntersection(intersection);
    if (!sampled) {
      modelWeightState.pickStatus = weightRigStatus('weightRig.status.noWeightsAtPoint');
      notifyChanged();
      return null;
    }
    const mesh = intersection.object;
    const source = modelWeightState.sourceDescriptors.get(sampled.sourceKey);
    const radiusWorld = pickRadiusWorld();
    const pickedPoint = {
      point: sampled.point,
      sourceKey: sampled.sourceKey,
      sourceFile: source?.sourceFile || sampled.sourceFile,
      boneIdOffset: source?.boneIdOffset ?? sampled.boneIdOffset,
      meshKey: mesh.userData?.semanticKey || null,
      radiusWorld,
      influences: sampled.influences,
    };
    modelWeightState.pickedPoint = pickedPoint;
    modelWeightState.pickerViewMode = 'picked';
    modelWeightState.pickStatus = '';
    notifyChanged();
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('mod-viewer-weight-point-picked', {
          detail: { sourceKey: sampled.sourceKey },
        }),
      );
    }
    return pickedPoint;
  }

  const picker = createWeightPickController({
    canvas,
    camera,
    controls,
    getMeshes,
    onPick: handlePickedIntersection,
    onStateChanged: (picking, { cancelled } = {}) => {
      modelWeightState.picking = picking;
      if (picking || cancelled) notifyChanged();
    },
    requestRender,
  });

  return {
    sampleAtIntersection,
    clearPickedPoint,
    begin() {
      if (!modelWeightState.loaded) {
        modelWeightState.pickStatus = weightRigStatus('weightRig.status.loadWeightsBeforePicking');
        notifyChanged();
        return false;
      }
      return picker.begin();
    },
    cancel: () => picker.cancel(),
    setViewMode(mode) {
      if (mode !== 'all' && mode !== 'picked') {
        return modelWeightState.pickerViewMode;
      }
      if (mode === 'picked' && !modelWeightState.pickedPoint) {
        return modelWeightState.pickerViewMode;
      }
      modelWeightState.pickerViewMode = mode;
      modelWeightState.pickStatus = '';
      notifyChanged();
      return mode;
    },
    reset() {
      picker.cancel();
    },
  };
}

export function createWeightModelSession({
  modelWeightState,
  states,
  stateFor,
  knownMeshes,
  modelWeightSnapshot,
  selectionMapFromEntries,
  sourceSelectionEntries,
  refreshSelectedWeightMask,
  updateModelWeightHeatmap,
  installSkinningEntry,
  syncPhysicsToSelection,
  serializeBoneSelection,
  eligibleSkinningMesh,
  notifyChanged,
  requestRender,
  getGeneration,
} = {}) {
  let selectionSavePromise = null;
  let selectionRestoreOperation = null;

  function setModelWeightLoadError(error) {
    modelWeightState.error = error instanceof Error ? error.message : String(error);
    modelWeightState.loaded = false;
    modelWeightState.noWeights = false;
  }

  async function restoreSelectedWeightMasks(generation, isCurrent = () => true) {
    const budget = createWorkBudget();
    for (const mesh of knownMeshes) {
      if (generation !== getGeneration() || !isCurrent()) return false;
      const state = states.get(mesh);
      if (state?.loaded) {
        refreshSelectedWeightMask(mesh, state, modelWeightState.selectedBonesBySource.get(state.skinningSourceKey));
      }
      await budget.checkpoint();
    }
    if (generation !== getGeneration() || !isCurrent()) return false;
    updateModelWeightHeatmap(null, modelWeightState.heatmapEnabled);
    notifyChanged();
    return true;
  }

  function loadModelWeights() {
    if (modelWeightState.loaded) return Promise.resolve(modelWeightSnapshot());
    if (modelWeightState.promise) return modelWeightState.promise;
    const generation = getGeneration();
    const loadStartedAt = clockNow();
    const performance = {};
    modelWeightState.performance = performance;
    const meshes = [...knownMeshes].filter(eligibleSkinningMesh);
    if (!meshes.length) {
      modelWeightState.loaded = true;
      modelWeightState.noWeights = true;
      modelWeightState.savedSelectionApplied = true;
      modelWeightState.savedSelectionMasksRestored = true;
      refreshModelWeightSummary({ refreshStats: true });
      notifyChanged();
      return Promise.resolve(modelWeightSnapshot());
    }
    const folderPath = meshes[0].userData.modPath;
    modelWeightState.loading = true;
    modelWeightState.error = null;
    modelWeightState.noWeights = false;
    notifyChanged();
    modelWeightState.promise = (async () => {
      const api = window.pywebview?.api?.get_model_skinning_preview;
      if (typeof api !== 'function') {
        throw new Error('Model skin-weight preview is unavailable.');
      }
      const preview = await api(folderPath);
      if (generation !== getGeneration()) return modelWeightSnapshot();
      modelWeightState.savedBonesBySource = selectionMapFromEntries(preview?.saved_bones);
      const bufferResponse = preview?.data?.url ? await fetch(preview.data.url, { cache: 'no-store' }) : null;
      if (bufferResponse && !bufferResponse.ok) {
        throw new Error(`Skin data download failed (${bufferResponse.status}).`);
      }
      const buffer = bufferResponse ? await bufferResponse.arrayBuffer() : null;
      if (buffer && buffer.byteLength !== Number(preview.data.length)) {
        throw new Error('Skin data download was incomplete.');
      }
      performance.fetchMs = clockNow() - loadStartedAt;
      const installStartedAt = clockNow();
      const installBudget = createWorkBudget();
      for (const mesh of meshes) {
        if (generation !== getGeneration() || !knownMeshes.has(mesh)) return modelWeightSnapshot();
        const state = stateFor(mesh);
        const entry = preview?.meshes?.[mesh.userData.semanticKey];
        if (!entry || entry.status !== 'ok') {
          state.error = entry?.error || 'No usable skin weights were returned.';
          state.loaded = false;
          continue;
        }
        try {
          const installed = installSkinningEntry(mesh, entry, buffer);
          modelWeightState.sourceDescriptors.set(installed.source.sourceKey, installed.source);
        } catch (error) {
          state.error = error instanceof Error ? error.message : String(error);
          state.loaded = false;
        }
        await installBudget.checkpoint();
      }
      if (generation !== getGeneration()) return modelWeightSnapshot();
      modelWeightState.loaded = true;
      refreshModelWeightSummary({ refreshStats: true });
      performance.basicInstallMs = clockNow() - installStartedAt;
      performance.weightReadyMs = clockNow() - loadStartedAt;
      Object.assign(performance, installBudget.getStats());
      // Apply saved IDs before making the newly loaded Weight controls
      // interactive. Derived masks remain part of feature activation.
      if (!modelWeightState.savedSelectionApplied) {
        const savedSelection = selectionMapFromEntries(modelWeightState.savedBonesBySource);
        retainAvailableBones(savedSelection);
        modelWeightState.selectedBonesBySource = savedSelection;
        modelWeightState.savedSelectionApplied = true;
      }
      modelWeightState.savedSelectionMasksRestored = false;
      modelWeightState.loading = false;
      notifyChanged();
      return modelWeightSnapshot();
    })();
    return modelWeightState.promise
      .catch((error) => {
        if (generation === getGeneration()) {
          setModelWeightLoadError(error);
          refreshModelWeightSummary({ refreshStats: true });
          notifyChanged();
        }
        return modelWeightSnapshot();
      })
      .finally(() => {
        if (generation === getGeneration()) {
          performance.totalMs = clockNow() - loadStartedAt;
          modelWeightState.loading = false;
          modelWeightState.promise = null;
          notifyChanged();
        }
      });
  }

  function retainAvailableBones(selection) {
    if (!modelWeightState.loaded) return;
    const availableBySource = new Map(
      modelWeightState.sources.map((source) => [source.key, new Set(source.availableBoneIds)]),
    );
    for (const [sourceKey, ids] of selection) {
      const available = availableBySource.get(sourceKey);
      const filtered = new Set([...ids].filter((id) => available?.has(id)));
      if (filtered.size) selection.set(sourceKey, filtered);
      else selection.delete(sourceKey);
    }
  }

  function restoreSavedSelection({ generation = getGeneration(), isCurrent = () => true } = {}) {
    const current = () => generation === getGeneration() && isCurrent();
    if (!current() || !modelWeightState.loaded || modelWeightState.noWeights) return Promise.resolve(false);
    if (modelWeightState.savedSelectionMasksRestored) return Promise.resolve(modelWeightSnapshot());
    if (selectionRestoreOperation?.generation === generation) {
      const pending = selectionRestoreOperation;
      return pending.promise.then((result) => {
        if (result !== false || !current()) return result;
        if (selectionRestoreOperation === pending) selectionRestoreOperation = null;
        return restoreSavedSelection({ generation, isCurrent });
      });
    }
    const startedAt = clockNow();
    const operation = { generation, promise: null };
    operation.promise = (async () => {
      const restored = await restoreSelectedWeightMasks(generation, isCurrent);
      if (!restored || !current()) return false;
      modelWeightState.savedSelectionMasksRestored = true;
      if (modelWeightState.performance) {
        modelWeightState.performance.selectedMaskRestoreMs = clockNow() - startedAt;
      }
      return modelWeightSnapshot();
    })().finally(() => {
      if (selectionRestoreOperation === operation) selectionRestoreOperation = null;
    });
    selectionRestoreOperation = operation;
    return operation.promise;
  }

  function refreshModelBoneStats() {
    const statsBySource = new Map();
    for (const mesh of knownMeshes) {
      const state = states.get(mesh);
      if (!state?.loaded || !state.skinningSourceKey) continue;
      const sourceStats = statsBySource.get(state.skinningSourceKey) || [];
      sourceStats.push(state.weightBoneStats || {});
      statsBySource.set(state.skinningSourceKey, sourceStats);
    }
    modelWeightState.sources = modelWeightState.sources.map((source) => ({
      ...source,
      boneStats: aggregateModelWeightBoneStats(statsBySource.get(source.key) || []),
    }));
  }

  function refreshModelWeightSummary({ refreshStats = false } = {}) {
    const groups = new Map();
    const previousStats = new Map(modelWeightState.sources.map((source) => [source.key, source.boneStats || {}]));
    let loadedMeshCount = 0;
    let failedMeshCount = 0;
    knownMeshes.forEach((mesh) => {
      const state = states.get(mesh);
      if (state?.loaded) {
        loadedMeshCount += 1;
        const source = modelWeightState.sourceDescriptors.get(state.skinningSourceKey);
        if (!source) return;
        const group = groups.get(state.skinningSourceKey) || {
          key: source.sourceKey,
          file: source.sourceFile,
          boneIdOffset: source.boneIdOffset,
          availableBoneIds: new Set(),
          boneStats: previousStats.get(state.skinningSourceKey) || {},
        };
        (state.boneIds || []).forEach((id) => group.availableBoneIds.add(Number(id)));
        groups.set(state.skinningSourceKey, group);
      } else if (state?.error) {
        failedMeshCount += 1;
      }
    });
    modelWeightState.sources = [...groups.values()]
      .map((group) => ({
        ...group,
        availableBoneIds: [...group.availableBoneIds].filter(Number.isFinite).sort((left, right) => left - right),
      }))
      .sort((left, right) => left.key.localeCompare(right.key));
    modelWeightState.loadedMeshCount = loadedMeshCount;
    modelWeightState.failedMeshCount = failedMeshCount;
    retainAvailableBones(modelWeightState.selectedBonesBySource);
    if (refreshStats) refreshModelBoneStats();
  }

  function setSelectedBones(selection, { syncPhysics = true, refreshMasks = true } = {}) {
    const next = selectionMapFromEntries(selection);
    retainAvailableBones(next);
    const previous = modelWeightState.selectedBonesBySource;
    const changedSourceKeys = new Set([...previous.keys(), ...next.keys()]);
    for (const sourceKey of changedSourceKeys) {
      const before = previous.get(sourceKey);
      const after = next.get(sourceKey);
      if (before?.size === after?.size && [...before].every((id) => after.has(id))) {
        changedSourceKeys.delete(sourceKey);
      }
    }
    if (!changedSourceKeys.size) {
      return modelWeightSnapshot();
    }
    modelWeightState.selectedBonesBySource = next;
    if (refreshMasks)
      knownMeshes.forEach((mesh) => {
        const state = states.get(mesh);
        if (changedSourceKeys.has(state?.skinningSourceKey)) {
          refreshSelectedWeightMask(mesh, state, modelWeightState.selectedBonesBySource.get(state.skinningSourceKey));
        }
      });
    if (refreshMasks && modelWeightState.heatmapEnabled) {
      updateModelWeightHeatmap(changedSourceKeys, modelWeightState.heatmapEnabled);
    }
    if (syncPhysics) syncPhysicsToSelection(changedSourceKeys);
    notifyChanged();
    requestRender();
    return modelWeightSnapshot();
  }

  function setBoneSelected(sourceKey, boneId, selected) {
    const descriptor = modelWeightState.sourceDescriptors.get(sourceKey);
    const id = Number(boneId);
    if (!descriptor || !Number.isInteger(id) || id < 0) {
      return modelWeightSnapshot();
    }
    const entries = sourceSelectionEntries(modelWeightState.selectedBonesBySource).filter(
      (entry) => entry.sourceKey !== sourceKey,
    );
    const ids = new Set(modelWeightState.selectedBonesBySource.get(sourceKey));
    if (selected) ids.add(id);
    else ids.delete(id);
    if (ids.size) entries.push({ ...descriptor, boneIds: [...ids] });
    return setSelectedBones(entries);
  }

  function saveSelection() {
    if (selectionSavePromise) return selectionSavePromise;
    const selectedBones = serializeBoneSelection(sourceSelectionEntries(modelWeightState.selectedBonesBySource));
    const mesh = [...knownMeshes].find(eligibleSkinningMesh);
    const api = window.pywebview?.api?.save_weight_selection;
    if (!selectedBones.length || !mesh || typeof api !== 'function') {
      return Promise.resolve(modelWeightSnapshot());
    }
    const generation = getGeneration();
    modelWeightState.savingSelection = true;
    modelWeightState.selectionSaveError = null;
    notifyChanged();
    selectionSavePromise = Promise.resolve(api(mesh.userData.modPath, selectedBones))
      .then((result) => {
        if (generation !== getGeneration()) return modelWeightSnapshot();
        if (!result?.saved) throw new Error('The bone selection was not saved.');
        modelWeightState.savedBonesBySource = selectionMapFromEntries(result.selected_bones ?? selectedBones);
        return modelWeightSnapshot();
      })
      .catch((error) => {
        if (generation === getGeneration()) {
          modelWeightState.selectionSaveError = error instanceof Error ? error.message : String(error);
        }
        return modelWeightSnapshot();
      })
      .finally(() => {
        if (generation === getGeneration()) {
          modelWeightState.savingSelection = false;
          selectionSavePromise = null;
          notifyChanged();
        }
      });
    return selectionSavePromise;
  }

  return {
    getState: modelWeightSnapshot,
    refreshModelWeightSummary,
    ensureLoaded: loadModelWeights,
    restoreSavedSelection,
    setSelectedBones,
    setBoneSelected,
    clearSelectedBones: () => setSelectedBones([]),
    loadSavedBoneSelection: () => setSelectedBones(sourceSelectionEntries(modelWeightState.savedBonesBySource)),
    saveSelection,
    setHeatmap(enabled) {
      modelWeightState.heatmapEnabled = !!enabled;
      updateModelWeightHeatmap(null, modelWeightState.heatmapEnabled);
      notifyChanged();
      requestRender();
      return modelWeightState.heatmapEnabled;
    },
    reset() {
      selectionSavePromise = null;
      selectionRestoreOperation = null;
      refreshModelWeightSummary({ refreshStats: true });
    },
  };
}
