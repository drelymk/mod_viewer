"""Ordered control replay independent of GPU availability."""

import pytest


def _prepare_compute_clock(page):
    page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      window.__animation = await import('./js/mesh/animation-runtime.js');
      window.__controls = await import('./js/editing/control-state.js');
      const pending = new Map();
      let serial = 0;
      window.requestAnimationFrame = callback => {pending.set(++serial, callback); return serial;};
      window.cancelAnimationFrame = id => pending.delete(id);
      window.__frame = now => {
        const id = Math.min(...pending.keys()), callback = pending.get(id);
        if (!callback) throw new Error('No animation frame scheduled');
        pending.delete(id); callback(now);
      };
      window.__pendingFrames = () => pending.size;
      window.__encode = array => btoa(String.fromCharCode(...new Uint8Array(array.buffer)));
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0,0,2, 0,0,2, 0,0,2], 3));
      geometry.setIndex([0,1,2]);
      window.__mesh = new THREE.Mesh(geometry);
      window.__mesh.userData.basePositions = new Float32Array(geometry.attributes.position.array);
      window.__position = geometry.attributes.position;
      window.__normal = geometry.attributes.normal;
      window.__literal = value => ({kind: 'literal', value});
      window.__variable = variable => ({kind: 'variable', variable});
      window.__enabled = variable => [{kind: 'compare', op: '==',
        left: window.__variable(variable), right: window.__literal(1)}];
      window.__advance = (variable, speed, conditions) => ({op: 'set', variable, conditions,
        expression: {kind: 'binary', op: '+', left: window.__variable(variable),
          right: {kind: 'binary', op: '*', left: window.__literal(speed), right: {kind: 'dt'}}}});
    }""")


def test_control_replay_and_refresh_preserve_legal_live_values(module_page):
    result = module_page.evaluate("""async () => {
      const state = await import('./js/editing/control-state.js');
      state.resetControlState();
      const condition = (variable, value) => [[{var: variable, value, negate: false}]];
      const controls = {toggles: {key01: {vars: [{var: 'input01', values: ['0', '1']}]}}};
      const rules = [
        {var: 'derived01', value: '1', conditions: condition('input01', '1')},
        {var: 'derived02', value: '1', conditions: condition('derived01', '1')},
      ];
      state.setControlStateRules(rules, {input01: '0', derived01: '0', derived02: '0'}, controls);
      state.setControlValue('input01', '1');
      state.replayControlStateRules();
      const visible = state.dnfSatisfied(condition('derived02', '1'));
      state.reconcileControlState(rules, {input01: '0', derived01: '0', derived02: '0'}, controls);
      const preserved = state.getControlValue('input01');
      controls.toggles.key01.vars[0].values = ['0'];
      state.reconcileControlState([], {input01: '0'}, controls);
      return {visible, preserved, final: state.getControlState()};
    }""")
    assert result == {'visible': True, 'preserved': '1', 'final': {'input01': '0'}}


def test_loose_partition_merge_and_cleanup_preserve_authored_triangle_identity(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three/webgpu');
      const parts = await import('./js/mesh/loose-parts.js');
      const create = () => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute([
          0,0,0, 1,0,0, 0,1,0, 4,0,0, 5,0,0, 4,1,0, 8,0,0, 9,0,0, 8,1,0], 3));
        geometry.setIndex([0,1,2,3,4,5,6,7,8]);
        return new THREE.Mesh(geometry, new THREE.MeshBasicNodeMaterial());
      };
      const source = create(), sibling = create();
      const position = source.geometry.attributes.position;
      const index = source.geometry.index;
      const originalRange = {...source.geometry.drawRange};
      const partition = parts.separateLooseParts(source);
      const other = parts.separateLooseParts(sibling);
      const initial = partition.map(part => part.userData.loosePartTriangles);
      const shared = partition.every(part => part.geometry.attributes.position === position && part.material === source.material);
      const crossSource = parts.canMergeLooseParts([partition[0], other[0]]);
      parts.mergeLooseParts([partition[2], partition[0]]);
      const merged = parts.getLooseParts(source).map(part => part.userData.loosePartTriangles);
      parts.clearLooseParts(source);
      parts.clearLooseParts(sibling);
      return {initial, shared, crossSource, merged, clean: parts.getLooseParts(source).length === 0,
        restored: source.geometry.index === index && source.geometry.drawRange.count === originalRange.count};
    }""")
    assert result['initial'] == [[0], [1], [2]]
    assert result['shared'] and result['restored'] and result['clean']
    assert result['crossSource'] is False
    assert sorted(result['merged']) == [[0, 2], [1]]


