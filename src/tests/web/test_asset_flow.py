from .support import *

def test_asset_identity_and_texture_provenance_are_diagnostic_only(
        edge_browser, frontend_url):
    payload = _payload("Asset")
    entry = payload["meshes"]["Body-Asset-0"]
    entry["asset_binding"] = {
        "status": "exact", "component_status": "exact",
        "range_status": "exact", "asset_type": "GIMI",
        "asset": "Alice", "component_name": "Body",
        "classification": "B", "geometry_hash": "73c8cae2",
        "first_index": 43845, "index_count": 24,
    }
    entry["texture_resolution"] = {
        "diffuse": "mod_slot_semantic",
        "normal_map": "asset_original_fallback",
        "light_map": "mod_texture_hash",
    }
    entry["asset_slot_evidence"] = [{
        "resource": "ps-t1", "texture_hash": "11111111",
        "vs_hash": "aaaaaaaa", "ps_hash": "bbbbbbbb",
    }, {
        "resource": "ps-t2", "texture_hash": "22222222",
        "role": "diffuse", "role_source": "mod_slot_mapping",
        "asset_hash_role": "normal_map", "conflict": True,
    }]
    payload["asset_resolution"] = {
        "total_draws": 1, "exact_draws": 1, "partial_draws": 0,
        "ambiguous_draws": 0, "unmatched_draws": 0,
        "index_unavailable_draws": 0, "index_status": "ready",
        "components": [],
    }
    context, page = _page(edge_browser, frontend_url, {"Asset": payload})
    try:
        _open(page, "Asset")
        page.locator(".draw-item").wait_for()
        assert page.locator(".asset-draw-label").inner_text() == "Alice · Body B"
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        assert page.locator("#health-asset-summary").inner_text() == (
            "Asset resolution: 1 / 1 draws exact")
        page.locator("#health-close").click()

        summary = page.evaluate("""async () => {
          const {summarizeAssetBindings} = await import('./js/panels/asset-diagnostics.js');
          return summarizeAssetBindings([
            {asset_binding: {
              status: 'exact', component_status: 'exact', range_status: 'exact',
              asset: 'Alice', component_name: 'Body', classification: 'A',
            }},
            {asset_binding: {
              status: 'exact', component_status: 'exact', range_status: 'exact',
              asset: 'Alice', component_name: 'Body', classification: 'A',
            }},
            {asset_binding: {
              status: 'not_found', component_status: 'not_found',
              range_status: 'unknown',
            }},
          ]);
        }""")
        assert summary["status"] == "partial"
        assert summary["assets"] == ["Alice"]
        assert summary["matchedDraws"] == 2
        assert summary["rangesVary"] is False

        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        inspector = page.locator("#inspector-content")
        assert inspector.locator(".inspector-asset-section").count() == 0
        assert inspector.locator(".inspector-slot-section").count() == 0
        assert "Asset resolution" not in inspector.inner_text()
        assert "Texture provenance" not in inspector.inner_text()
        assert "Slot evidence" not in inspector.inner_text()

        page.locator(".inspector-texture-option", has_text="Asset two").click()
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manualTexOverride") == (
                "diffuse::Asset-two.png")
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        assert page.locator("#health-asset-summary").inner_text() == (
            "Asset resolution: 1 / 1 draws exact")
        page.locator("#health-close").click()

        page.locator(".group-hdr .group-name").first.click()
        assert "Asset resolution" not in inspector.inner_text()
    finally:
        context.close()

