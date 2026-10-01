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


def test_saved_humanoid_arm_rebinds_after_builder_change_and_deforms_vertices(module_page):
    result = module_page.evaluate("""async () => {
      const humanoid = await import('./js/weight-rig/humanoid-control-rig.js');
      const bindingApi = await import('./js/weight-rig/humanoid-rig-binding.js');
      const ik = await import('./js/weight-rig/humanoid-rig-ik.js');
      const deformation = await import('./js/weight-rig/weight-deformation.js');
      const automatic = humanoid.buildHumanoidControlRig({meshes: [{userData: {
        humanoidRestPositions: new Float32Array([-1,0,0, 1,0,0, 0,2,0, -1,1.5,0, 1,1.5,0]),
      }}]});
      const keys = ['leftShoulder', 'leftElbow', 'leftHand'];
      const positions = ['rightHand', ...keys].map(key => automatic.controls[key].position);
      const model = {joints: positions.map((restPivot, jointId) => ({jointId, restPivot})),
        components: [{rootId: 0, nodeIds: [0], parentById: {0: null}, childrenById: {0: []}},
          {rootId: 1, nodeIds: [1,2,3], parentById: {1: null, 2: 1, 3: 2},
            childrenById: {1: [2], 2: [3], 3: []}}]};
      const baseline = new Float32Array(positions.flat());
      const indices = new Uint32Array([10,11,12,13]);
      const weights = new Float32Array([1,1,1,1]);
      const authored = {indices: [...indices], weights: [...weights]};
      const target = [...positions[3]]; target[1] += 0.1; target[2] += 0.05;
      const rows = [];
      for (const version of [1, 2, humanoid.MODEL_RIG_BUILDER_VERSION]) {
        const current = version === humanoid.MODEL_RIG_BUILDER_VERSION;
        const saved = {version: humanoid.HUMANOID_CONTROL_RIG_VERSION,
          model_rig_builder_version: version, controls: Object.fromEntries(keys.map((key, index) =>
            [key, {semantic: automatic.controls[key].semantic, joint_id: index + (current ? 1 : 0)}]))};
        const originalSaved = JSON.stringify(saved);
        const mappings = humanoid.resolveHumanoidControlMappings({savedOverrides: saved, modelRig: model});
        const controlRig = humanoid.applyHumanoidControlRigOverrides({automaticRig: automatic,
          savedOverrides: saved, modelRig: model, resolvedMappings: mappings});
        const binding = bindingApi.buildHumanoidRigBinding({controlRig, modelRig: model, controlMappings: mappings});
        const solved = ik.solveHumanoidControlIk({controlRig, role: 'left_arm', target});
        const posedControls = ik.mergeHumanoidLimbPose({}, solved.positions, solved.keys);
        const deform = pose => {
          const driver = bindingApi.buildHumanoidDriverBaseTransforms({binding, controlRig,
            modelRig: model, posedControls: pose});
          const aliases = new Map([...driver.result].map(([id, matrix]) => [id + 10, matrix]));
          return deformation.applyWeightedTransformDeformation(baseline, indices, weights, 1, aliases);
        };
        const posed = deform(posedControls), restored = deform(null);
        const close = (actual, expected) => actual.every((value, i) => Math.abs(value - expected[i]) < 1e-5);
        rows.push({current, mappedIds: [...mappings.values()].map(value => value.jointId),
          rejected: [...mappings.rejectedControlKeys],
          anchors: keys.map(key => binding.diagnostics.bindingsByControl[key].anchorJointId),
          reached: solved.reached, handFollows: close([...posed.slice(9,12)], solved.end),
          handMoved: !close([...posed.slice(9,12)], positions[3]),
          otherHandUnchanged: close([...posed.slice(0,3)], positions[0]),
          restored: close([...restored], [...baseline]), savedUnchanged: JSON.stringify(saved) === originalSaved});
      }
      return {rows, authoredUnchanged: JSON.stringify(authored) ===
        JSON.stringify({indices: [...indices], weights: [...weights]})};
    }""")
    assert result['authoredUnchanged']
    for row in result['rows']:
        assert row['mappedIds'] == ([1, 2, 3] if row['current'] else [])
        assert row['rejected'] == []
        assert row['anchors'] == [1, 2, 3]
        assert all(row[key] for key in ('reached', 'handFollows', 'handMoved',
                                       'otherHandUnchanged', 'restored', 'savedUnchanged'))