def test_weight_selection_keeps_equal_bone_ids_scoped_to_exact_sources(module_page):
    result = module_page.evaluate("""async () => {
      const weights = await import('./js/weight-rig/weight-selection.js');
      const selection = weights.normalizeBoneSelection([
        {source: 'folder-01/stream.buf', bone_id_offset: 0, bone_ids: [2, 2, 4]},
        {source: 'folder-02/stream.buf', bone_id_offset: 0, bone_ids: [2]},
        {source: 'folder-01/stream.buf', bone_id_offset: 0, bone_ids: [6]},
      ]);
      const first = [...weights.selectionForSource(selection, 'folder-01/stream.buf|offset=0')];
      const serialized = weights.serializeBoneSelection(selection);
      return {first, serialized, count: weights.selectedBoneCount(selection)};
    }""")
    assert result['first'] == [2, 4, 6]
    assert result['count'] == 4
    assert [entry['source'] for entry in result['serialized']] == ['folder-01/stream.buf', 'folder-02/stream.buf']
    assert [entry['bone_ids'] for entry in result['serialized']] == [[2, 4, 6], [2]]


def test_weight_session_preserves_model_wide_descriptor_through_saved_selection_and_save(module_page):
    result = module_page.evaluate("""async () => {
      const selection = await import('./js/weight-rig/weight-selection.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const {createWeightModelSession} = await import('./js/weight-rig/weight-model-session.js');
      const runtime = createWeightRuntimeState();
      const mesh = {userData: {modPath: 'fixture-root', semanticKey: 'mesh-a'}, geometry: {index: null}};
      runtime.knownMeshes.add(mesh);
      const sourceKey = 'fixture/stream.buf|offset=7';
      const descriptor = {sourceKey, sourceFile: 'fixture/stream.buf', boneIdOffset: 7, boneIdsModelWide: true};
      let saved = null;
      const refreshedSelections = [];
      const snapshot = () => ({
        loaded: runtime.modelWeightState.loaded,
        selectedBones: selection.selectionRecordsFromMap(
          runtime.modelWeightState.selectedBonesBySource, runtime.modelWeightState.sourceDescriptors),
      });
      const session = createWeightModelSession({
        modelWeightState: runtime.modelWeightState,
        states: runtime.states,
        stateFor: runtime.stateFor,
        knownMeshes: runtime.knownMeshes,
        modelWeightSnapshot: snapshot,
        selectionMapFromEntries: selection.selectionMapFromEntries,
        sourceSelectionEntries: map => selection.sourceSelectionEntries(map, runtime.modelWeightState.sourceDescriptors),
        refreshSelectedWeightMask: (_mesh, _state, boneIds) => refreshedSelections.push([...(boneIds || [])]),
        updateModelWeightHeatmap: () => {},
        installSkinningEntry: () => {
          const state = runtime.stateFor(mesh);
          Object.assign(state, {loaded: true, skinningSourceKey: sourceKey, boneIds: [3, 5], influenceCount: 1,
            indices: new Uint32Array([3, 5]), weights: new Float32Array([1, 0]), baselinePositions: new Float32Array([0, 0, 0])});
          return {source: descriptor};
        },
        syncPhysicsToSelection: () => true,
        serializeBoneSelection: selection.serializeBoneSelection,
        eligibleSkinningMesh: () => true,
        notifyChanged: () => {}, requestRender: () => {}, getGeneration: () => 1,
      });
      const previousApi = window.pywebview?.api;
      window.pywebview = {api: {
        get_model_skinning_preview: async () => ({
          saved_bones: [{source: descriptor.sourceFile, source_key: sourceKey, bone_id_offset: 7, bone_ids: [3]}],
          meshes: {'mesh-a': {status: 'ok'}}, data: null,
        }),
        save_weight_selection: async (path, entries) => {
          saved = {path, entries};
          return {saved: true, selected_bones: entries};
        },
      }};
      try {
        const ready = await session.ensureLoaded();
        const beforeMasks = {
          selected: ready.selectedBones[0]?.boneIds,
          descriptor: runtime.modelWeightState.sourceDescriptors.get(sourceKey)?.boneIdsModelWide,
          masksRestored: runtime.modelWeightState.savedSelectionMasksRestored,
        };
        session.setBoneSelected(sourceKey, 5, true);
        await session.restoreSavedSelection();
        await session.saveSelection();
        return {
          beforeMasks,
          refreshedSelections,
          afterSave: selection.selectionRecordsFromMap(
            runtime.modelWeightState.selectedBonesBySource, runtime.modelWeightState.sourceDescriptors),
          descriptorAfterSave: runtime.modelWeightState.sourceDescriptors.get(sourceKey)?.boneIdsModelWide,
          saved: saved && {path: saved.path, entries: saved.entries},
        };
      } finally {
        window.pywebview = {api: previousApi};
      }
    }""")
    assert result['beforeMasks'] == {'selected': [3], 'descriptor': True, 'masksRestored': False}
    assert result['refreshedSelections'] == [[3, 5], [3, 5]]
    assert result['afterSave'][0]['boneIds'] == [3, 5]
    assert result['afterSave'][0]['boneIdsModelWide'] is True
    assert result['descriptorAfterSave'] is True
    assert result['saved']['path'] == 'fixture-root'
    assert result['saved']['entries'][0]['bone_ids'] == [3, 5]


