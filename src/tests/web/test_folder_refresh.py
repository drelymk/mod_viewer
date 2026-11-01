"""Reindex refreshes cached folder classifications without loading Assets."""


def test_reindex_promotes_cached_category_and_preserves_other_roots(module_page):
    result = module_page.evaluate('''async () => {
      const {createFolderRegistryPanel} = await import('./js/panels/folder-registry-panel.js');
      const list = document.createElement('div'); document.body.appendChild(list);
      let indexed = false;
      const selected = [], requests = [];
      const roots = [{name:'Library',path:'library'}, {name:'Other',path:'other'}];
      const tree = createFolderRegistryPanel({listElement:list, classPrefix:'fixture',
        listChildren:async path => {
          requests.push(path);
          return {folders:[{name:'Asset',path:`${path}/asset`,asset:indexed}]};
        }, onChildSelected:(path,entry) => selected.push({path,asset:entry.asset})});
      const settle = () => new Promise(resolve => setTimeout(resolve,0));
      tree.render(roots);
      for (const node of [...list.children]) {
        node.querySelector('.fixture-expand').click(); await settle();
      }
      const select = () => list.querySelector('[data-fixture-path="library/asset"] .fixture-select').click();
      select();
      indexed = true;
      tree.updateRoot(roots[0]); await settle(); select();
      const otherBefore = list.children[1].querySelector('.fixture-children').firstChild;
      tree.updateRoot(roots[0], {refreshChildren:true}); await settle(); select();
      const otherPreserved = otherBefore === list.children[1].querySelector('.fixture-children').firstChild;
      return {selected, requests, otherPreserved,
        expanded:list.children[0].classList.contains('expanded')};
    }''')
    assert [entry['asset'] for entry in result['selected']] == [False, False, True]
    assert result['requests'] == ['library', 'other', 'library']
    assert result['otherPreserved'] and result['expanded']