@pytest.mark.parametrize(('builder', 'joint_id', 'valid_semantic'), [
    (None, 0, True), (0, 0, True), (4, 0, True), (True, 0, True),
    (1, -1, True), (1, None, True), (1, '0', True), (1, 0, False),
    (3, -1, True), (3, 99, True),
])
def test_invalid_humanoid_mapping_provenance_stays_unbound(module_page, builder, joint_id, valid_semantic):
    result = module_page.evaluate("""async ({builder, jointId, validSemantic}) => {
      const humanoid = await import('./js/weight-rig/humanoid-control-rig.js');
      const semantic = validSemantic ? {sideN: -0.2, height01: 0.7, depthN: 0} : {height01: 0.7};
      const mappings = humanoid.resolveHumanoidControlMappings({
        savedOverrides: {model_rig_builder_version: builder,
          controls: {leftHand: {joint_id: jointId, semantic}}},
        modelRig: {joints: [{jointId: 0}]},
      });
      return {mapped: [...mappings], rejected: [...mappings.rejectedControlKeys]};
    }""", {'builder': builder, 'jointId': joint_id, 'validSemantic': valid_semantic})
    assert result == {'mapped': [], 'rejected': ['leftHand']}


@pytest.fixture
def reconciliation_page(module_page):
    module_page.evaluate("""async () => {
      window.__reconciliation = await import('./js/weight-rig/weight-rig-reconcile.js');
      window.__sourceRig = (sourceKey, rows, modelWide = false, direction = [1, 0, 0]) => {
        const nodes = rows.map(([boneId, center, parent, weight = 1]) => ({
          boneId, weightedCenter: center, weightedRadius: 1, totalWeight: weight, affectedVertexCount: 32,
        }));
        const parentById = Object.fromEntries(rows.map(([id, , parent]) => [id, parent]));
        const childrenById = Object.fromEntries(rows.map(([id]) => [id,
          rows.filter(([, , parent]) => parent === id).map(([child]) => child)]));
        const components = rows.filter(([, , parent]) => parent === null).map(([rootId], componentId) => {
          const nodeIds = [], depthById = {[rootId]: 0}, pending = [rootId];
          while (pending.length) {
            const id = pending.shift(); nodeIds.push(id);
            childrenById[id].forEach(child => {depthById[child] = depthById[id] + 1; pending.push(child);});
          }
          return {componentId, rootId, nodeIds, depthById,
            parentById: Object.fromEntries(nodeIds.map(id => [id, parentById[id]])),
            childrenById: Object.fromEntries(nodeIds.map(id => [id, childrenById[id]])),
            edges: nodeIds.filter(id => parentById[id] !== null).map(id =>
              ({boneA: parentById[id], boneB: id, treeEdgeScore: 1})),
          };
        });
        return {sourceKey, boneIdsModelWide: modelWide, boneIds: nodes.map(node => node.boneId),
          influenceGraph: {nodes, relationships: components.flatMap(component => component.edges)},
          inferredForest: {components, componentByBoneId: new Map(components.flatMap(component =>
            component.nodeIds.map(id => [id, component.componentId])))},
          centerByBoneId: new Map(nodes.map(node => [node.boneId, node.weightedCenter])),
          restDirectionByBoneId: new Map(nodes.map(node => [node.boneId, direction])),
          restFrameEvidenceByBoneId: new Map(nodes.map(node => [node.boneId, {directionSource: 'geometry'}])),
          restFrameByBoneId: new Map(), jointPivotByBoneId: new Map(), vertexEvidence: [],
        };
      };
      window.__buildRig = (rigs, options = {}, work = {}) =>
        window.__reconciliation.buildModelRigReconciliationCooperative(rigs, options,
          {budget: {checkpoint: async () => {}}, ...work});
    }""")
    return module_page