def test_asset_diagnostics_refresh_with_semantic_updates(
        edge_browser, frontend_url):
    payload = _payload("Semantic")
    entry = payload["meshes"]["Body-Semantic-0"]
    entry["asset_binding"] = {
        "status": "exact", "component_status": "exact",
        "range_status": "exact", "asset_type": "GIMI",
        "asset": "Alice", "component_name": "Body",
        "geometry_hash": "73c8cae2",
    }
    entry["texture_resolution"] = {"normal_map": "asset_original_fallback"}
    payload["asset_resolution"] = {
        "total_draws": 1, "exact_draws": 1, "index_status": "ready",
        "configured_roots": 1, "ready_roots": 1,
    }
    context, page = _page(edge_browser, frontend_url, {"Semantic": payload})
    try:
        _open(page, "Semantic")
        page.locator(".draw-item").wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        inspector = page.locator("#inspector-content")
        assert inspector.locator(".inspector-asset-section").count() == 0

        page.evaluate("""() => {
          const state = window.__fakeApi;
          const entry = state.responses.Semantic.meshes['Body-Semantic-0'];
          state.responses.Semantic.meshSemantics = {
            'Body-Semantic-0': {
              conditions: entry.conditions || [], sources: entry.sources || [],
              tex_key: entry.tex_key, normal_map_key: null,
              normal_data_key: null, light_map_key: null,
              material_map_key: null,
              asset_binding: {
                status: 'not_found', component_status: 'not_found',
                range_status: 'unknown', asset_type: 'GIMI',
              },
              texture_resolution: {normal_map: 'mod_semantic'},
              asset_slot_evidence: [],
            },
          };
          state.responses.Semantic.meshSemanticsAssetResolution = {
            total_draws: 1, exact_draws: 0, partial_draws: 0,
            ambiguous_draws: 0, unmatched_draws: 1,
            index_unavailable_draws: 0, index_status: 'ready',
            configured_roots: 1, ready_roots: 1,
          };
        }""")
        page.evaluate("window.modViewer.refreshMeshSemantics()")
        page.wait_for_function(
            "document.querySelector('#inspector-content .inspector-material-kind-control')")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.assetEntry"
            ".asset_binding.status === 'not_found'")
        assert page.locator(".asset-draw-label").count() == 0
        assert inspector.locator(".inspector-asset-section").count() == 0
        assert "Not found" not in inspector.inner_text()
        assert page.locator(".asset-component-label").inner_text() == (
            "Asset: Partial")
        page.locator("#health-btn").click()
        page.locator("#health-modal-backdrop.show").wait_for()
        health_asset_summary = page.locator("#health-asset-summary").inner_text()
        assert "Asset resolution: 0 / 1 draws exact" in health_asset_summary
        assert "1 not found" in health_asset_summary
    finally:
        context.close()

def test_assets_panel_uses_badges_and_lazy_browse_only_children(
        edge_browser, frontend_url):
    root = "fixture-assets"
    child = root + r"\Character"
    context, page = _page(
        edge_browser, frontend_url, {},
        asset_folders=[{
            "type": "GIMI", "path": root, "exists": True, "enabled": False,
        }],
        asset_subfolders={root: [{"name": "Character", "path": child}]},
    )
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-list .asset-folder-select").first.wait_for()
        assert "GIMI" in page.locator("#asset-folder-list").first.inner_text()
        assert page.locator(".asset-folder-status").count() == 0
        assert page.locator(
            ".asset-folder-row.asset-folder-disabled .asset-folder-label").count() == 1
        page.locator("#asset-folder-list .asset-folder-expand").first.click()
        page.locator(".asset-folder-select", has_text="Character").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listAssetSubfolders") == [root]
        child_select = page.locator(".asset-folder-select", has_text="Character")
        child_select.click()
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        switch = page.locator(".asset-folder-switch").first
        assert switch.get_attribute("aria-checked") == "false"
        switch.click()
        page.wait_for_function("window.__fakeApi.assetFolders[0].enabled === true")
        page.locator(".asset-folder-switch[aria-checked='true']").wait_for()
        child_select.wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        root_arrow = page.locator(
            "#asset-folder-list > .asset-folder-node > .asset-folder-row .asset-folder-expand")
        root_children = page.locator(
            "#asset-folder-list > .asset-folder-node > .asset-folder-children")
        root_arrow.click()
        assert root_children.is_hidden()
        root_arrow.click()
        assert not root_children.is_hidden()
        page.locator(".asset-folder-switch").first.click()
        page.wait_for_function("window.__fakeApi.assetFolders[0].enabled === false")
        page.locator(".asset-folder-switch[aria-checked='false']").wait_for()
        child_select.wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        root_select = page.locator("#asset-folder-list .asset-folder-select").first
        root_select.click()
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == root
        switch = page.locator(".asset-folder-switch").first
        page.evaluate("""() => {
          window.pywebview.api.set_asset_folder_enabled = async () => ({
            error: 'write failed'});
        }""")
        page.locator(".asset-folder-switch").first.click()
        page.locator("#asset-folder-error.show").wait_for()
        assert page.locator(".asset-folder-switch").first.get_attribute(
            "aria-checked") == "false"
        page.evaluate("""() => {
          window.pywebview.api.set_asset_folder_enabled = async (path, enabled) => {
            const item = window.__fakeApi.assetFolders.find(folder => folder.path === path);
            if (item) item.enabled = enabled;
            return {folders: window.__fakeApi.assetFolders.map(folder => ({...folder}))};
          };
        }""")
        page.locator(".asset-folder-switch").first.click()
        page.wait_for_function(
            "!document.querySelector('#asset-folder-error').classList.contains('show')")
        page.locator(".asset-folder-switch[aria-checked='true']").wait_for()
        page.locator("#asset-folder-add").click()
        assert page.locator("#afm-type option").all_inner_texts() == ["ZZMI", "GIMI", "WWMI"]
        page.evaluate("window.__fakeApi.nextPath = 'picked-asset-folder'")
        page.locator("#afm-browse").click()
        assert page.locator("#afm-path").input_value() == "picked-asset-folder"
        assert page.evaluate("window.__fakeApi.calls.selectAssetFolder") == [
            "picked-asset-folder"]
        assert page.locator(".asset-folder-path-field").count() == 1
        assert page.locator("#afm-save").evaluate(
            "button => getComputedStyle(button).backgroundColor") == "rgb(35, 134, 54)"
        assert page.locator("#afm-cancel").evaluate(
            "button => getComputedStyle(button).backgroundColor") == "rgb(33, 38, 45)"
    finally:
        context.close()