def test_weight_rig_activation_sequences_ready_paint_and_retries_independent_stages(module_page):
    result = module_page.evaluate("""async () => {
      const {createWeightRigActivationSession} = await import('./js/weight-rig/weight-rig-activation-session.js');
      let generation = 4, releasePaint;
      const paintGate = new Promise(resolve => { releasePaint = resolve; });
      const calls = [];
      let rigAttempt = 0;
      const session = createWeightRigActivationSession({
        getGeneration: () => generation,
        ensureWeightsLoaded: async () => { calls.push('weights'); return {loaded: true}; },
        waitForWeightReadyPaint: async () => { calls.push('paint-wait'); await paintGate; calls.push('painted'); },
        restoreSavedSelection: async () => { calls.push('selection'); return {savedSelectionMasksRestored: true}; },
        syncPhysicsToSelection: async () => { calls.push('physics'); return true; },
        ensureRigLoaded: async () => {
          calls.push('rig'); rigAttempt += 1;
          return rigAttempt === 1 ? {loaded: false, error: 'fixture rig failure'} : {loaded: true};
        },
      });
      const first = session.activate();
      const duplicate = session.activate();
      await Promise.resolve();
      const beforePaint = [...calls];
      releasePaint();
      const firstResult = await first;
      const retryResult = await session.activate();
      const afterRetry = [...calls];
      const completedResult = await session.activate();
      return {samePromise: first === duplicate, beforePaint, firstRig: firstResult.rig,
        retryRig: retryResult.rig, afterRetry, afterComplete: calls, completedRig: completedResult.rig};
    }""")
    assert result['samePromise'] is True
    assert result['beforePaint'] == ['weights', 'paint-wait']
    assert result['firstRig']['error'] == 'fixture rig failure'
    assert result['retryRig']['loaded'] is True
    assert result['afterRetry'].count('physics') == 1
    assert result['afterRetry'].count('rig') == 2
    assert result['afterComplete'] == result['afterRetry']