@pytest.mark.parametrize(('marked', 'model_wide'), [(False, False), (True, False), (True, True)])
def test_reconciliation_preserves_exact_sources_and_stable_rebuild(reconciliation_page, marked, model_wide):
    result = reconciliation_page.evaluate("""async ({marked, modelWide}) => {
      const {sourceBoneKey} = window.__reconciliation;
      const keys = [1, 2].map(id => marked ? `stream#bone=part-0${id}.buf|offset=0`
        : `folder-0${id}/stream.buf|offset=0`);
      const rigs = keys.map(key => window.__sourceRig(key, [[1, [0, 0, 0], null]], modelWide));
      const first = await window.__buildRig(rigs);
      const permuted = await window.__buildRig([...rigs].reverse());
      const rebuilt = await window.__buildRig(rigs);
      const signature = rig => rig.joints.map(joint => [joint.jointId, joint.signature]);
      const separate = await window.__buildRig([
        window.__sourceRig(keys[0], [[1, [0, 0, 0], null]], true),
        window.__sourceRig(keys[1], [[1, [10, 0, 0], null]], false),
      ], {modelReferenceRadius: 1});
      return {jointCount: first.joints.length, members: first.joints[0].members.map(member => member.sourceKey),
        mapped: keys.map(key => first.sourceBoneToModelJointMap.get(sourceBoneKey(key, 1))),
        stable: JSON.stringify(signature(first)) === JSON.stringify(signature(permuted)),
        rebuilt: JSON.stringify(signature(first)) === JSON.stringify(signature(rebuilt)),
        separateCount: separate.joints.length};
    }""", {'marked': marked, 'modelWide': model_wide})
    assert result['jointCount'] == 1
    assert len(set(result['members'])) == 2
    assert result['mapped'] == [0, 0]
    assert result['stable'] and result['rebuilt']
    assert result['separateCount'] == 2


