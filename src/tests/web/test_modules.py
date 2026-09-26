"""Ordered control replay independent of GPU availability."""


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
      const weights = await import('./js/mesh/weight-selection.js');
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