def test_weight_rig_activation_cancels_after_model_rebaseline(module_page):
    result = module_page.evaluate("""async () => {
      const {createWeightRigActivationSession} = await import('./js/weight-rig/weight-rig-activation-session.js');
      let generation = 2, releasePaint;
      const paintGate = new Promise(resolve => { releasePaint = resolve; });
      const calls = [];
      const session = createWeightRigActivationSession({
        getGeneration: () => generation,
        ensureWeightsLoaded: async () => ({loaded: true}),
        waitForWeightReadyPaint: async () => paintGate,
        restoreSavedSelection: async () => { calls.push('selection'); },
        syncPhysicsToSelection: async () => { calls.push('physics'); },
        ensureRigLoaded: async () => { calls.push('rig'); return {loaded: true}; },
      });
      const pending = session.activate();
      await Promise.resolve();
      session.invalidate();
      releasePaint();
      const result = await pending;
      return {result, calls};
    }""")
    assert result['result']['stale'] is True
    assert result['calls'] == []


def test_prepared_model_rig_commit_discards_stale_save_completion(module_page):
    result = module_page.evaluate("""async () => {
      const {commitPreparedModelRig} = await import('./js/weight-rig/weight-rig-activation-session.js');
      let current = true, releaseSave;
      const saveGate = new Promise(resolve => { releaseSave = resolve; });
      const calls = [];
      const pending = commitPreparedModelRig({
        isCurrent: () => current,
        saveCache: async () => { calls.push('save-start'); await saveGate; calls.push('save-end'); return true; },
        activate: () => calls.push('activate'),
      });
      await Promise.resolve();
      current = false;
      releaseSave();
      const result = await pending;
      return {result, calls};
    }""")
    assert result['result']['activated'] is False
    assert result['result']['cacheSaved'] is True
    assert result['calls'] == ['save-start', 'save-end']


def test_source_rig_cooperative_preparation_keeps_source_wide_mode_and_cache_identity(module_page):
    result = module_page.evaluate("""async () => {
      const {createRigSourceSession} = await import('./js/weight-rig/rig-model-session.js');
      const sourceKey = 'fixture/stream.buf|offset=0';
      const sourceSkinningRigs = new Map();
      const modelWeightState = {sourceDescriptors: new Map([[sourceKey, {
        sourceKey, sourceFile: 'fixture/stream.buf', boneIdOffset: 0,
      }]])};
      const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
      const indices = new Uint32Array([1, 1, 1]);
      const weights = new Float32Array([1, 1, 1]);
      const makeMesh = (name, boneId, triangleIndices) => ({
        userData: {semanticKey: name}, geometry: {index: {array: triangleIndices}}, boneId,
      });
      const first = makeMesh('mesh-a', 1, new Uint16Array([0, 1, 2]));
      const duplicate = makeMesh('mesh-b', 1, new Uint16Array([0, 1, 2]));
      const vertexFallback = makeMesh('mesh-c', 2, new Uint16Array([0, 1]));
      const states = new Map([first, duplicate, vertexFallback].map(mesh => [mesh, {
        loaded: true, skinningSourceKey: sourceKey, influenceCount: 1,
        boneIds: [mesh.boneId], indices, weights, baselinePositions: positions,
      }]));
      const knownMeshes = new Set([first, duplicate, vertexFallback]);
      const preparationModes = [];
      let preparationCount = 0;
      const session = createRigSourceSession({
        states, knownMeshes, modelWeightState, sourceSkinningRigs,
        ensureRigMeshPreparedCooperative: async () => { preparationCount += 1; return true; },
        ensureInfluenceGraphCooperative: async (mesh, state, mode) => {
          preparationModes.push(mode);
          return {evidenceMode: mode, nodes: [{boneId: mesh.boneId, totalWeight: 1,
            affectedVertexCount: 1, weightedCenter: [mesh.boneId, 0, 0], weightedRadius: 0,
            maxVertexWeight: 1}], relationships: []};
        },
        rebuildRestFrames: () => {}, cloneForest: forest => structuredClone(forest),
      });
      const members = [first, duplicate, vertexFallback];
      const rig = await session.ensureCooperative(sourceKey, members, {generation: 1, isCurrent: () => true});
      const callsAfterBuild = preparationCount;
      const cached = await session.ensureCooperative(sourceKey, members, {generation: 1, isCurrent: () => true});
      return {sameObject: rig === cached, preparationModes, callsAfterBuild, callsAfterCacheHit: preparationCount,
        memberCount: rig.influenceGraph.memberCount, uniqueMemberCount: rig.influenceGraph.uniqueMemberCount,
        meshCount: rig.meshes.size, vertexEvidenceCount: rig.vertexEvidence.length};
    }""")
    assert result['sameObject'] is True
    assert result['preparationModes'] == ['vertex', 'vertex']
    assert result['callsAfterCacheHit'] == result['callsAfterBuild'] == 3
    assert result['memberCount'] == 3
    assert result['uniqueMemberCount'] == 2
    assert result['meshCount'] == 3
    assert result['vertexEvidenceCount'] == 3