def test_indexed_asset_row_loads_read_only_preview(edge_browser, frontend_url):
    root = "fixture-assets"
    child = root + r"\Character"
    payload = _payload("Asset")
    payload["metadata"].update({
        "source_kind": "asset",
        "asset": {"type": "ZZMI", "path": child},
    })
    context, page = _page(
        edge_browser, frontend_url, {child: payload},
        asset_folders=[{"type": "ZZMI", "path": root, "exists": True}],
        asset_subfolders={root: [{
            "name": "Character", "path": child, "asset": True, "asset_type": "ZZMI",
        }]},
    )
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-list .asset-folder-expand").first.click()
        child_select = page.locator(".asset-folder-select", has_text="Character")
        child_select.wait_for()
        child_select.click()
        page.wait_for_function("window.__fakeApi.calls.loadAsset.length === 1")
        assert page.evaluate("window.__fakeApi.calls.loadAsset") == [child]
        assert page.locator(".draw-item").count() == 1
        assert page.locator("body.asset-preview-mode").count() == 1
        assert page.locator("#controls-tab").is_hidden()
        assert page.locator("#export-btn").is_hidden()
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []
        assert page.evaluate("window.modViewer.getCurrentSource().kind") == "asset"
    finally:
        context.close()

def test_missing_asset_parts_append_and_remove_without_reloading_mod(
        edge_browser, frontend_url):
    mod_payload = _payload("FillMod")
    mod_payload["asset_resolution"] = {"configured_roots": 1}
    fill_payload = _payload("FillAsset")
    fill_entry = next(iter(fill_payload["meshes"].values()))
    fill_entry["source"] = "ORIGINAL ASSET"
    fill_entry["component"] = "Original Hair"
    fill_entry["asset_fill"] = True
    fill_entry["fill_reason"] = "missing_mod_coverage"
    fill_entry["sources"] = [{"asset": {
        "type": "ZZMI", "asset": "Character", "geometry_hash": "bbbbbbbb",
        "component_name": "Hair", "first_index": 0, "index_count": 3,
    }}]
    fill_payload["metadata"] = {
        "source_kind": "asset-fill",
        "material_profiles": {},
    }
    mod_payload["assetFillResponse"] = {
        "status": "loaded",
        "fill_id": "fill-FillAsset",
        "coverage": {
            "asset_parts": 2, "handled_parts": 1,
            "missing_parts": 1, "skipped_parts": 0,
        },
        "payload": fill_payload,
    }
    context, page = _page(
        edge_browser, frontend_url, {"FillMod": mod_payload})
    try:
        _open(page, "FillMod")
        page.locator(".draw-item").wait_for()
        button = page.locator("#asset-fill-btn")
        assert not button.is_disabled()
        assert button.inner_text() == ""
        assert button.get_attribute("data-state") == "load"
        assert button.get_attribute("aria-label") == "Load missing parts"
        assert button.get_attribute("aria-pressed") == "false"
        assert button.locator("use").get_attribute("href") == "#icon-mesh-add"

        button.click()
        page.wait_for_function(
            "window.__fakeApi.calls.loadMissingAssetParts.length === 1")
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        assert button.get_attribute("aria-label") == "Remove missing parts"
        assert button.get_attribute("aria-pressed") == "true"
        assert button.locator("use").get_attribute("href") == "#icon-close"
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        assert page.evaluate("window.modViewer.refreshMeshSemantics()") is True
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        appended = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          position: mesh.position.toArray(), quaternion: mesh.quaternion.toArray(),
        }))""")
        assert appended[1]["position"] == pytest.approx(appended[0]["position"])
        assert appended[1]["quaternion"] == pytest.approx(
            appended[0]["quaternion"])
        assert page.locator(".mesh-src-hdr", has_text="ORIGINAL ASSET").count() == 1
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["FillMod"]

        button.click()
        page.wait_for_function(
            "window.__fakeApi.calls.removeMissingAssetParts.length === 1")
        page.locator("#asset-fill-btn[data-state='load']").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes.length") == 1
        assert page.locator(".mesh-src-hdr", has_text="ORIGINAL ASSET").count() == 0
        assert page.evaluate("window.__fakeApi.calls.loadMod") == ["FillMod"]

        page.locator("#camera-flip-btn").click()
        page.locator("#camera-flip-horizontal-btn").click()
        page.locator("#asset-fill-btn[data-state='load']").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        turned = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          position: mesh.position.toArray(), quaternion: mesh.quaternion.toArray(),
        }))""")
        assert turned[1]["position"] == pytest.approx(turned[0]["position"])
        assert turned[1]["quaternion"] == pytest.approx(turned[0]["quaternion"])
        page.locator("#camera-reset-view-btn").click()
        reset = page.evaluate("""() => window.modViewer.activeMeshes.map(mesh => ({
          position: mesh.position.toArray(), quaternion: mesh.quaternion.toArray(),
        }))""")
        assert reset[1]["position"] == pytest.approx(reset[0]["position"])
        assert reset[1]["quaternion"] == pytest.approx(reset[0]["quaternion"])

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")
        assert page.locator("#asset-fill-btn").get_attribute("data-state") == "load"
        assert page.evaluate("window.modViewer.activeMeshes.length") == 1
    finally:
        context.close()

