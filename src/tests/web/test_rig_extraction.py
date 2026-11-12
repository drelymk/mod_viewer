def test_asset_rig_extraction_menu_and_dialog_lifecycle(module_page):
    result = module_page.evaluate('''async () => {
      const html = await (await fetch('./index.html')).text();
      document.body.innerHTML = new DOMParser().parseFromString(html,'text/html').body.innerHTML;
      const style = document.createElement('link');style.rel='stylesheet';style.href='./css/app.css';
      await new Promise((resolve,reject)=>{style.onload=resolve;style.onerror=reject;document.head.append(style);});
      const {applyTranslations}=await import('./js/i18n/index.js');applyTranslations(document);
      const root={path:'library',type:'WWMI',index:{status:'ready'}};
      const asset={path:'library/Character01',name:'Character01',asset:true,asset_type:'WWMI'};
      const unsupported={path:'library/Character02',name:'Character02',asset:true,asset_type:'GIMI'};
      const calls=[],switches=[];let response={status:'running',phase:'rig',current:1,total:2};
      let pick={token:'game01',path:'game-folder'},pickResolve=null;
      window.pywebview={api:{
        get_asset_folders:async()=>({folders:[root]}),list_asset_subfolders:async()=>({folders:[asset,unsupported]}),
        select_game_folder:async()=>pickResolve?await new Promise(r=>{pickResolve=r;}):pick,
        start_asset_rig_extraction:async(...args)=>{calls.push(args);return {job_id:'job01',status:'running'};},
        get_asset_rig_extraction:async()=>response,
        cancel_asset_rig_extraction:async()=>{response={status:'cancelled',error:'cancelled'};return {cancelled:true};},
      }};
      const {initAssetFolderPanel}=await import('./js/panels/asset-folder-panel.js');
      initAssetFolderPanel({getCurrentSource:()=>({kind:'asset',path:asset.path}),
        switchAsset:async(...args)=>{switches.push(args);return true;}});
      const settle=()=>new Promise(r=>setTimeout(r,0));
      const wait=async condition=>{for(let n=0;n<100&&!condition();n++)await new Promise(r=>setTimeout(r,10));if(!condition())throw Error('UI state did not settle');};
      await settle();
      const list=document.getElementById('asset-folder-list');
      const menu=row=>{row.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));
        return [...document.querySelectorAll('.asset-folder-action-menu:not([hidden]) button')];};
      const rootHasNoAction=!menu(list.querySelector('.asset-folder-row')).some(b=>b.textContent==='Extract Rig/Animation');
      list.querySelector('.asset-folder-expand').click();await settle();
      const row=path=>list.querySelector(`[data-asset-folder-path="${path}"]`);
      const unsupportedHasNoAction=!menu(row(unsupported.path)).some(b=>b.textContent==='Extract Rig/Animation');
      const open=()=>menu(row(asset.path)).find(b=>b.textContent==='Extract Rig/Animation').click();
      const $=name=>document.getElementById('rig-extract-'+name);
      open();
      const opened=$('backdrop').classList.contains('show')&&$('query').value==='Character01'&&$('asset').value===asset.path;
      const readOnly=$('asset').readOnly&&$('game').readOnly;
      const hint=$('hint').textContent.includes('installation folder')&&$('hint').textContent.includes('individual archive');
      const matchingStyle=getComputedStyle($('backdrop').querySelector('.modal')).width===
        getComputedStyle(document.querySelector('#asset-extract-backdrop .modal')).width&&
        getComputedStyle($('ok')).backgroundColor===getComputedStyle(document.getElementById('asset-extract-ok')).backgroundColor;
      $('form').dispatchEvent(new Event('submit',{cancelable:true}));await settle();
      const required=calls.length===0&&!!$('error').textContent;
      $('browse').click();await settle();
      $('form').dispatchEvent(new Event('submit',{cancelable:true}));await settle();
      $('form').dispatchEvent(new Event('submit',{cancelable:true}));
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
      const busy=$('ok').disabled&&$('query').disabled&&$('backdrop').classList.contains('show')&&calls.length===1;
      response={status:'error',phase:'rig',current:0,total:1,error:'synthetic failure'};
      await wait(()=>!$('ok').disabled);
      const failed=$('backdrop').classList.contains('show')&&$('error').textContent==='synthetic failure'&&$('game').value==='game-folder'&&!$('progress').textContent;
      response={status:'running',phase:'convert'};
      $('ok').click();await settle();
      response={status:'running',phase:'install'};await wait(()=>$('cancel').disabled);
      document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
      const installing=$('backdrop').classList.contains('show')&&$('cancel').disabled;
      response={status:'running',phase:'convert'};await wait(()=>!$('cancel').disabled);$('cancel').click();
      await wait(()=>!$('ok').disabled);
      const cancelled=$('error').textContent==='cancelled'&&switches.length===0;
      response={status:'complete',phase:'complete',result:{bones:2,clips:3,secondary:1,skipped:0}};
      $('ok').click();await settle();
      const saved=!$('backdrop').classList.contains('show')&&switches.length===1&&
        document.getElementById('asset-folder-notice').textContent.includes('2 bones and 3 animation clips');
      open();pick=null;$('browse').click();await settle();
      const pickerCancel=$('game').value==='';
      pickResolve=true;$('browse').click();await settle();document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));
      pickResolve({token:'stale',path:'stale-path'});await settle();
      const stalePicker=!$('backdrop').classList.contains('show')&&$('game').value==='';
      return {rootHasNoAction,unsupportedHasNoAction,opened,readOnly,hint,matchingStyle,required,busy,failed,installing,cancelled,saved,pickerCancel,stalePicker,calls,switches};
    }''')
    assert all(result[key] for key in (
        'rootHasNoAction', 'unsupportedHasNoAction', 'opened', 'readOnly', 'hint', 'matchingStyle',
        'required', 'busy', 'failed', 'installing', 'cancelled', 'saved', 'pickerCancel', 'stalePicker')), result
    assert result['calls'] == [['library/Character01', 'game01', 'Character01']] * 3
    assert result['switches'] == [['library/Character01', {'asset': True}]]