def test_control_conditions_preserve_or_groups_negation_and_contradictions(module_page):
    result = module_page.evaluate("""async () => {
      const state = await import('./js/editing/control-state.js');
      state.resetControlState(); state.setControlValue('input01', '1');
      const is = value => ({var: 'input01', value, negate: false});
      const not = value => ({var: 'input01', value, negate: true});
      return [state.dnfSatisfied([]), state.dnfSatisfied([[is('0'), is('1')]]),
        state.dnfSatisfied([[is('0')], [is('1')]]), state.dnfSatisfied([[not('1')]]),
        state.dnfSatisfied([[not('0')]])];
    }""")
    assert result == [True, False, True, False, True]


def test_compute_shape_and_pose_reuse_attributes_across_pause_and_resume(module_page):
    _prepare_compute_clock(module_page)
    result = module_page.evaluate("""() => {
      const runtime = window.__animation, controls = window.__controls;
      const encode = window.__encode, variable = window.__variable;
      controls.setControlValue('input01', '1');
      const frames = new Float32Array(28);
      for (const offset of [0,14]) {
        frames[offset] = frames[offset+1] = frames[offset+2] = 1;
        frames[offset+3] = 0.25; frames[offset+9] = 1;
      }
      const program = {external_variables: ['input01'], initials: {phase01: 0}, commands: [
        window.__advance('phase01', 1, window.__enabled('input01')),
        {op: 'dispatch', track_id: 'track-01', kind: 'shape', pass: 0, phase: variable('phase01')},
        {op: 'dispatch', track_id: 'track-01', kind: 'pose', phase: variable('phase01')},
      ]};
      const registered = runtime.registerAnimatedMesh(window.__mesh, 'track-01', {
        kind: 'gimi_compute', program_id: 'program-01', track_id: 'track-01', program, vertex_count: 3,
        base_normals: encode(new Float32Array([0,0,2, 0,0,2, 0,0,2])),
        shape_passes: [{deltas: encode(new Float32Array([1,0,0,0,0,0, 1,0,0,0,0,0, 1,0,0,0,0,0])),
          weight_operation: {kind: 'sine', scale: 1, amplitude: 0.5, offset: 0.5}}],
        pose: {bone_count: 1, frame_count: 2, frames: encode(frames), blend: {
          weights: encode(new Float32Array([1,0,0,0, 1,0,0,0, 1,0,0,0])),
          indices: encode(new Int32Array(12)),
        }},
      });
      window.__frame(0);
      const first = [...window.__position.array];
      controls.setControlValue('input01', '0'); window.__frame(1000);
      const paused = [...window.__position.array], sleeping = window.__pendingFrames() === 0;
      controls.setControlValue('input01', '1'); runtime.wakeAnimationRuntime();
      window.__frame(5000);
      const resumed = [...window.__position.array];
      window.__frame(5100);
      const continued = [...window.__position.array];
      runtime.resetAnimationRuntime();
      return {registered, first, paused, resumed, continued, sleeping,
        stable: window.__position === window.__mesh.geometry.attributes.position
          && window.__normal === window.__mesh.geometry.attributes.normal,
        normals: [...window.__normal.array], cleared: runtime.animationRuntimeSnapshot().clocks};
    }""")
    assert result['registered'] and result['stable'] and result['sleeping']
    assert result['first'] == pytest.approx([0.75,0,0, 1.75,0,0, 0.75,1,0])
    assert result['paused'] == result['resumed'] == result['first']
    assert result['continued'][0] > result['resumed'][0] + 0.04
    assert result['normals'] == [0,0,1] * 3
    assert result['cleared'] == 0