def test_reconciliation_skips_unneeded_vertex_work_and_completes_strict_chains(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const source = window.__sourceRig('source-01', [[1, [0, 0, 0], null]]);
      source.vertexEvidence = [{get positions() {throw new Error('unused vertex evidence was read');}}];
      const single = await window.__buildRig([source]);
      const empty = window.__sourceRig('source-02', []);
      empty.vertexEvidence = source.vertexEvidence;
      const withEmpty = await window.__buildRig([source, empty]);
      const chain = (key, first) => window.__sourceRig(key, Array.from({length: 100}, (_,i) =>
        [first+i, [i, 0, 0], i ? first+i-1 : null]));
      let completeCheckpoints = 0;
      const complete = await window.__buildRig([chain('source-01', 1), chain('source-02', 201)], {},
        {budget: {checkpoint: async () => {completeCheckpoints += 1;}}});
      return {singleCount: single.joints.length, withEmptyCount: withEmpty.joints.length,
        candidateCount: single.reconciliation.candidateCount,
        completeCount: complete.joints.length, edges: complete.edges.length,
        completedWithoutPathWork: completeCheckpoints < 100,
        strictCount: complete.reconciliation.acceptedEquivalences.filter(item => item.pass === 'strict').length,
        uniqueSources: complete.joints.every(joint => new Set(joint.members.map(item => item.sourceKey)).size
          === joint.members.length)};
    }""")
    assert result == {'singleCount': 1, 'withEmptyCount': 1, 'candidateCount': 0, 'completeCount': 100,
                      'edges': 99, 'completedWithoutPathWork': True, 'strictCount': 100, 'uniqueSources': True}


@pytest.mark.parametrize('model_wide', [False, True])
def test_reconciliation_cancels_at_collection_and_graph_checkpoints(reconciliation_page, model_wide):
    result = reconciliation_page.evaluate("""async modelWide => {
      const rigs = ['source-01', 'source-02'].map((key, source) => window.__sourceRig(key,
        Array.from({length: 12}, (_, i) => {
          const first = modelWide ? 1 : source * 100 + 1;
          return [first + i, [i, 0, 0], i ? first + i - 1 : null];
        }), modelWide));
      let totalCheckpoints = 0;
      const expected = await window.__buildRig(rigs, {},
        {budget: {checkpoint: async () => {totalCheckpoints += 1;}}});
      const cancelled = [];
      for (const cancelAt of [1, 2, Math.ceil(totalCheckpoints / 2), totalCheckpoints]) {
        let current = true, checkpoints = 0;
        const result = await window.__buildRig(rigs, {}, {isCurrent: () => current,
          budget: {checkpoint: async () => {
            await new Promise(resolve => setTimeout(resolve, 0));
            if (++checkpoints === cancelAt) current = false;
          }}});
        cancelled.push(result === null);
      }
      const recovered = await window.__buildRig(rigs);
      return {cancelled, graphCheckpoints: totalCheckpoints > 4,
        recovered: JSON.stringify(recovered) === JSON.stringify(expected)};
    }""", model_wide)
    assert result == {'cancelled': [True] * 4, 'graphCheckpoints': True, 'recovered': True}


def test_reconciliation_rejects_equal_endpoint_competition(reconciliation_page):
    result = reconciliation_page.evaluate("""async () => {
      const rigs = [window.__sourceRig('source-01', [[1, [0, 0, 0], null]]),
        window.__sourceRig('source-02', [[2, [0, 0, 0], null], [3, [0, 0, 0], null]])];
      const built = await window.__buildRig(rigs);
      const reversed = await window.__buildRig([...rigs].reverse());
      return {joints: built.joints.length, accepted: built.reconciliation.acceptedEquivalences.length,
        ambiguous: built.reconciliation.rejectedCandidates.some(item => item.rejectionReason === 'ambiguous'),
        stable: JSON.stringify(built) === JSON.stringify(reversed)};
    }""")
    assert result == {'joints': 3, 'accepted': 0, 'ambiguous': True, 'stable': True}


@pytest.mark.parametrize('parent', [3, 99])
def test_reconciliation_recovers_source_cycles_and_missing_parents(reconciliation_page, parent):
    result = reconciliation_page.evaluate("""async parent => {
      const rig = window.__sourceRig('source-01',
        [[1, [0, 0, 0], null], [2, [1, 0, 0], 1], [3, [2, 0, 0], 2]]);
      rig.inferredForest.components[0].parentById[1] = parent;
      const built = await window.__buildRig([rig]);
      const component = built.components[0];
      return {joints: built.joints.length, edges: built.edges.length, root: component.rootId,
        roots: Object.values(component.parentById).filter(value => value === null).length,
        reachable: component.nodeIds.every(id => Number.isFinite(component.depthById[id]))};
    }""", parent)
    assert result == {'joints': 3, 'edges': 2, 'root': 0, 'roots': 1, 'reachable': True}


@pytest.mark.parametrize('competing_host', [False, True])
@pytest.mark.parametrize('chained', [False, True])
def test_reconciliation_preserves_boundary_orientation_and_rejects_ambiguous_hosts(reconciliation_page, competing_host, chained):
    result = reconciliation_page.evaluate("""async ({competingHost, chained}) => {
      const {sourceBoneKey} = window.__reconciliation;
      const body = key => window.__sourceRig(key,
        [[10, [-2, 0, 0], null, 32], [11, [0, 0, 0], 10, 32], [12, [2, 0, 0], 11, 32]], false,
        key === 'source-02' ? [0, 1, 0] : [1, 0, 0]);
      const accessory = window.__sourceRig('source-03',
        [[20, [0, 0, 0.5], null, 4], [21, [0, 0, 0.08], 20, 4]], false, [0, 0, 1]);
      const rigs = [body('source-01'), accessory];
      if (competingHost) rigs.push(body('source-02'));
      if (chained) rigs.push(window.__sourceRig('source-04',
        [[30, [0, 0, 1], null], [31, [0, 0, 0.58], 30]]));
      const built = await window.__buildRig(rigs, {modelReferenceRadius: 2});
      const reversed = await window.__buildRig([...rigs].reverse(), {modelReferenceRadius: 2});
      const id = (key, bone) => built.sourceBoneToModelJointMap.get(sourceBoneKey(key, bone));
      const parent = (key, bone) => built.joints[id(key, bone)].parentId;
      return {attachments: built.reconciliation.attachmentCount,
        hostPreserved: parent('source-01', 11) === id('source-01', 10)
          && parent('source-01', 12) === id('source-01', 11),
        boundaryPreserved: competingHost ? parent('source-03', 21) === id('source-03', 20)
          : parent('source-03', 21) === id('source-01', 11) && parent('source-03', 20) === id('source-03', 21),
        chainPreserved: !chained || parent('source-04', 31) === id('source-03', 20)
          && parent('source-04', 30) === id('source-04', 31),
        ambiguity: built.reconciliation.attachmentDiagnostics.some(item => item.rejectionReason === 'attachment_ambiguous'),
        reachable: built.components.every(component => component.nodeIds.every(id => component.depthById[id] !== null)),
        stable: JSON.stringify(built.joints) === JSON.stringify(reversed.joints),
        edgeCount: built.edges.length};
    }""", {'competingHost': competing_host, 'chained': chained})
    assert result['attachments'] == (0 if competing_host else 1) + int(chained)
    assert result['hostPreserved'] and result['boundaryPreserved'] and result['chainPreserved']
    assert result['reachable'] and result['stable']
    assert result['ambiguity'] is competing_host
    assert result['edgeCount'] == (5 if competing_host else 4) + 2 * int(chained)


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


def test_source_rig_surface_failure_recovery_and_cache_lifecycle(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createRigSourceSession} = await import('./js/weight-rig/rig-model-session.js');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const sourceKey = 'fixture/stream.buf|offset=0';
      const otherKey = 'fixture/stream-02.buf|offset=0';
      const makeMesh = (name, boneId, source, triangles) => {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
        geometry.setIndex(triangles);
        const mesh = new THREE.Mesh(geometry);
        mesh.userData.semanticKey = name;
        const state = runtime.stateFor(mesh);
        Object.assign(state, {loaded: true, skinningSourceKey: source, influenceCount: 1,
          boneIds: [boneId], indices: new Uint32Array(3).fill(boneId), weights: new Float32Array([1,1,1])});
        runtime.knownMeshes.add(mesh);
        runtime.modelWeightState.sourceDescriptors.set(source, {sourceKey: source, sourceFile: 'fixture/stream.buf', boneIdOffset: 0});
        return mesh;
      };
      const first = makeMesh('mesh-01', 1, sourceKey, [0,1,2]);
      const duplicate = makeMesh('mesh-02', 1, sourceKey, [0,1,2]);
      const invalid = makeMesh('mesh-03', 2, sourceKey, [0,0,0]);
      const sourceSkinningRigs = new Map();
      let preparationCount = 0;
      const session = createRigSourceSession({...runtime, sourceSkinningRigs,
        ensureRigMeshPrepared: (...args) => { preparationCount++; return skin.ensureRigMeshPrepared(...args); },
        ensureInfluenceGraphCooperative: (...args) => skin.ensureInfluenceGraphCooperative(...args),
        rebuildRestFrames: () => {}, cloneForest: structuredClone});
      let rejected = false;
      try { await session.buildAllCooperative(); } catch (error) { rejected = error.message.includes('triangle geometry'); }
      const missing = {rejected, committed: sourceSkinningRigs.size, errors: Object.keys(session.getErrors())};
      const other = makeMesh('mesh-04', 3, otherKey, [0,1,2]);
      const partial = await session.buildAllCooperative();
      invalid.geometry.setIndex([0,1,2]);
      skin.rebaseAfterShapeChange(invalid);
      const members = [first, duplicate, invalid];
      const repaired = await session.ensureCooperative(sourceKey, members);
      const callsAfterBuild = preparationCount;
      const cached = await session.ensureCooperative(sourceKey, members);
      return {missing, partial: partial.map(rig => rig.sourceKey), sameObject: repaired === cached,
        callsAfterBuild, callsAfterCacheHit: preparationCount, errors: session.getErrors(),
        memberCount: repaired.influenceGraph.memberCount, uniqueMemberCount: repaired.influenceGraph.uniqueMemberCount,
        meshCount: repaired.meshes.size, vertexEvidenceCount: repaired.vertexEvidence.length,
        mode: repaired.influenceGraph.evidenceMode,
        weightsUsable: [...runtime.knownMeshes].every(mesh => runtime.stateFor(mesh).loaded),
        noVertexPreparation: [...runtime.knownMeshes].every(mesh => !('influenceNodes' in runtime.stateFor(mesh))),
        authoredWeights: [...runtime.stateFor(first).weights]};
    }""")
    assert result['missing'] == {'rejected': True, 'committed': 0, 'errors': ['fixture/stream.buf|offset=0']}
    assert result['partial'] == ['fixture/stream-02.buf|offset=0']
    assert result['sameObject'] and result['weightsUsable'] and result['noVertexPreparation']
    assert result['callsAfterCacheHit'] == result['callsAfterBuild']
    assert result['errors'] == {}
    assert result['memberCount'] == result['meshCount'] == result['vertexEvidenceCount'] == 3
    assert result['uniqueMemberCount'] == 2
    assert result['mode'] == 'surface'
    assert result['authoredWeights'] == [1, 1, 1]