def test_settings_game_folders_save_and_prefill_extraction(module_page):
    result = module_page.evaluate('''async () => {
      const html = await (await fetch('./index.html')).text();
      document.body.innerHTML = new DOMParser().parseFromString(html,'text/html').body.innerHTML;
      let folders = {WWMI:'wwmi-game'};const picks=[],clears=[];
      window.pywebview={api:{
        get_viewer_preferences:async()=>({value:{}}),
        get_game_folders:async()=>({value:{...folders}}),
        select_game_directory:async kind=>{picks.push(kind);folders={...folders,[kind]:kind+'-new'};return {value:{...folders}};},
        clear_game_directory:async kind=>{clears.push(kind);delete folders[kind];return {value:{...folders}};},
        get_saved_game_folder:async()=>({token:'saved01',path:'wwmi-game'}),
      }};
      const {initSettingsDialog}=await import('./js/ui/settings.js');
      initSettingsDialog();
      const settle=()=>new Promise(r=>setTimeout(r,20));await settle();
      const row=kind=>document.querySelector(`.settings-game-row[data-game-kind="${kind}"]`);
      const kinds=[...document.querySelectorAll('.settings-game-row')].map(r=>r.dataset.gameKind);
      const initial=[row('WWMI').querySelector('input').value,row('GIMI').querySelector('input').value,
        row('WWMI').querySelector('.settings-game-clear').hidden,row('GIMI').querySelector('.settings-game-clear').hidden];
      row('GIMI').querySelector('.settings-game-select').click();await settle();
      const selected=row('GIMI').querySelector('input').value;
      row('WWMI').querySelector('.settings-game-clear').click();await settle();
      const cleared=row('WWMI').querySelector('input').value;
      const {initRigExtractionDialog}=await import('./js/panels/rig-extraction-dialog.js');
      const dialog=initRigExtractionDialog({onSaved(){}});
      dialog.open({path:'library/Character01',name:'Character01'});await settle();
      return {kinds,initial,selected,cleared,picks,clears,prefilled:document.getElementById('rig-extract-game').value};
    }''')
    assert result['kinds'] == ['GIMI', 'ZZMI', 'WWMI']
    assert result['initial'] == ['wwmi-game', '', False, True]
    assert result['selected'] == 'GIMI-new' and result['cleared'] == ''
    assert result['picks'] == ['GIMI'] and result['clears'] == ['WWMI']
    assert result['prefilled'] == 'wwmi-game'