def test_sparse_compute_overlay_retains_rest_geometry_and_independent_pass_state(module_page):
    _prepare_compute_clock(module_page)
    result = module_page.evaluate("""() => {
      const runtime = window.__animation, controls = window.__controls, mesh = window.__mesh;
      const encode = window.__encode, variable = window.__variable;
      mesh.userData.humanoidRestPositions = new Float32Array([10,0,0, 11,0,0, 10,1,0]);
      mesh.userData.humanoidRestNormals = new Float32Array([0,0,1, 0,0,1, 0,0,1]);
      window.__position.array.set(mesh.userData.humanoidRestPositions);
      window.__normal.array.set(mesh.userData.humanoidRestNormals);
      controls.setControlValue('input01', '1'); controls.setControlValue('input02', '0');
      const commands = ['input01','input02'].flatMap((input, pass) => [
        window.__advance('phase' + pass, pass + 1, window.__enabled(input)),
        {op: 'dispatch', track_id: 'track-01', kind: 'shape', pass,
          phase: variable('phase' + pass)},
      ]);
      const registered = runtime.registerAnimatedMesh(mesh, 'track-01', {
        kind: 'gimi_compute', program_id: 'program-01', track_id: 'track-01', vertex_count: 3,
        program: {external_variables: ['input01','input02'], initials: {phase0: 0.2, phase1: 0.3}, commands},
        overlay: true, position_only: true, pose: null,
        shape_passes: [[1,0,0, 1,0,0, 1,0,0], [0,2,0, 0,2,0, 0,2,0]].map(deltas => ({
          deltas: encode(new Float32Array(deltas)), weight_operation: {kind: 'linear'}})),
      });
      window.__frame(0);
      const first = [...window.__position.array];
      controls.setControlValue('input02', '1'); runtime.wakeAnimationRuntime();
      window.__frame(100); window.__frame(200);
      const both = [...window.__position.array];
      controls.setControlValue('input01', '0'); runtime.wakeAnimationRuntime();
      window.__frame(300); window.__frame(400);
      const independent = [...window.__position.array];
      controls.setControlValue('input02', '0'); runtime.wakeAnimationRuntime(); window.__frame(500);
      const frozen = [...window.__position.array], sleeping = window.__pendingFrames() === 0;
      runtime.resetAnimationRuntime();
      return {registered, first, both, independent, frozen, sleeping,
        stable: window.__position === mesh.geometry.attributes.position,
        normals: [...window.__normal.array], baseline: [...mesh.userData.basePositions]};
    }""")
    assert result['registered'] and result['stable'] and result['sleeping']
    assert result['first'] == pytest.approx([10.2,0.6,0, 11.2,0.6,0, 10.2,1.6,0])
    assert result['both'][0] > result['first'][0]
    assert result['both'][1] > result['first'][1]
    assert result['independent'][0] == pytest.approx(result['both'][0])
    assert result['independent'][1] > result['both'][1]
    assert result['frozen'] == result['independent']
    assert result['normals'] == [0,0,1] * 3
    assert result['baseline'] == [0,0,0,1,0,0,0,1,0]
