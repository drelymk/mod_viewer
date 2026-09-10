// Weight-model session. This owns the product-facing selection, picking-view
// and heatmap state while the coordinator supplies shared mesh algorithms.

let activeSession = null;

function createSession({modelWeightState, states, knownMeshes,
    modelWeightSnapshot, selectionMapFromEntries, sourceSelectionEntries,
    refreshModelWeightSummary, refreshSelectedWeightMask,
    updateModelWeightHeatmap, syncPhysicsToSelection, sameBoneSelection,
    serializeBoneSelection, eligibleSkinningMesh, notifyChanged,
    requestRender, getGeneration} = {}) {
  let selectionSavePromise = null;

  function setSelectedBones(selection) {
    refreshModelWeightSummary();
    const next = selectionMapFromEntries(selection);
    if (modelWeightState.loaded) {
      const available = new Map(modelWeightState.sources.map(source => [
        source.key, new Set(source.availableBoneIds),
      ]));
      for (const [sourceKey, ids] of next) {
        const valid = available.get(sourceKey);
        if (!valid) {
          next.delete(sourceKey);
          continue;
        }
        const filtered = new Set([...ids].filter(id => valid.has(id)));
        if (filtered.size) next.set(sourceKey, filtered);
        else next.delete(sourceKey);
      }
    }
    const previousEntries = sourceSelectionEntries(
      modelWeightState.selectedBonesBySource);
    const nextEntries = sourceSelectionEntries(next);
    if (sameBoneSelection(previousEntries, nextEntries)) {
      syncPhysicsToSelection();
      return modelWeightSnapshot();
    }
    const changedSourceKeys = new Set([
      ...modelWeightState.selectedBonesBySource.keys(), ...next.keys(),
    ].filter(sourceKey => !sameBoneSelection(
      sourceSelectionEntries(new Map([
        [sourceKey, modelWeightState.selectedBonesBySource.get(sourceKey)
          || new Set()],
      ])),
      sourceSelectionEntries(new Map([
        [sourceKey, next.get(sourceKey) || new Set()],
      ])),
    )));
    modelWeightState.selectedBonesBySource = next;
    knownMeshes.forEach(mesh => {
      const state = states.get(mesh);
      if (changedSourceKeys.has(state?.skinningSourceKey)) {
        refreshSelectedWeightMask(mesh, state);
      }
    });
    if (modelWeightState.heatmapEnabled) {
      updateModelWeightHeatmap(changedSourceKeys);
    }
    syncPhysicsToSelection(changedSourceKeys);
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
    const entries = sourceSelectionEntries(modelWeightState.selectedBonesBySource)
      .filter(entry => entry.sourceKey !== sourceKey);
    const ids = new Set(modelWeightState.selectedBonesBySource.get(sourceKey));
    if (selected) ids.add(id);
    else ids.delete(id);
    if (ids.size) entries.push({...descriptor, boneIds: [...ids]});
    return setSelectedBones(entries);
  }

  function saveSelection() {
    if (selectionSavePromise) return selectionSavePromise;
    const selectedBones = serializeBoneSelection(sourceSelectionEntries(
      modelWeightState.selectedBonesBySource));
    const mesh = [...knownMeshes].find(eligibleSkinningMesh);
    const api = window.pywebview?.api?.save_weight_selection;
    if (!selectedBones.length || !mesh || typeof api !== 'function') {
      return Promise.resolve(modelWeightSnapshot());
    }
    const generation = getGeneration();
    modelWeightState.savingSelection = true;
    modelWeightState.selectionSaveError = null;
    notifyChanged();
    selectionSavePromise = Promise.resolve(
      api(mesh.userData.modPath, selectedBones))
      .then(result => {
        if (generation !== getGeneration()) return modelWeightSnapshot();
        if (!result?.saved) throw new Error('The bone selection was not saved.');
        modelWeightState.savedBonesBySource = selectionMapFromEntries(
          result.selected_bones ?? selectedBones);
        return modelWeightSnapshot();
      })
      .catch(error => {
        if (generation === getGeneration()) {
          modelWeightState.selectionSaveError = error instanceof Error
            ? error.message : String(error);
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
    setSelectedBones,
    setBoneSelected,
    clearSelectedBones: () => setSelectedBones([]),
    loadSavedBoneSelection: () => setSelectedBones(sourceSelectionEntries(
      modelWeightState.savedBonesBySource)),
    saveSelection,
    setHeatmap(enabled) {
      modelWeightState.heatmapEnabled = !!enabled;
      updateModelWeightHeatmap();
      notifyChanged();
      requestRender();
      return modelWeightState.heatmapEnabled;
    },
    reset() { selectionSavePromise = null; },
  };
}

export function initializeWeightModelSession(options) {
  activeSession = createSession(options);
}

export function resetWeightModelSession() { activeSession?.reset(); }

function session() {
  if (!activeSession) throw new Error('Weight model session is not initialized.');
  return activeSession;
}

export function getModelWeightState() { return session().getState(); }
export function setSelectedBones(selection) {
  return session().setSelectedBones(selection);
}
export function setBoneSelected(sourceKey, boneId, selected) {
  return session().setBoneSelected(sourceKey, boneId, selected);
}
export function clearSelectedBones() { return session().clearSelectedBones(); }
export function loadSavedBoneSelection() {
  return session().loadSavedBoneSelection();
}
export function saveModelWeightSelection() { return session().saveSelection(); }
export function setModelWeightHeatmap(enabled) {
  return session().setHeatmap(enabled);
}