def test_settings_index_prompt_after_selecting_game_and_reused_by_index_button(module_page):
    result = module_page.evaluate("""async () => {
      const html = await (await fetch('./index.html')).text();
      document.body.innerHTML = new DOMParser().parseFromString(html,'text/html').body.innerHTML;
      const {applyTranslations}=await import('./js/i18n/index.js');applyTranslations(document);
      let folders={},state={state:'missing',meshes:0},job={status:'running',phase:'index',current:1,total:4};
      const starts=[],cancels=[];
      window.pywebview={api:{
        get_viewer_preferences:async()=>({value:{}}),
        get_game_folders:async()=>({value:{...folders}}),
        select_game_directory:async kind=>{folders={...folders,[kind]:kind+'-new'};return {value:{...folders}};},
        clear_game_directory:async kind=>{delete folders[kind];return {value:{...folders}};},
        get_game_index_status:async()=>state,
        start_game_index:async()=>{starts.push(1);return {job_id:'idx01',status:'running'};},
        get_game_index_job:async()=>job,
        cancel_game_index:async()=>{cancels.push(1);job={status:'cancelled',error:'cancelled'};return {cancelled:true};},
      }};
      const {initSettingsDialog}=await import('./js/ui/settings.js');
      initSettingsDialog();
      const settle=()=>new Promise(r=>setTimeout(r,30));
      const wait=async condition=>{for(let n=0;n<200&&!condition();n++)await new Promise(r=>setTimeout(r,10));if(!condition())throw Error('UI state did not settle');};
      await settle();
      const row=kind=>document.querySelector(`.settings-game-row[data-game-kind="${kind}"]`);
      const backdrop=document.getElementById('game-index-backdrop');
      const button=row('WWMI').querySelector('.settings-game-index');
      const hiddenWithoutPath=button.hidden;
      row('GIMI').querySelector('.settings-game-select').click();await settle();
      const noPromptForOtherGames=!backdrop.classList.contains('show');
      row('WWMI').querySelector('.settings-game-select').click();await wait(()=>backdrop.classList.contains('show'));
      const status=document.querySelector('.settings-game-index-status');
      const prompted=!button.hidden&&status.textContent==='Not indexed';
      document.getElementById('game-index-cancel').click();await settle();
      const dismissed=!backdrop.classList.contains('show')&&starts.length===0;
      button.click();await settle();
      state={state:'ready',meshes:7};job={status:'complete',phase:'complete'};
      document.getElementById('game-index-ok').click();
      await wait(()=>!backdrop.classList.contains('show'));await settle();
      const indexed=starts.length===1&&status.textContent==='Indexed · 7 meshes';
      job={status:'running',phase:'index',current:1,total:4};
      button.click();await settle();
      document.getElementById('game-index-ok').click();
      await wait(()=>document.getElementById('game-index-progress').textContent.includes('(1/4)'));
      document.getElementById('game-index-cancel').click();
      await wait(()=>!backdrop.classList.contains('show'));
      return {hiddenWithoutPath,noPromptForOtherGames,prompted,dismissed,indexed,cancelled:cancels.length===1};
    }""")
    assert all(result.values()), result
