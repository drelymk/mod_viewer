"""Asset menus and the shared extraction dialog use native-picked dump paths."""


def test_asset_add_update_dialog_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const html = await (await fetch('./index.html')).text();
      document.body.innerHTML = new DOMParser().parseFromString(html, 'text/html').body.innerHTML;
      const root = {path:'library',type:'ZZMI',index:{status:'ready'}};
      const asset = {path:'library/Asset_001',name:'Asset_001',asset:true};
      const calls = [], switches = [];
      let indexed = false, finish, fail = false;
      window.pywebview = {api:{
        get_asset_folders:async () => ({folders:[root]}),
        list_asset_subfolders:async () => ({folders:indexed ? [asset] : []}),
        select_asset_dump_folder:async () => 'capture',
        extract_asset:async (...args) => {
          calls.push(args);
          await new Promise(resolve => {finish = resolve;});
          if (fail) return {error:'synthetic failure'};
          indexed = true;
          return {folders:[root],path:asset.path,warnings:[],
            extraction:{components:2,parts:3,partial:calls.length > 1,skipped:calls.length > 1 ? 1 : 0}};
        }
      }};
      const {initAssetFolderPanel} = await import('./js/panels/asset-folder-panel.js');
      initAssetFolderPanel({
        getCurrentSource:() => ({kind:'asset',path:asset.path}),
        switchAsset:async (path,entry) => {switches.push([path,entry]);return true;}
      });
      const settle = () => new Promise(resolve => setTimeout(resolve,0));
      await settle();
      const list = document.getElementById('asset-folder-list');
      const menuAction = (row, label) => {
        row.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true}));
        const button = [...document.querySelectorAll('.asset-folder-action-menu button')]
          .find(button => button.textContent === label && !button.parentElement.hidden);
        if (!button) throw Error('Missing menu action: ' + label);
        button.click();
      };
      const $ = id => document.getElementById('asset-extract-' + id);
      menuAction(list.querySelector('.asset-folder-row'), 'Add asset');
      const addOpened = $('backdrop').classList.contains('show');
      $('name').value = 'Asset_001';
      $('form').dispatchEvent(new Event('submit', {cancelable:true}));
      await settle();
      const required = $('error').textContent.length > 0 && calls.length === 0;
      $('browse').click(); await settle();
      $('form').dispatchEvent(new Event('submit', {cancelable:true})); await settle();
      $('cancel').click(); document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape'}));
      $('form').dispatchEvent(new Event('submit', {cancelable:true}));
      const busy = $('ok').disabled && $('cancel').disabled && $('backdrop').classList.contains('show') && calls.length === 1;
      finish(); await settle();
      const added = !$('backdrop').classList.contains('show');
      const notice = document.getElementById('asset-folder-notice');
      const saved = !notice.hidden && notice.textContent.includes('2 components') && !notice.classList.contains('warning');
      list.querySelector('.asset-folder-expand').click(); await settle();
      const child = () => list.querySelector('[data-asset-folder-path="library/Asset_001"]');
      menuAction(child(), 'Update asset');
      const prefilled = $('name').value;
      $('browse').click(); await settle();
      fail = true;
      $('form').dispatchEvent(new Event('submit', {cancelable:true})); await settle();
      finish(); await settle();
      const failed = $('error').textContent === 'synthetic failure' && $('backdrop').classList.contains('show') && !$('ok').disabled;
      fail = false;
      $('form').dispatchEvent(new Event('submit', {cancelable:true})); await settle();
      finish(); await settle();
      const partialSaved = !notice.hidden && notice.classList.contains('warning') && notice.textContent.includes('Skipped resource groups: 1');
      const noError = !document.getElementById('asset-folder-error').classList.contains('show');
      return {addOpened,required,busy,added,saved,partialSaved,noError,prefilled,failed,calls,switches,
        closed:!$('backdrop').classList.contains('show'), assetRefreshed:!!child()};
    }''')
    assert all(result[key] for key in (
        'addOpened', 'required', 'busy', 'added', 'saved', 'partialSaved', 'noError',
        'failed', 'closed', 'assetRefreshed'))
    assert result['prefilled'] == 'Asset_001'
    assert result['switches'] == [['library/Asset_001', {'asset': True}]]
    assert result['calls'] == [
        ['library', 'Asset_001', 'capture', None],
        ['library', 'Asset_001', 'capture', 'library/Asset_001'],
        ['library', 'Asset_001', 'capture', 'library/Asset_001']]
