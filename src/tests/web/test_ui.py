from .support import *

def test_left_dock_tabs_toggle_and_keep_aria_state(edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, asset_folders=[])
    try:
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-folder-list").is_hidden()
        assert page.locator("#mod-folder-empty").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-selected") == "true"
        page.locator("#assets-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator("#asset-folder-panel").is_visible()
        assert page.locator("#asset-folder-list").is_hidden()
        assert page.locator("#asset-folder-empty").is_visible()
        assert page.locator("#assets-tab").get_attribute("aria-expanded") == "true"
        page.locator("#assets-tab").click()
        assert page.locator("#asset-folder-panel").is_hidden()
        assert page.locator("#left-panel-container").is_hidden()
        assert page.locator("#left-dock-tabs").is_visible()
        assert page.locator(".left-dock-tabs .active").count() == 0
        assert all(value == "false" for value in page.locator(
            ".left-dock-tabs > button").evaluate_all(
                "buttons => buttons.map(button => button.getAttribute('aria-selected'))"))
    finally:
        context.close()

def test_right_dock_tabs_toggle_without_reopening_on_refresh(edge_browser, frontend_url):
    path = "fixture-model"
    context, page = _page(edge_browser, frontend_url, {path: _payload()})
    try:
        _open(page, path)
        page.locator("#right-dock.ui-visible").wait_for()
        assert page.locator("#controls-panel").is_visible()
        assert page.locator("body.right-dock-visible").count() == 1
        assert page.locator("body.right-dock-mounted").count() == 1
        page.locator("#controls-tab").click()
        assert page.locator("#controls-panel").is_hidden()
        assert page.locator("body.right-dock-visible").count() == 0
        assert page.locator("body.right-dock-mounted").count() == 1
        assert page.evaluate("""() => {
          const tabs = document.querySelector('#right-dock .right-dock-tabs').getBoundingClientRect();
          const gizmo = document.querySelector('#view-gizmo').getBoundingClientRect();
          return gizmo.top >= tabs.bottom;
        }""")
        assert page.locator("#right-dock .right-dock-tabs").is_visible()
        page.locator("#inspector-tab").click()
        assert page.locator("#inspector-panel").is_visible()
        assert page.locator("body.right-dock-visible").count() == 1
        page.locator("#inspector-tab").click()
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("body.right-dock-visible").count() == 0
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("#controls-panel").is_hidden()
        assert page.locator("#controls-tab").get_attribute("aria-selected") == "false"
        assert page.locator("#inspector-tab").get_attribute("aria-expanded") == "false"
    finally:
        context.close()

def test_mesh_row_selection_invalidates_on_demand_renderer(
        edge_browser, frontend_url):
    payload = _payload("Selection")
    second = copy.deepcopy(payload["meshes"]["Body-Selection-0"])
    second["component"] = "Face Selection"
    payload["meshes"]["Face-Selection-0"] = second
    context, page = _page(edge_browser, frontend_url, {"Selection": payload})
    try:
        _open(page, "Selection")
        rows = page.locator(".draw-item")
        rows.nth(0).wait_for()
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(300)
        idle_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == idle_count

        rows.nth(0).click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=idle_count)
        first_state = page.evaluate("""() => {
          return window.modViewer.activeMeshes.map(mesh => ({
            emissive: mesh.material.emissive.getHex(),
            intensity: mesh.material.emissiveIntensity,
          }));
        }""")
        assert first_state == [
            {"emissive": 0xffd60a, "intensity": 0.22},
            {"emissive": 0x000000, "intensity": 1},
        ]

        selected_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == selected_count

        rows.nth(1).click()
        page.wait_for_function(
            "count => window.modViewer.getRenderCount() > count", arg=selected_count)
        second_state = page.evaluate("""() => {
          return window.modViewer.activeMeshes.map(mesh => ({
            emissive: mesh.material.emissive.getHex(),
            intensity: mesh.material.emissiveIntensity,
          }));
        }""")
        assert second_state == [
            {"emissive": 0x000000, "intensity": 1},
            {"emissive": 0xffd60a, "intensity": 0.22},
        ]

        final_count = page.evaluate("window.modViewer.getRenderCount()")
        page.wait_for_timeout(200)
        assert page.evaluate("window.modViewer.getRenderCount()") == final_count
    finally:
        context.close()