def test_skinning_install_requires_canonical_wire_fields(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const {aggregateModelWeightBoneStats} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      const mesh = new THREE.Mesh(geometry);
      const buffer = new ArrayBuffer(24);
      new Uint32Array(buffer, 0, 3).set([1,2,1]);
      new Float32Array(buffer, 12, 3).set([1,0.5,1]);
      const entry = {vertex_count: 3, influence_count: 1, bone_ids: [1,2],
        source: {key: 'fixture/stream.buf|offset=0', file: 'fixture/stream.buf', bone_id_offset: 0},
        data: {indices: {offset: 0, length: 12, type: 'u32'}, weights: {offset: 12, length: 12, type: 'f32'}},
        weight_stats: {'1': {affected_vertex_count: 2, total_weight: 2}, '2': {affected_vertex_count: 1, total_weight: 0.5}}};
      const variants = [{bone_ids: undefined}, {bone_ids: null}, {bone_ids: [true]}, {bone_ids: [-1]},
        {bone_ids: [1.5]}, {weight_stats: {'1': {affectedVertexCount: 2, totalWeight: 2}}}];
      const rejected = variants.map(overrides => {
        try { skin.installSkinningEntry(mesh, {...entry, ...overrides}, buffer); return false; }
        catch { return !runtime.stateFor(mesh).loaded; }
      });
      skin.installSkinningEntry(mesh, entry, buffer);
      return {rejected, bones: runtime.stateFor(mesh).boneIds,
        stats: aggregateModelWeightBoneStats([runtime.stateFor(mesh).weightBoneStats])};
    }""")
    assert result['rejected'] == [True] * 6
    assert result['bones'] == [1, 2]
    assert result['stats'] == {'1': {'affectedVertexCount': 2, 'averageInfluence': 1},
                               '2': {'affectedVertexCount': 1, 'averageInfluence': 0.5}}


def test_surface_graph_rebaseline_discards_inflight_evidence(module_page):
    result = module_page.evaluate("""async () => {
      const THREE = await import('three');
      const {createSkinningRuntime} = await import('./js/weight-rig/skinning-runtime.js');
      const {createWeightRuntimeState} = await import('./js/weight-rig/weight-runtime.js');
      const runtime = createWeightRuntimeState();
      const skin = createSkinningRuntime({...runtime, requestRender: () => {}});
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
      geometry.setIndex([0,1,2]);
      const mesh = new THREE.Mesh(geometry);
      const state = runtime.stateFor(mesh);
      Object.assign(state, {loaded: true, indices: new Uint32Array([1,1,1]), weights: new Float32Array([1,1,1]),
        boneIds: [1], influenceCount: 1});
      let release, started;
      const ready = new Promise(resolve => started = resolve);
      const paused = new Promise(resolve => release = resolve);
      const old = skin.ensureInfluenceGraphCooperative(mesh, state, {budget: {checkpoint: async () => {started(); await paused;}}});
      await ready;
      skin.rebaseAfterShapeChange(mesh, {positions: new Float32Array([10,0,0, 11,0,0, 10,1,0])});
      const current = await skin.ensureInfluenceGraphCooperative(mesh, state);
      release();
      const obsolete = await old;
      const cached = await skin.ensureInfluenceGraphCooperative(mesh, state);
      return {obsolete: obsolete === null, cachePreserved: current === cached,
        center: current.nodes[0].weightedCenter, weights: [...state.weights]};
    }""")
    assert result['obsolete'] and result['cachePreserved']
    assert result['center'] == pytest.approx([10 + 1 / 3, 1 / 3, 0])
    assert result['weights'] == [1, 1, 1]


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