def test_stale_missing_asset_response_is_rolled_back_after_mod_switch(
        edge_browser, frontend_url):
    first = _payload("FillRaceA")
    first["asset_resolution"] = {"configured_roots": 1}
    _add_asset_fill_response(first, "FillRaceAsset")
    second = _payload("FillRaceB")
    context, page = _page(
        edge_browser, frontend_url, {"FillRaceA": first, "FillRaceB": second})
    try:
        _open(page, "FillRaceA")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const original = window.pywebview.api.load_missing_asset_parts;
          window.__releaseFill = null;
          window.pywebview.api.load_missing_asset_parts = async path => {
            const result = await original(path);
            await new Promise(resolve => window.__releaseFill = resolve);
            return result;
          };
          document.getElementById('asset-fill-btn').click();
        }""")
        page.wait_for_function("window.__releaseFill !== null")

        _open(page, "FillRaceB")
        page.locator(".draw-item").wait_for()
        page.evaluate("window.__releaseFill()")

        page.wait_for_function(
            "window.__fakeApi.calls.removeMissingAssetParts.length === 1")
        assert page.evaluate("window.modViewer.getCurrentSource().path") == "FillRaceB"
        assert page.evaluate("window.modViewer.activeMeshes.length") == 1
    finally:
        context.close()

def test_stale_missing_asset_response_preserves_new_mod_fill(
        edge_browser, frontend_url):
    first = _payload("FillRaceA")
    _add_asset_fill_response(first, "FillRaceAssetA")
    second = _payload("FillRaceB")
    _add_asset_fill_response(second, "FillRaceAssetB")
    context, page = _page(
        edge_browser, frontend_url, {"FillRaceA": first, "FillRaceB": second})
    try:
        _open(page, "FillRaceA")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const original = window.pywebview.api.load_missing_asset_parts;
          window.__releaseFill = null;
          window.pywebview.api.load_missing_asset_parts = async path => {
            const result = await original(path);
            if (path === 'FillRaceA') {
              await new Promise(resolve => window.__releaseFill = resolve);
            }
            return result;
          };
          document.getElementById('asset-fill-btn').click();
        }""")
        page.wait_for_function("window.__releaseFill !== null")

        _open(page, "FillRaceB")
        page.locator(".draw-item").wait_for()
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2

        page.evaluate("window.__releaseFill()")
        page.wait_for_function(
            "window.__fakeApi.calls.removeMissingAssetParts.includes('FillRaceA')")
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        assert page.locator("#asset-fill-btn").get_attribute("data-state") == "remove"
        assert page.locator("#asset-fill-btn").get_attribute("aria-pressed") == "true"
    finally:
        context.close()