def test_controls_precede_inspector_and_are_the_default_right_dock_tab(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator(".right-dock-tabs > button").evaluate_all(
            "tabs => tabs.map(tab => tab.id)") == ["controls-tab", "inspector-tab"]
        assert page.locator("#controls-tab").get_attribute(
            "aria-selected") == "true"
        assert page.locator("#controls-panel").is_visible()
        assert page.locator("#inspector-panel").is_hidden()

        page.locator("#inspector-tab").click()
        page.reload()
        page.wait_for_function("window.modViewer !== undefined")
        _open(page, "A")
        page.locator(".draw-item").wait_for()
        assert page.locator("#inspector-tab").get_attribute(
            "aria-selected") == "true"
        assert page.locator("#inspector-panel").is_visible()
        assert page.locator("#controls-panel").is_hidden()
    finally:
        context.close()

def test_source_grouping_and_collapse_are_shared_without_losing_duplicates(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url,
        {"Single": _payload("Single"), "Sources": _source_payload()},
    )
    try:
        _open(page, "Single")
        page.locator(".draw-item").wait_for()
        assert page.locator("#mesh-list .mesh-src-hdr").count() == 0
        assert page.locator("#toggle-list .toggle-src-hdr").count() == 0
        assert page.locator("#menu-list .toggle-src-hdr").count() == 0

        page.evaluate("window.__oldDrawRow = document.querySelector('.draw-item')")
        _open(page, "Sources")
        page.wait_for_function(
            "document.querySelector('.draw-item') !== window.__oldDrawRow")
        pool_identity = page.evaluate("""() => {
          const meshes = window.modViewer.activeMeshes;
          const root = meshes[0];
          const nested = meshes.slice(1);
          return {
            poolCount: new Set(meshes.map(mesh => mesh.userData.texturePool)).size,
            nestedShared: nested[0]?.userData.texturePool
              === nested[1]?.userData.texturePool,
            rootDistinct: root?.userData.texturePool
              !== nested[0]?.userData.texturePool,
          };
        }""")
        expected = ["Root.ini", "variants/sub"]
        assert page.locator("#mesh-list .mesh-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#toggle-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#menu-list .toggle-src-hdr .group-name").all_inner_texts() == expected
        assert page.locator("#mesh-list .group-hdr .group-name").all_inner_texts() == ["Body", "Body"]
        assert page.locator("#toggle-list .toggle-name").all_inner_texts() == ["Duplicate", "Duplicate"]
        assert pool_identity["poolCount"] == 2
        assert pool_identity["nestedShared"]
        assert pool_identity["rootDistinct"]

        first_header = page.locator("#mesh-list .mesh-src-hdr").first
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 1
        assert first_header.locator(".group-toggle.collapsed").count() == 1
        first_header.click()
        assert page.locator("#mesh-list .mesh-src-items.collapsed").count() == 0
    finally:
        context.close()

def test_toggle_panel_headers_collapse_and_expand_their_content(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator("#toggle-list .toggle-item").wait_for()
        page.locator("#menu-list .menu-item").first.wait_for()

        for panel_id, content_id in (("toggle-panel", "toggle-list"),
                                     ("menu-panel", "menu-list")):
            panel = page.locator(f"#{panel_id}")
            content = page.locator(f"#{content_id}")
            chevron = panel.locator(".panel-hdr .group-toggle")

            assert "collapsed" not in (content.get_attribute("class") or "")
            chevron.click()
            assert "collapsed" in (content.get_attribute("class") or "")
            assert chevron.get_attribute("aria-expanded") == "false"
            assert not content.is_visible()

            chevron.click()
            assert "collapsed" not in (content.get_attribute("class") or "")
            assert chevron.get_attribute("aria-expanded") == "true"
            assert content.is_visible()
    finally:
        context.close()

def test_feature_flag_css_keeps_cycle_preview_and_core_invariants(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        page.locator("#toggle-list .toggle-cycle-btn").wait_for()
        assert page.locator("#present-list").inner_text() == (
            "No key or menu toggle is available for PRESENT.")
        assert page.locator("#present-action-btn").is_enabled()
        page.evaluate("""
          document.body.classList.add('feature-export-off', 'feature-modify-toggle-off')
        """)
        assert page.locator("#export-btn").is_hidden()
        assert page.locator("#toggle-add-btn").is_hidden()
        assert page.locator("#toggle-list .toggle-actions").is_hidden()
        assert page.locator("#toggle-list .toggle-cycle-btn").is_visible()

        css = (paths.web_dir() + "/css/app.css")
        with open(css, encoding="utf-8") as handle:
            source = handle.read().replace(" ", "")
        assert "#menu-list.image-layout.collapsed{display:none;}" in source
        with open(paths.web_dir() + "/index.html", encoding="utf-8") as handle:
            html = handle.read()
        assert "https://cdn" not in html.lower()
        assert "http://" not in html.lower()
    finally:
        context.close()

def test_mod_folder_panel_browses_children_lazily(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    astra = root + r"\Astra"
    context, page = _page(
        edge_browser, frontend_url, {},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={
            root: [
                {"name": "Alice", "path": alice},
                {"name": "Astra", "path": astra},
            ],
            alice: [{"name": "Summer", "path": alice + r"\Summer"}],
        },
    )
    try:
        assert page.locator("#empty-add-folder-btn").inner_text() == "Open Mod Folder"
        _open_library(page)
        assert page.locator("#mod-folder-modal-backdrop.show").count() == 0
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"

        root_node = page.locator("#mod-folder-list > .mod-folder-node").first
        arrow_style = page.evaluate("""() => {
          const arrow = document.querySelector('.mod-folder-expand');
          const style = getComputedStyle(arrow);
          return {width: style.width, height: style.height, fontSize: style.fontSize};
        }""")
        assert arrow_style == {"width": "20px", "height": "22px", "fontSize": "18px"}
        root_node.locator(":scope > .mod-folder-row > .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Alice").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root]
        assert page.evaluate("window.__fakeApi.calls.loadMod") == []

        alice_node = page.locator(".mod-folder-select", has_text="Alice").locator(
            "xpath=../..")
        alice_node.locator(".mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Summer").wait_for()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root, alice]

        root_arrow = root_node.locator(":scope > .mod-folder-row > .mod-folder-expand")
        root_children = root_node.locator(":scope > .mod-folder-children")
        root_arrow.click()
        assert root_children.is_hidden()
        root_arrow.click()
        assert not root_children.is_hidden()
        assert page.evaluate("window.__fakeApi.calls.listSubfolders") == [root, alice]
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator(".left-dock-tabs .active").count() == 0
    finally:
        context.close()

def test_empty_mod_folder_panel_stays_above_navigation_hint(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {}, mod_folders=[])
    try:
        assert page.locator("#empty-add-folder-btn").inner_text() == "Add Mod Folder"
        page.locator("#empty-add-folder-btn").click()
        page.locator("#mod-folder-panel:not([hidden])").wait_for()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        page.locator("#mfm-cancel").click()
        panel = page.locator("#mod-folder-panel").bounding_box()
        info = page.locator("#footer").bounding_box()
        assert panel["y"] + panel["height"] <= info["y"]
        assert page.locator("#mod-folder-list").inner_text() == ""
        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return {inert: panel.inert, ariaHidden: panel.getAttribute('aria-hidden')};
        }""") == {"inert": False, "ariaHidden": "false"}
        page.locator("#mod-library-tab").click()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.evaluate("document.querySelector('#mod-folder-panel').inert")
    finally:
        context.close()

def test_mod_folder_name_selection_preserves_dock_state_across_reload(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    context, page = _page(
        edge_browser, frontend_url, {alice: _payload("Alice")},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={root: [{"name": "Alice", "path": alice}]},
    )
    try:
        _open_library(page)
        page.locator("#mod-folder-list > .mod-folder-node .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="Alice").click()
        page.locator(".draw-item").wait_for(state="attached")
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [alice]
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-folder-list > .mod-folder-node > .mod-folder-row > .mod-folder-expand").click()
        assert "active-descendant" in page.locator(
            "#mod-folder-list > .mod-folder-node > .mod-folder-row").get_attribute("class")

        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 2")
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function("window.__fakeApi.calls.loadMod.length === 3")
        assert page.locator("#mod-folder-panel").is_hidden()
    finally:
        context.close()

def test_mod_folder_failed_selection_keeps_panel_open_and_reports_error(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    alice = root + r"\Alice"
    broken = root + r"\Broken"
    context, page = _page(
        edge_browser, frontend_url,
        {alice: _payload("Alice"), broken: {"error": "loader failed"}},
        mod_folders=[
            {"name": "Alice", "path": alice, "exists": True},
            {"name": "Broken", "path": broken, "exists": True},
        ],
    )
    try:
        _open_library(page)
        page.locator(".mod-folder-select", has_text="Alice").click()
        page.locator(".draw-item").wait_for(state="attached")
        assert page.locator(".mod-folder-row.active").count() == 1

        page.locator(".mod-folder-select", has_text="Broken").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        assert page.locator(".mod-folder-row.active").count() == 0
        assert page.locator(".draw-item").count() == 0
        page.locator("#dialog-ok").click()
    finally:
        context.close()

def test_mod_folder_tree_switch_respects_pending_change_confirmation(
        edge_browser, frontend_url):
    root = _MOD_LIBRARY
    first = root + r"\First"
    second = root + r"\Second"
    context, page = _page(
        edge_browser, frontend_url,
        {first: _payload("First"), second: _payload("Second")},
        pending={first: True},
        mod_folders=[{"name": "Library", "path": root, "exists": True}],
        subfolders={root: [
            {"name": "First", "path": first},
            {"name": "Second", "path": second},
        ]},
    )
    try:
        _open_library(page)
        page.locator("#mod-folder-list > .mod-folder-node .mod-folder-expand").click()
        page.locator(".mod-folder-select", has_text="First").click()
        page.locator(".draw-item").wait_for(state="attached")

        page.locator(".mod-folder-select", has_text="Second").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert page.locator("#dialog-message").inner_text().startswith(
            "This mod has unsaved changes")
        page.locator("#dialog-cancel").click()
        assert page.evaluate("window.__fakeApi.calls.loadMod") == [first]
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
    finally:
        context.close()

def test_mod_folder_add_edit_delete_modal_flow(
        edge_browser, frontend_url):
    original = _MOD_LIBRARY + r"\Original"
    replacement = _MOD_LIBRARY + r"\Replacement"
    context, page = _page(
        edge_browser, frontend_url, {},
        mod_folders=[{"name": "Original", "path": original, "exists": True}],
    )
    try:
        _open_library(page)

        page.locator("#mod-folder-add").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        modal_styles = page.evaluate("""() => {
          const style = id => {
            const value = getComputedStyle(document.querySelector(id));
            return {background: value.backgroundColor, color: value.color,
              fontSize: value.fontSize, padding: value.padding};
          };
          return {
            cancel: style('#mfm-cancel'),
            save: style('#mfm-save'),
            browse: style('#mfm-browse'),
            presentCancel: style('#pm-cancel'),
            presentSave: style('#pm-save'),
          };
        }""")
        assert modal_styles["cancel"] == modal_styles["presentCancel"]
        assert modal_styles["save"] == modal_styles["presentSave"]
        assert modal_styles["browse"]["background"] == modal_styles["cancel"]["background"]
        page.evaluate("path => { window.__fakeApi.nextPath = path; }", replacement)
        page.locator("#mfm-browse").click()
        assert page.locator("#mfm-path").input_value() == replacement
        assert page.locator("#mfm-name").input_value() == "Replacement"
        page.locator("#mfm-save").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for(state="hidden")
        page.locator(".mod-folder-select", has_text="Replacement").wait_for()

        page.locator("[aria-label='More actions for Original']").click()
        original_menu = page.locator(
            ".mod-folder-node", has_text="Original").locator(
            ".mod-folder-action-menu")
        assert page.locator(
            "[aria-label='More actions for Original']").get_attribute(
                "aria-expanded") == "true"
        assert original_menu.evaluate("menu => getComputedStyle(menu).position") == "absolute"
        assert original_menu.evaluate(
            "menu => getComputedStyle(menu).flexDirection") == "column"
        menu_widths = original_menu.evaluate("""menu => {
          const menuBox = menu.getBoundingClientRect();
          return [...menu.querySelectorAll('button')].map(button => ({
            button: button.getBoundingClientRect().width,
            menu: menuBox.width,
          }));
        }""")
        assert all(item["button"] >= item["menu"] - 12 for item in menu_widths)
        assert original_menu.locator("button").all_inner_texts() == [
            "Edit", "Remove"]
        page.locator(".mod-folder-node").filter(has_text="Original").locator(
            ".mod-folder-action-menu button", has_text="Edit").click()
        assert page.locator(
            "[aria-label='More actions for Original']").get_attribute(
                "aria-expanded") == "false"
        page.locator("#mfm-name").fill("Renamed")
        page.locator("#mfm-save").click()
        page.locator(".mod-folder-select", has_text="Renamed").wait_for()

        page.locator("[aria-label='More actions for Renamed']").click()
        page.locator(".mod-folder-node").filter(has_text="Renamed").locator(
            ".mod-folder-action-menu button", has_text="Remove").click()
        page.locator("#dialog-backdrop.show").wait_for()
        assert "Files on disk will not be deleted." in page.locator(
            "#dialog-message").inner_text()
        page.locator("#dialog-ok").click()
        assert page.locator(".mod-folder-select", has_text="Renamed").count() == 0
        assert page.locator(".mod-folder-select", has_text="Replacement").count() == 1
    finally:
        context.close()

def test_panel_opacity_bridge_fallback_and_late_ready(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")},
        panel_opacity=27, panel_opacity_api=False)
    try:
        assert page.locator("#panel-opacity").input_value() == "58"
        assert page.locator("#open-btn").is_enabled()
        assert page.locator("#empty-add-folder-btn").is_enabled()
        page.locator("#empty-add-folder-btn").click()
        page.locator("#mod-folder-modal-backdrop.show").wait_for()
        page.locator("#mfm-cancel").click()
        page.locator("#mod-library-tab").click()
        _open(page, "A")
        page.locator("#meshes-tab").click()
        page.locator(".draw-item").wait_for()
        page.evaluate("""() => {
          const state = window.__fakeApi;
          window.pywebview.api.get_panel_opacity = async () => ({value: state.panelOpacity});
          window.pywebview.api.set_panel_opacity = async value => {
            state.panelOpacity = value;
            state.calls.panelOpacity.push(value);
            return {value};
          };
          window.dispatchEvent(new Event('pywebviewready'));
        }""")
        page.wait_for_function("document.querySelector('#panel-opacity').value === '27'")
        assert page.locator("#appearance-btn").get_attribute("aria-label") == (
            "Panel opacity: 27%")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
    finally:
        context.close()

def test_panel_opacity_control_applies_and_saves_whole_percent(
        edge_browser, frontend_url):
    context, page = _page(
        edge_browser, frontend_url, {"A": _payload("A")}, panel_opacity=35)
    try:
        page.wait_for_function("document.querySelector('#panel-opacity').value === '35'")
        _open(page, "A")
        slider = page.locator("#panel-opacity")
        assert slider.get_attribute("min") == "0"
        assert slider.get_attribute("max") == "100"
        assert page.locator("#appearance-btn").get_attribute("aria-label") == (
            "Panel opacity: 35%")
        page.evaluate("""() => document.querySelector('#panel-opacity')
          .dispatchEvent(new Event('change', {bubbles: true}))""")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
        header_controls = page.evaluate("""() => {
          const toolbar = document.querySelector('#toolbar').getBoundingClientRect();
          const environment = document.querySelector('#environment-btn').getBoundingClientRect();
          const appearance = document.querySelector('#appearance-btn').getBoundingClientRect();
          return {
            environmentLeftOfAppearance: environment.right <= appearance.left,
            equalSize: environment.width === appearance.width &&
              environment.height === appearance.height,
            appearanceAtRight: appearance.right >= toolbar.right - 16,
          };
        }""")
        assert header_controls == {
            "environmentLeftOfAppearance": True,
            "equalSize": True,
            "appearanceAtRight": True,
        }
        assert page.locator("#environment-label").count() == 0
        opacity_surfaces = page.locator(
            "#sidebar, .right-dock-tabs, #tool-panel.viewport-toolbar")
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgba(13, 17, 23, 0.35)"}

        page.locator("#appearance-btn").click()
        assert not page.locator("#appearance-popover").is_hidden()
        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '100';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        assert page.locator("#panel-opacity-value").inner_text() == "100%"
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgb(13, 17, 23)"}
        assert page.evaluate("""() => {
          const style = getComputedStyle(document.documentElement);
          return [
            style.getPropertyValue('--panel-blur').trim(),
            style.getPropertyValue('--panel-shadow-opacity').trim(),
          ];
        }""") == ["10px", "0.22"]
        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '0';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        assert page.locator("#panel-opacity-value").inner_text() == "0%"
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).backgroundColor)")) == {
                "rgba(13, 17, 23, 0)"}
        assert page.evaluate("""() => {
          const style = getComputedStyle(document.documentElement);
          return [
            style.getPropertyValue('--panel-blur').trim(),
            style.getPropertyValue('--panel-shadow-opacity').trim(),
          ];
        }""") == ["0px", "0"]
        assert set(opacity_surfaces.evaluate_all(
            "panels => panels.map(panel => getComputedStyle(panel).borderColor)")) == {
                "rgba(139, 148, 158, 0.45)"}
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == []
        page.evaluate("""() => document.querySelector('#panel-opacity')
          .dispatchEvent(new Event('change', {bubbles: true}))""")
        page.wait_for_function("window.__fakeApi.calls.panelOpacity.length === 1")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == [0]

        page.evaluate("""() => {
          const slider = document.querySelector('#panel-opacity');
          slider.value = '58';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_function("window.__fakeApi.calls.panelOpacity.length === 2")
        assert page.evaluate("window.__fakeApi.calls.panelOpacity") == [0, 58]
        page.keyboard.press("Escape")
        assert page.locator("#appearance-popover").is_hidden()
        assert page.locator("#appearance-btn").get_attribute("aria-expanded") == "false"
    finally:
        context.close()

def test_inspector_follows_component_and_mesh_selection(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        _open(page, "A")
        assert page.evaluate("document.querySelector('#camera-buttons').parentElement.id") == (
            "viewport-camera-buttons")
        assert not page.locator("#camera-panel").is_visible()
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).flexDirection") == "row"
        layout = page.evaluate("""() => {
          const toolbar = document.querySelector('#toolbar').getBoundingClientRect();
          const tabs = document.querySelector('#right-dock .right-dock-tabs').getBoundingClientRect();
          const present = document.querySelector('#present-panel').getBoundingClientRect();
          const gizmo = document.querySelector('#view-gizmo').getBoundingClientRect();
          return {
            toolbarBottom: toolbar.bottom,
            tabsBottom: tabs.bottom,
            presentTop: present.top,
            gizmoTop: gizmo.top,
            gizmoWidth: gizmo.width,
            gizmoHeight: gizmo.height,
            inCanvas: !!document.querySelector('#view-gizmo').closest('#canvas-container'),
          };
        }""")
        assert layout["inCanvas"]
        assert layout["gizmoTop"] >= layout["toolbarBottom"]
        assert layout["gizmoTop"] >= layout["tabsBottom"]
        assert abs(layout["gizmoTop"] - layout["presentTop"]) < 1
        assert layout["gizmoWidth"] == 104
        assert layout["gizmoHeight"] == 104
        page.locator("#view-gizmo .gizmo-axis.positive").first.click()
        assert "collapsed" not in (page.locator("#present-list").get_attribute("class") or "")
        panel_styles = page.locator(
            "#sidebar, #mod-folder-panel, #present-panel, #toggle-panel, "
            "#menu-panel, #inspector-panel, .right-dock-tabs, "
            "#tool-panel.viewport-toolbar").evaluate_all(
                "panels => panels.map(panel => { const style = getComputedStyle(panel); "
                "return {background: style.backgroundColor, backdrop: style.backdropFilter}; })")
        assert panel_styles
        assert {style["background"] for style in panel_styles} == {
            "rgba(13, 17, 23, 0.58)"}
        assert all("blur(5.8px)" in style["backdrop"] for style in panel_styles)
        assert page.locator("#health-btn .ui-icon").count() == 1
        assert page.locator("#wire-btn").get_attribute("aria-pressed") == "false"
        assert page.locator("#interaction-help").inner_text() == "LMB Orbit · RMB Pan · Wheel Zoom"
        assert page.locator("#sidebar > .panel-hdr .group-toggle").evaluate(
            "button => button.tagName") == "BUTTON"
        assert page.locator("#sidebar > .panel-hdr #reset-state-btn").count() == 1
        assert page.locator("#sidebar > .panel-hdr > *").evaluate_all(
            "nodes => nodes.map(node => node.id || node.tagName)") == [
                "BUTTON", "H3", "DIV"]
        assert page.locator("#sidebar > .panel-hdr > .panel-hdr-actions > *").evaluate_all(
            "nodes => nodes.map(node => node.id)") == [
                "asset-fill-btn", "reset-state-btn"]
        assert page.locator("#asset-fill-btn").get_attribute("aria-label") == (
            "Load missing parts")
        assert page.locator("#asset-fill-btn").get_attribute("aria-pressed") == "false"
        assert page.locator("#asset-fill-btn").get_attribute("data-state") == "load"
        assert page.locator("#asset-fill-btn .ui-icon use").get_attribute("href") == (
            "#icon-mesh-add")
        for panel_id, action_id in (("mod-folder-panel", "mod-folder-add"),
                                    ("asset-folder-panel", "asset-folder-add")):
            actions = page.locator(f"#{panel_id} > .panel-hdr .panel-hdr-actions")
            assert actions.count() == 1
            assert actions.locator(f"#{action_id}").get_attribute("aria-label")
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#meshes-tab").get_attribute("aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-folder-panel").is_visible()
        assert page.locator("#mod-library-tab").get_attribute("aria-expanded") == "true"
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        page.locator("#mod-library-tab").click()
        assert page.locator("#sidebar").is_hidden()
        assert page.locator("#mod-folder-panel").is_hidden()
        assert page.locator(".left-dock-tabs .active").count() == 0
        page.locator("#meshes-tab").click()
        assert page.locator("#sidebar").is_visible()
        assert page.locator("#meshes-tab").get_attribute("aria-expanded") == "true"
        page.locator("#reset-state-btn").click()
        assert page.locator("#sidebar > .panel-hdr .group-toggle").get_attribute(
            "aria-expanded") == "true"
        page.locator("#inspector-tab").click()
        page.locator(".group-hdr .group-name").first.click()
        page.locator("#inspector-content").wait_for()
        inspector = page.locator("#inspector-content")
        assert "Draw calls" not in inspector.inner_text()
        assert inspector.locator(".inspector-header h3").inner_text() == "Body A"
        assert inspector.locator(".inspector-context").inner_text() == (
            "1 mesh · 1 visible")
        assert inspector.locator(".inspector-section-title").all_inner_texts() == [
            "MATERIAL", "TEXTURES"]
        assert inspector.locator(".inspector-manage-textures").count() == 1
        assert page.locator("#inspector-empty").is_hidden()

        page.locator(".draw-item").first.click()
        assert inspector.locator(".inspector-header h3").inner_text()
        assert inspector.locator(".inspector-context").inner_text() == "Body A"
        assert inspector.locator(".inspector-section-title").all_inner_texts() == [
            "MATERIAL", "TEXTURE"]
        assert "State" not in inspector.inner_text()
        assert "Material kind" not in inspector.inner_text()
        assert "Texture provenance" not in inspector.inner_text()
        assert "Slot evidence" not in inspector.inner_text()
        assert "Draw" not in inspector.inner_text()
        assert "Resolved" not in inspector.inner_text()
        assert "Body A >" in page.locator("#selected-mesh-status").inner_text()
        assert page.locator(".draw-item.selected").count() == 1
        page.evaluate("import('./js/scene/selection.js').then(({clearSelection}) => clearSelection())")
        assert page.locator("#inspector-empty").is_visible()
        assert page.locator("#inspector-content").is_hidden()

        page.locator(".group-hdr .group-name").first.click()
        assert page.locator(".draw-item.selected").count() == 0
        assert "selected" in page.locator(".group-hdr").first.get_attribute("class")
        assert inspector.locator(".inspector-context").inner_text() == (
            "1 mesh · 1 visible")
        assert page.locator("#inspector-empty").is_hidden()

        page.locator(".draw-item .mesh-state-btn").first.click()
        assert inspector.locator(".inspector-context").inner_text() == (
            "1 mesh · 0 visible")
        eye = page.locator(".draw-item .mesh-state-btn").first
        assert eye.get_attribute("aria-pressed") == "false"
        assert "state-hidden" in eye.get_attribute("class")
        assert "state-manual" in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is True
        page.locator("#reset-state-btn").click()
        assert inspector.locator(".inspector-context").inner_text() == (
            "1 mesh · 1 visible")
        assert eye.get_attribute("aria-pressed") == "true"
        assert "state-hidden" not in eye.get_attribute("class")
        assert "state-manual" not in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False

        eye.click()
        eye.click()
        assert eye.get_attribute("aria-pressed") == "true"
        assert "state-manual" not in eye.get_attribute("class")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False
        page.evaluate("import('./js/mesh/visibility.js').then(({refreshAll}) => refreshAll())")
        assert eye.get_attribute("aria-pressed") == "true"
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.manuallyToggled") is False

        assert page.locator(".group-hdr .material-kind-select").count() == 0
        assert page.locator(".group-hdr .group-tex-btn").count() == 0

        page.locator("#controls-tab").click()
        assert page.locator("#inspector-panel").is_hidden()
        assert page.locator("#controls-panel").evaluate(
            "panel => getComputedStyle(panel).overflowY") == "auto"
        page.locator("#inspector-tab").click()
        assert not page.locator("#inspector-panel").is_hidden()
    finally:
        context.close()

def test_viewport_toolbar_popovers_and_responsive_overflow(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"A": _payload("A")})
    try:
        page.evaluate(
            "localStorage.setItem('mod-viewer.panel.tool-panel.collapsed', 'true')")
        page.reload()
        page.wait_for_function("window.modViewer !== undefined")
        _open(page, "A")
        page.locator(".draw-item").first.wait_for()
        assert page.locator("#tool-buttons").is_visible()
        toolbar_geometry = page.evaluate("""() => {
          const style = selector => getComputedStyle(document.querySelector(selector));
          return {
            token: getComputedStyle(document.documentElement)
              .getPropertyValue('--toolbar-height').trim(),
            toolbarHeight: style('#toolbar').height,
            canvasTop: style('#canvas-container').top,
            leftDockTop: style('#left-dock').top,
            rightDockTop: style('#right-dock').top,
          };
        }""")
        assert toolbar_geometry == {
            "token": "54px",
            "toolbarHeight": "54px",
            "canvasTop": "54px",
            "leftDockTop": "58px",
            "rightDockTop": "58px",
        }
        toolbar_layout = page.evaluate("""() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect();
          const style = selector => getComputedStyle(document.querySelector(selector));
          const toolbar = rect('#tool-panel');
          const camera = rect('#viewport-camera-buttons');
          const tools = rect('#tool-buttons');
          return {
            centered: Math.abs(toolbar.left + toolbar.width / 2 - window.innerWidth / 2) < 1,
            sameRow: Math.abs(camera.top + camera.height / 2
              - tools.top - tools.height / 2) < 1,
            toolbarDirection: style('#tool-panel').flexDirection,
            toolbarWrap: style('#tool-panel').flexWrap,
            toolsDisplay: style('#tool-buttons').display,
            toolsDirection: style('#tool-buttons').flexDirection,
            cameraLabelsHidden: [...document.querySelectorAll(
              '#viewport-camera-buttons .camera-btn span')]
              .every(span => getComputedStyle(span).display === 'none'),
          };
        }""")
        assert toolbar_layout == {
            "centered": True,
            "sameRow": True,
            "toolbarDirection": "row",
            "toolbarWrap": "nowrap",
            "toolsDisplay": "flex",
            "toolsDirection": "row",
            "cameraLabelsHidden": True,
        }
        toolbar_center = page.evaluate("""() => {
          const box = document.querySelector('#tool-panel').getBoundingClientRect();
          return box.left + box.width / 2;
        }""")
        page.locator("#inspector-tab").click()
        page.wait_for_function("document.querySelector('#inspector-panel').hidden === false")
        assert abs(page.evaluate("""() => {
          const box = document.querySelector('#tool-panel').getBoundingClientRect();
          return box.left + box.width / 2;
        }""") - toolbar_center) < 1
        page.wait_for_function("window.modViewer.getRenderCount() > 0")
        page.wait_for_timeout(250)

        for button_id in ("camera-reset-view-btn", "camera-flip-btn",
                          "camera-flip-horizontal-btn"):
            before = page.evaluate("window.modViewer.getRenderCount()")
            page.locator(f"#{button_id}").click()
            page.wait_for_function(
                "count => window.modViewer.getRenderCount() > count", arg=before)

        page.locator("#environment-btn").click()
        page.locator("#environment-popover:not([hidden])").wait_for()
        page.locator("#environment-popover .ui-popover-option", has_text="Studio").click()
        assert page.locator("#environment-btn").get_attribute("aria-label") == (
            "Environment: Studio. Click to change.")
        assert page.locator("#environment-popover").is_hidden()

        page.locator("#texture-btn").click()
        page.locator("#texture-popover:not([hidden])").wait_for()
        assert page.locator("#texture-btn").get_attribute("aria-expanded") == "true"
        assert page.locator(
            "#texture-popover .ui-popover-option").all_inner_texts() == [
                "All maps", "Diffuse and NormalMap", "Diffuse only", "No textures"]
        texture_position = page.evaluate("""() => {
          const button = document.querySelector('#texture-btn').getBoundingClientRect();
          const popover = document.querySelector('#texture-popover').getBoundingClientRect();
          return {
            above: popover.bottom <= button.top,
            within: popover.left >= 0 && popover.right <= window.innerWidth
              && popover.top >= 0 && popover.bottom <= window.innerHeight,
          };
        }""")
        assert texture_position == {"above": True, "within": True}
        page.locator("#texture-popover .ui-popover-option", has_text="Diffuse only").click()
        assert page.locator("#texture-btn").get_attribute("aria-label") == (
            "Textures: diffuse only")
        assert page.locator("#texture-btn").get_attribute("aria-expanded") == "false"
        assert page.locator("#texture-popover").is_hidden()

        page.locator("#light-btn").click()
        page.locator("#light-popover:not([hidden])").wait_for()
        assert page.locator("#light-btn").get_attribute("aria-expanded") == "true"
        light_position = page.evaluate("""() => {
          const button = document.querySelector('#light-btn').getBoundingClientRect();
          const popover = document.querySelector('#light-popover').getBoundingClientRect();
          return {
            above: popover.bottom <= button.top,
            within: popover.left >= 0 && popover.right <= window.innerWidth
              && popover.top >= 0 && popover.bottom <= window.innerHeight,
          };
        }""")
        assert light_position == {"above": True, "within": True}
        assert page.locator("#light-popover .ui-popover-option").all_inner_texts() == [
            "Bright", "Normal", "Off"]
        page.locator("#light-popover .ui-popover-option", has_text="Normal").click()
        assert page.locator("#light-popover").is_hidden()
        assert page.locator("#light-btn").get_attribute("aria-expanded") == "false"
        assert page.locator("#light-btn").get_attribute("aria-label").startswith("Key light: normal")

        page.locator("#light-btn").click()
        page.locator("#light-popover:not([hidden])").wait_for()
        assert page.locator("#light-popover .ui-popover-option").all_inner_texts() == [
            "Bright", "Normal", "Off"]
        page.locator("#light-btn").click()
        assert page.locator("#light-popover").is_hidden()

        ao_button = page.locator("#ao-btn")
        assert ao_button.get_attribute("aria-pressed") is None
        assert ao_button.get_attribute("aria-expanded") == "false"
        assert ao_button.get_attribute("aria-controls") == "ao-popover"
        assert ao_button.get_attribute("aria-label") == "Ambient occlusion: 0%"
        assert not ao_button.evaluate("button => button.classList.contains('active')")
        assert not ao_button.evaluate("button => button.classList.contains('partial')")

        ao_button.click()
        page.locator("#ao-popover:not([hidden])").wait_for()
        assert ao_button.get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.activeElement.id") == "ao-slider"
        slider = page.locator("#ao-slider")
        assert slider.get_attribute("min") == "0"
        assert slider.get_attribute("max") == "100"
        assert slider.get_attribute("step") == "1"
        assert slider.input_value() == "0"
        ao_position = page.evaluate("""() => {
          const button = document.querySelector('#ao-btn').getBoundingClientRect();
          const popover = document.querySelector('#ao-popover').getBoundingClientRect();
          return {
            above: popover.bottom <= button.top,
            within: popover.left >= 0 && popover.right <= window.innerWidth
              && popover.top >= 0 && popover.bottom <= window.innerHeight,
          };
        }""")
        assert ao_position == {"above": True, "within": True}

        for level, class_name in ((1, "partial"), (50, "partial"),
                                  (99, "partial"), (100, "active"), (0, None)):
            page.evaluate("""value => {
              const slider = document.querySelector('#ao-slider');
              slider.value = String(value);
              slider.dispatchEvent(new Event('input', {bubbles: true}));
            }""", level)
            assert slider.input_value() == str(level)
            assert page.locator("#ao-value").inner_text() == f"{level}%"
            assert ao_button.get_attribute("aria-label") == (
                f"Ambient occlusion: {level}%")
            assert ao_button.get_attribute("title") == f"Ambient occlusion: {level}%"
            assert ao_button.evaluate(
                "button => button.classList.contains('active')") is (level == 100)
            assert ao_button.evaluate(
                "button => button.classList.contains('partial')") is (class_name == "partial")

        page.keyboard.press("Escape")
        assert page.locator("#ao-popover").is_hidden()
        assert ao_button.get_attribute("aria-expanded") == "false"
        ao_button.click()
        page.locator("#ao-popover:not([hidden])").wait_for()
        page.locator("#footer").click(position={"x": 5, "y": 5})
        assert page.locator("#ao-popover").is_hidden()
        assert ao_button.get_attribute("aria-expanded") == "false"

        page.locator("#environment-btn").click()
        page.locator("#environment-popover:not([hidden])").wait_for()
        assert page.locator("#environment-btn").get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert page.locator("#environment-popover").is_hidden()
        assert page.locator("#environment-btn").get_attribute("aria-expanded") == "false"

        page.set_viewport_size({"width": 640, "height": 720})
        page.locator("#toolbar-more").wait_for()
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).flexWrap") == "nowrap"
        assert page.locator("#tool-panel").evaluate(
            "panel => getComputedStyle(panel).overflowX") == "auto"
        assert page.locator("#health-btn .health-label").is_hidden()
        page.locator("#toolbar-more").click()
        assert not page.locator("#toolbar-overflow").is_hidden()
        assert page.locator("#toolbar-overflow [data-toolbar-target='health-btn']").count() == 1

        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return panel.inert && panel.getAttribute('aria-hidden') === 'true';
        }""")
        page.locator("#mod-library-tab").click()
        assert page.evaluate("document.querySelector('#mod-folder-panel').inert === false")
        page.locator("#mod-library-tab").click()
        assert page.evaluate("""() => {
          const panel = document.querySelector('#mod-folder-panel');
          return panel.inert && panel.getAttribute('aria-hidden') === 'true';
        }""")
    finally:
        context.close()