def test_stale_missing_asset_remove_preserves_new_mod_fill(
        edge_browser, frontend_url):
    first = _payload("FillRemoveA")
    _add_asset_fill_response(first, "FillRemoveAssetA")
    second = _payload("FillRemoveB")
    _add_asset_fill_response(second, "FillRemoveAssetB")
    context, page = _page(
        edge_browser, frontend_url,
        {"FillRemoveA": first, "FillRemoveB": second})
    try:
        _open(page, "FillRemoveA")
        page.locator(".draw-item").wait_for()
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()

        page.evaluate("""() => {
          const original = window.pywebview.api.remove_missing_asset_parts;
          window.__releaseRemove = null;
          window.pywebview.api.remove_missing_asset_parts = async path => {
            const result = await original(path);
            if (path === 'FillRemoveA') {
              await new Promise(resolve => window.__releaseRemove = resolve);
            }
            return result;
          };
          document.getElementById('asset-fill-btn').click();
        }""")
        page.wait_for_function("window.__releaseRemove !== null")

        _open(page, "FillRemoveB")
        page.locator(".draw-item").wait_for()
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2

        page.evaluate("window.__releaseRemove()")
        page.wait_for_function(
            "window.__fakeApi.calls.removeMissingAssetParts.includes('FillRemoveA')")
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        assert page.locator("#asset-fill-btn").get_attribute("data-state") == "remove"
        assert page.locator("#asset-fill-btn").get_attribute("aria-pressed") == "true"
    finally:
        context.close()


def _add_asset_fill_response(payload, label, position=None):
    payload["asset_resolution"] = {"configured_roots": 1}
    fill_payload = _payload(label)
    fill_entry = next(iter(fill_payload["meshes"].values()))
    if position is not None:
        fill_entry["pos"] = position
    fill_entry.update({
        "source": "ORIGINAL ASSET",
        "component": "Original Hair",
        "asset_fill": True,
        "fill_reason": "missing_mod_coverage",
        "sources": [{"asset": {
            "type": "ZZMI", "asset": "Character", "geometry_hash": "bbbbbbbb",
            "component_name": "Hair", "first_index": 0, "index_count": 3,
        }}],
    })
    fill_payload["metadata"] = {
        "source_kind": "asset-fill", "material_profiles": {},
    }
    payload["assetFillResponse"] = {
        "status": "loaded",
        "fill_id": f"fill-{label}",
        "coverage": {
            "asset_parts": 2, "handled_parts": 1,
            "missing_parts": 1, "skipped_parts": 0,
        },
        "payload": fill_payload,
    }

def test_missing_asset_parts_inherit_auto_upright_and_wuwa_facing(
        edge_browser, frontend_url):
    upright = _payload("Upright")
    upright_position = _f32(0, 0, 0, 1, 0, 0, 0, 0, 2)
    upright["meshes"]["Body-Upright-0"]["pos"] = upright_position
    _add_asset_fill_response(upright, "UprightAsset", upright_position)

    wuwa = _payload("WuWaFill")
    wuwa["metadata"]["game"] = {
        "id": "wuwa", "runtime": "wwmi", "texture_api": "raw",
        "confidence": "high",
    }
    _add_asset_fill_response(wuwa, "WuWaAsset")

    context, page = _page(
        edge_browser, frontend_url, {"Upright": upright, "WuWa": wuwa})
    try:
        _open(page, "Upright")
        page.locator(".draw-item").wait_for()
        initial = page.evaluate(
            "window.modViewer.activeMeshes[0].quaternion.toArray()")
        assert initial == pytest.approx(
            [-2 ** -0.5, 0, 0, 2 ** -0.5])
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        upright_states = page.evaluate("""() => window.modViewer.activeMeshes.map(
          mesh => mesh.quaternion.toArray())""")
        assert upright_states[1] == pytest.approx(upright_states[0])

        page.evaluate("async () => await window.modViewer.switchMod('WuWa')")
        page.wait_for_function(
            "window.__fakeApi.calls.loadMod.length === 2"
            " && window.modViewer.activeMeshes.length === 1")
        wuwa_initial = page.evaluate(
            "window.modViewer.activeMeshes[0].quaternion.toArray()")
        assert wuwa_initial == pytest.approx([0, 1, 0, 0])
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        wuwa_states = page.evaluate("""() => window.modViewer.activeMeshes.map(
          mesh => mesh.quaternion.toArray())""")
        assert wuwa_states[1] == pytest.approx(wuwa_states[0])
    finally:
        context.close()

def test_asset_rebuild_preserves_tree_and_disables_only_one_root(
        edge_browser, frontend_url):
    root = "fixture-assets"
    child = root + r"\Character"
    context, page = _page(
        edge_browser, frontend_url, {},
        asset_folders=[{
            "type": "GIMI", "path": root, "exists": True,
            "index": {
                "status": "ready", "assetCount": 3,
                "geometryHashCount": 4, "skippedCount": 1,
            },
        }],
        asset_subfolders={root: [{"name": "Character", "path": child}]},
    )
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-list .asset-folder-select").first.wait_for()
        assert "3 assets" in page.locator(".asset-folder-index-status").inner_text()
        page.locator(".asset-folder-expand").first.click()
        page.locator(".asset-folder-select", has_text="Character").click()
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.rebuild_asset_index = async path => {
            state.calls.rebuildAssetIndex.push(path);
            return new Promise(resolve => { state.releaseRebuild = () => resolve({
              folders: state.assetFolders}); });
          };
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.wait_for_function(
            "window.__fakeApi.calls.rebuildAssetIndex.length === 1")
        assert page.locator(".asset-folder-rebuild").is_disabled()
        assert page.locator(".asset-folder-switch").is_disabled()
        assert page.locator(".asset-folder-more").is_disabled()
        page.evaluate("window.__fakeApi.releaseRebuild()")
        page.locator(".asset-folder-rebuild:not([disabled])").wait_for()
        assert page.locator(".asset-folder-expand.expanded").count() == 1
        assert page.evaluate(
            "document.querySelector('.asset-folder-row.active')?.dataset.assetFolderPath"
        ) == child
        root_arrow = page.locator(
            "#asset-folder-list > .asset-folder-node > .asset-folder-row .asset-folder-expand")
        root_children = page.locator(
            "#asset-folder-list > .asset-folder-node > .asset-folder-children")
        root_arrow.click()
        assert root_children.is_hidden()
        root_arrow.click()
        assert not root_children.is_hidden()
        page.evaluate("""() => {
          window.pywebview.api.rebuild_asset_index = async () => ({
            error: 'rebuild failed', indexPreserved: true});
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.locator("#asset-folder-error.show").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.rebuild_asset_index = async () => ({
            folders: window.__fakeApi.assetFolders.map(folder => ({...folder}))});
        }""")
        page.locator(".asset-folder-rebuild").click()
        page.wait_for_function(
            "!document.querySelector('#asset-folder-error').classList.contains('show')")
    finally:
        context.close()

def test_asset_save_shows_building_state_until_index_is_ready(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, asset_folders=[])
    try:
        page.locator("#assets-tab").click()
        page.locator("#asset-folder-add").click()
        page.evaluate("window.__fakeApi.nextPath = 'picked-asset-folder'")
        page.locator("#afm-browse").click()
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.add_asset_folder = async () => new Promise(resolve => {
            state.releaseAssetSave = () => resolve({folders: []});
          });
        }""")
        page.locator("#afm-save").click()
        page.wait_for_function(
            "document.querySelector('#afm-save').textContent === 'Building index…'")
        assert page.locator("#afm-save").is_disabled()
        assert page.locator("#afm-browse").is_disabled()
        assert page.locator("#afm-cancel").is_disabled()
        assert page.locator("#afm-type").is_disabled()
        page.evaluate("window.__fakeApi.releaseAssetSave()")
        page.locator("#asset-folder-modal-backdrop").wait_for(state="hidden")
    finally:
        context.close()

def test_stale_same_path_fill_response_preserves_replacement_session(
        edge_browser, frontend_url):
    payload = _payload("FillSamePath")
    _add_asset_fill_response(payload, "FillSamePathAsset")
    context, page = _page(
        edge_browser, frontend_url, {"FillSamePath": payload})
    try:
        _open(page, "FillSamePath")
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const api = window.pywebview.api;
          const originalLoadMod = api.load_mod;
          const originalLoadFill = api.load_missing_asset_parts;
          const originalRemove = api.remove_missing_asset_parts;
          let loadCount = 0;
          window.__backendFillId = null;
          window.__removeFillIds = [];
          window.__releaseFill = null;
          api.load_mod = async path => {
            window.__backendFillId = null;
            return originalLoadMod(path);
          };
          api.load_missing_asset_parts = async path => {
            const result = await originalLoadFill(path);
            const fillId = `same-path-fill-${++loadCount}`;
            window.__backendFillId = fillId;
            const enriched = {...result, fill_id: fillId};
            if (loadCount === 1) {
              await new Promise(resolve => window.__releaseFill = resolve);
            }
            return enriched;
          };
          api.remove_missing_asset_parts = async (path, fillId) => {
            window.__removeFillIds.push(fillId);
            const result = await originalRemove(path, fillId);
            if (fillId !== window.__backendFillId) {
              return {status: 'removed', removed: false, stale: true};
            }
            window.__backendFillId = null;
            return result;
          };
          document.getElementById('asset-fill-btn').click();
        }""")
        page.wait_for_function("window.__releaseFill !== null")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function(
            "window.__fakeApi.calls.loadMod.length === 2"
            " && window.modViewer.activeMeshes.length === 1")
        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='remove']").wait_for()
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        assert page.evaluate("window.__backendFillId") == "same-path-fill-2"

        page.evaluate("window.__releaseFill()")
        page.wait_for_function(
            "window.__fakeApi.calls.removeMissingAssetParts.length === 1")
        assert page.evaluate("window.__backendFillId") == "same-path-fill-2"
        assert page.evaluate("window.modViewer.activeMeshes.length") == 2
        assert page.locator("#asset-fill-btn").get_attribute("data-state") == "remove"

        page.locator("#asset-fill-btn").click()
        page.locator("#asset-fill-btn[data-state='load']").wait_for()
        assert page.evaluate("window.__removeFillIds") == [
            "same-path-fill-1", "same-path-fill-2"]
        assert page.evaluate("window.__backendFillId") is None
        assert page.evaluate("window.modViewer.activeMeshes.length") == 1
    finally:
        context.close()
