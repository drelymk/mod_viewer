import base64
import copy
import json

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

def test_reload_current_mod_does_not_read_disabled_ini_checkbox(
        edge_browser, frontend_url):
    path = "ReloadCheckbox"
    context, page = _page(edge_browser, frontend_url, {path: _payload(path)})
    try:
        _open(page, path)
        page.locator(".draw-item").wait_for()
        page.locator("#open-disabled-mod").check()
        page.evaluate("window.modViewer.reloadCurrentMod()")
        page.wait_for_function(
            "window.__fakeApi.calls.loadModArgs.length === 2")

        assert page.evaluate("window.__fakeApi.calls.loadModArgs") == [
            [path, False], [path, False],
        ]
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
            selected: mesh.userData.viewerOutline.userData.selectionSelected,
            outlineVisible: mesh.userData.viewerOutline.visible,
            outlineMaterial: mesh.userData.viewerOutline.material.color.getHex(),
          }));
        }""")
        assert first_state == [
            {"emissive": 0x000000, "intensity": 1, "selected": True,
             "outlineVisible": True, "outlineMaterial": 0xffd60a},
            {"emissive": 0x000000, "intensity": 1, "selected": False,
             "outlineVisible": False, "outlineMaterial": 0x111318},
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
            selected: mesh.userData.viewerOutline.userData.selectionSelected,
            outlineVisible: mesh.userData.viewerOutline.visible,
            outlineMaterial: mesh.userData.viewerOutline.material.color.getHex(),
          }));
        }""")
        assert second_state == [
            {"emissive": 0x000000, "intensity": 1, "selected": False,
             "outlineVisible": False, "outlineMaterial": 0x111318},
            {"emissive": 0x000000, "intensity": 1, "selected": True,
             "outlineVisible": True, "outlineMaterial": 0xffd60a},
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
            "tabs => tabs.map(tab => tab.id)") == [
                "controls-tab", "inspector-tab", "weight-tab"]
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
          document.body.classList.add(
            'feature-export-off', 'feature-modify-toggle-off',
            'feature-open-disabled-mod-off')
        """)
        assert page.locator("#export-btn").is_hidden()
        assert page.locator("#toggle-add-btn").is_hidden()
        assert page.locator("#toggle-list .toggle-actions").is_hidden()
        assert page.locator("#toggle-list .toggle-cycle-btn").is_visible()
        assert page.locator("#open-disabled-mod").is_hidden()

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


def test_open_disabled_checkbox_controls_direct_and_library_loads(
        edge_browser, frontend_url):
    direct_unchecked = "DirectUnchecked"
    direct_checked = "DirectChecked"
    library_unchecked = "LibraryUnchecked"
    library_checked = "LibraryChecked"
    context, page = _page(
        edge_browser, frontend_url,
        {path: _payload(path) for path in (
            direct_unchecked, direct_checked,
            library_unchecked, library_checked)},
        mod_folders=[
            {"name": "Library Unchecked", "path": library_unchecked,
             "exists": True},
            {"name": "Library Checked", "path": library_checked,
             "exists": True},
        ],
    )
    try:
        checkbox = page.locator("#open-disabled-mod")
        assert checkbox.is_visible()
        assert checkbox.get_attribute("title") == "Open disabled mod"
        assert checkbox.get_attribute("aria-label") == "Open disabled mod"
        assert not checkbox.is_checked()
        assert checkbox.evaluate("element => element.labels.length") == 0
        assert "Open disabled mod" not in page.locator("#toolbar").inner_text()

        _open(page, direct_unchecked)
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.__fakeApi.calls.loadModArgs.length === 1")
        checkbox.check()
        _open(page, direct_checked)
        page.locator(".draw-item").wait_for()
        page.wait_for_function(
            "window.__fakeApi.calls.loadModArgs.length === 2")

        checkbox.uncheck()
        _open_library(page)
        page.locator(".mod-folder-select", has_text="Library Unchecked").click()
        page.locator(".draw-item").wait_for(state="attached")
        page.wait_for_function(
            "window.__fakeApi.calls.loadModArgs.length === 3")
        checkbox.check()
        page.locator(".mod-folder-select", has_text="Library Checked").click()
        page.locator(".draw-item").wait_for(state="attached")
        page.wait_for_function(
            "window.__fakeApi.calls.loadModArgs.length === 4")

        assert page.evaluate("window.__fakeApi.calls.loadModArgs") == [
            [direct_unchecked, False], [direct_checked, True],
            [library_unchecked, False], [library_checked, True],
        ]
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
            "MATERIAL", "TEXTURE", "COLOR"]
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

def test_inspector_color_controls_gate_asset_textures_and_persist_on_change(
        edge_browser, frontend_url):
    payload = _payload("ColorUI")
    first = payload["meshes"]["Body-ColorUI-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::ColorUI-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body ColorUI::3,0,0": {
            "hue": 30, "saturation": 1.15, "brightness": 1,
            "contrast": 1, "red": 1, "green": 1, "blue": 1,
            "tint": "#ffffff", "tint_strength": 0,
        },
    }
    context, page = _page(edge_browser, frontend_url, {"ColorUI": payload})
    try:
        _open(page, "ColorUI")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        color = page.locator(".inspector-color-section")
        assert color.locator(".inspector-color-slider").count() == 8
        hue = color.locator("[data-color-field='hue'] .inspector-color-slider")
        assert hue.input_value() == "30"
        assert color.locator("[data-color-field='saturation'] .inspector-color-value").inner_text() == "115%"

        before_loads = page.evaluate("window.__fakeApi.calls.loadMod.length")
        page.evaluate("""() => {
          const slider = document.querySelector(
            '[data-color-field="hue"] .inspector-color-slider');
          slider.value = '55';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.colorAdjustment.hue") == 55
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].material.userData.gameMaterial.colorAdjustmentEnabledNode.value")
        assert page.evaluate("window.__fakeApi.calls.loadMod.length") == before_loads
        page.evaluate("""() => document.querySelector(
          '[data-color-field="hue"] .inspector-color-slider')
          .dispatchEvent(new Event('change', {bubbles: true}))""")
        page.wait_for_function(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length === 1")
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment[0][2].hue") == 55

        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {diffuse: 'diffuse::asset/root/Body.dds'});
          window.dispatchEvent(new CustomEvent('mod-viewer-mesh-state-changed', {
            detail: {meshes: [mesh]},
          }));
        }""")
        page.locator(".inspector-color-readonly-title").wait_for()
        assert color.locator(".inspector-color-slider").count() == 0
        assert "Color editing is unavailable for Asset textures." in color.inner_text()

        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {diffuse: mesh.userData.defaultTexKey});
          window.dispatchEvent(new CustomEvent('mod-viewer-mesh-state-changed', {
            detail: {meshes: [mesh]},
          }));
        }""")
        page.locator(".inspector-color-slider").first.wait_for()
        assert page.locator(
            "[data-color-field='hue'] .inspector-color-slider").input_value() == "55"

        color.locator(".inspector-color-reset").click()
        assert page.locator(
            "[data-color-field='hue'] .inspector-color-slider").input_value() == "0"
        assert color.locator(".inspector-texture-bake").is_disabled()

        page.evaluate("""async () => {
          const {setMeshTextureState} = await import('./js/mesh/mesh-factory.js');
          const mesh = window.modViewer.activeMeshes[0];
          setMeshTextureState(mesh, {diffuse: null});
          window.dispatchEvent(new CustomEvent('mod-viewer-mesh-state-changed', {
            detail: {meshes: [mesh]},
          }));
        }""")
        page.locator(".inspector-color-readonly-title").wait_for()
        assert page.locator(".inspector-color-readonly-title").inner_text() == (
            "No diffuse texture")
    finally:
        context.close()


def test_color_persistence_serializes_updates_and_flushes_before_texture_save(
        edge_browser, frontend_url):
    payload = _payload("ColorQueue")
    first = payload["meshes"]["Body-ColorQueue-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::ColorQueue-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body ColorQueue::3,0,0": {"hue": 30},
    }
    context, page = _page(edge_browser, frontend_url, {"ColorQueue": payload})
    try:
        _open(page, "ColorQueue")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.evaluate("window.__fakeApi.blockColorSaves = true")
        page.evaluate("""() => {
          const slider = document.querySelector(
            '[data-color-field="hue"] .inspector-color-slider');
          slider.value = '40';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_function(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length === 1")
        page.evaluate("""() => {
          const slider = document.querySelector(
            '[data-color-field="hue"] .inspector-color-slider');
          slider.value = '55';
          slider.dispatchEvent(new Event('input', {bubbles: true}));
          slider.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        page.wait_for_timeout(50)
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length") == 1
        page.evaluate("""async () => {
          const {flushMeshColorAdjustmentPersistence} =
            await import('./js/mesh/mesh-color-state.js');
          const mesh = window.modViewer.activeMeshes[0];
          window.__colorFlushDone = false;
          flushMeshColorAdjustmentPersistence(mesh).then(() => {
            window.__colorFlushDone = true;
          });
        }""")
        page.wait_for_timeout(50)
        assert not page.evaluate("window.__colorFlushDone")
        page.evaluate("window.__fakeApi.releaseColorSaves()")
        page.wait_for_function(
            "window.__colorFlushDone && "
            "window.__fakeApi.calls.saveMeshColorAdjustment.length === 2")
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment.map(item => item[2].hue)") == [
                40, 55]
    finally:
        context.close()


def test_texture_save_modal_opens_without_analysis_and_lists_changed_meshes(
        edge_browser, frontend_url):
    payload = _payload("Bake")
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body Bake::3,0,0": {"hue": 30},
    }
    first = payload["meshes"]["Body-Bake-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::Bake-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    second = copy.deepcopy(first)
    second["component"] = "Face Bake"
    second["display_name"] = "Friendly Face"
    payload["meshes"]["Face-Bake-0"] = second
    payload["metadata"]["mesh_color_adjustments"][
        "Face Bake::3,0,0"] = {"hue": 45}
    context, page = _page(edge_browser, frontend_url, {"Bake": {
        **payload,
    }})
    try:
        _open(page, "Bake")
        page.locator(".draw-item").first.wait_for()
        page.evaluate("""() => {
          window.modViewer.activeMeshes[1].visible = false;
        }""")
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        button = page.locator(".inspector-texture-bake")
        assert button.count() == 1
        button.click()
        page.locator("#texture-bake-modal-backdrop.show").wait_for()
        assert "SAVE TO TEXTURE" in page.locator("#texture-bake-body").inner_text()
        assert page.locator("#texture-bake-close").count() == 1
        assert page.locator("#texture-bake-close-x").count() == 1
        assert "Friendly Face" in page.locator("#texture-bake-body").inner_text()
        assert "Body-Bake-0" in page.locator("#texture-bake-body").inner_text()
        assert page.locator("#texture-bake-confirm").inner_text() == "Save"
        assert page.locator("#texture-bake-confirm").is_visible()
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function(
            "window.__fakeApi.calls.saveTextureColor.length === 1")
        request = page.evaluate("window.__fakeApi.calls.saveTextureColor[0]")
        assert request[1] == dds_key
        targets = request[2]
        assert [item["semantic_key"] for item in targets] == [
            "Body-Bake-0", "Face-Bake-0"]
        usage = request[3]
        assert len(usage) == 2
        assert {item["semantic_key"] for item in usage} == {
            "Body-Bake-0", "Face-Bake-0"}
        assert usage[1]["texture_keys"] == {
            "diffuse": dds_key,
            "normal_map": None,
            "normal_data": None,
            "light_map": None,
            "material_map": None,
            "emission_map": None,
        }
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length") == 0
        page.locator("#texture-bake-close").click()
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 0
    finally:
        context.close()


def test_texture_save_modal_does_not_show_coverage_or_analysis_state(
        edge_browser, frontend_url):
    payload = _payload("Unknown")
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body Unknown::3,0,0": {"hue": 30},
    }
    old_key = payload["meshes"]["Body-Unknown-0"]["tex_key"]
    dds_key = "diffuse::Unknown-one.dds"
    payload["meshes"]["Body-Unknown-0"]["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    context, page = _page(edge_browser, frontend_url, {"Unknown": payload})
    try:
        _open(page, "Unknown")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-modal-backdrop.show").wait_for()
        details = page.locator("#texture-bake-body").inner_text()
        assert "SAVE TO TEXTURE" in details
        assert "coverage" not in details.lower()
        assert page.locator("#texture-bake-confirm").is_visible()
    finally:
        context.close()


def test_texture_save_action_is_disabled_for_neutral_color_state(
        edge_browser, frontend_url):
    payload = _payload("NeutralBake")
    first = payload["meshes"]["Body-NeutralBake-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::NeutralBake-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    context, page = _page(edge_browser, frontend_url, {"NeutralBake": payload})
    try:
        _open(page, "NeutralBake")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        button = page.locator(".inspector-texture-bake")
        assert button.is_disabled()
        assert button.get_attribute("title") == \
            "Adjust a mesh color before saving."
    finally:
        context.close()


def test_texture_save_cross_role_usage_is_rejected_by_save_request(
        edge_browser, frontend_url):
    payload = _payload("CrossRole")
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body CrossRole::3,0,0": {"hue": 30},
    }
    first = payload["meshes"]["Body-CrossRole-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::CrossRole-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    second = copy.deepcopy(first)
    second["component"] = "Face CrossRole"
    second["display_name"] = "Friendly Face"
    second["normal_map_key"] = dds_key.replace("diffuse::", "normal_map::")
    payload["meshes"]["Face-CrossRole-0"] = second
    payload["textureSaveResult"] = {
        "status": "unsupported",
        "code": "cross_role_texture_usage",
        "error": "This DDS is also used as a Normal Map by Face-CrossRole-0.",
        "details": {},
    }
    context, page = _page(edge_browser, frontend_url, {"CrossRole": payload})
    try:
        _open(page, "CrossRole")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-modal-backdrop.show").wait_for()
        assert page.locator("#texture-bake-confirm").is_visible()
        page.locator("#texture-bake-confirm").click()
        page.locator("#texture-bake-error").wait_for()
        assert page.locator("#texture-bake-error").inner_text() == (
            "This DDS is also used as a Normal Map by Face-CrossRole-0.")
        usage = page.evaluate("window.__fakeApi.calls.saveTextureColor[0][3]")
        face = next(item for item in usage
                    if item["semantic_key"] == "Face-CrossRole-0")
        assert face["texture_keys"]["normal_map"] == (
            "normal_map::CrossRole-one.dds")
        assert page.evaluate(
            "window.__fakeApi.calls.saveTextureColor.length") == 1
    finally:
        context.close()


def _bake_test_payload(label, texture_uri, hue=30):
    payload = _payload(label)
    first = payload["meshes"][f"Body-{label}-0"]
    tex_key = f"diffuse::{label}-bake.dds"
    first["uv"] = _f32(0, 0, 1, 0, 0, 1)
    first["tex_key"] = tex_key
    payload["texture_pools"]["p0"][0]["tex_key"] = tex_key
    payload["textures"] = {tex_key: texture_uri}
    payload["metadata"]["mesh_color_adjustments"] = {
        f"Body {label}::3,0,0": {"hue": hue},
    }
    payload["textureSaveResult"] = {
        "status": "ok", "tex_key": tex_key,
        "affected_tex_keys": [tex_key],
        "saved_meshes": [{
            "semantic_key": f"Body-{label}-0",
            "metadata_key": f"Body {label}::3,0,0",
        }],
        "texture": {"file": f"{label}-bake.dds"},
        "backup": {"file": f"{label}-bake.dds.modviewer.bak"},
    }
    return payload, tex_key


def test_texture_save_modal_blocks_dismissal_while_saving(
        edge_browser, frontend_url):
    payload, tex_key = _bake_test_payload("BakeModal", _PNG_URI)
    context, page = _page(edge_browser, frontend_url, {"BakeModal": payload})
    try:
        _open(page, "BakeModal")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        assert "Saving" not in page.locator("#texture-bake-body").inner_text()
        page.evaluate("""() => {
          window.pywebview.api.save_texture_color = async () =>
            new Promise(resolve => { window.__releaseTextureSave = resolve; });
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("window.__releaseTextureSave !== undefined")

        page.locator("#texture-bake-close").click()
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 1
        page.locator("#texture-bake-close-x").click()
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 1
        page.keyboard.press("Escape")
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 1
        page.evaluate("""() => document.querySelector(
          '#texture-bake-modal-backdrop').dispatchEvent(
            new MouseEvent('click', {bubbles: true}))""")
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 1

        page.evaluate("""() => window.__releaseTextureSave({
          status: 'ok', tex_key: %s, affected_tex_keys: [%s],
          saved_meshes: [{semantic_key: 'Body-BakeModal-0',
            metadata_key: 'Body BakeModal::3,0,0'}],
          texture: {file: 'BakeModal-bake.dds'},
          backup: {file: 'BakeModal-bake.dds.modviewer.bak'},
        })""" % (json.dumps(tex_key), json.dumps(tex_key)))
        page.locator("#texture-bake-body", has_text="TEXTURE SAVED").wait_for()
        details = page.locator("#texture-bake-body").inner_text()
        assert "Color metadata" not in details
        page.locator("#texture-bake-close").click()
        assert page.locator("#texture-bake-modal-backdrop.show").count() == 0
    finally:
        context.close()


def test_texture_save_refreshes_targets_after_color_change(
        edge_browser, frontend_url):
    payload, _tex_key = _bake_test_payload("BakeStale", _PNG_URI)
    context, page = _page(edge_browser, frontend_url, {"BakeStale": payload})
    try:
        _open(page, "BakeStale")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        page.evaluate("""async () => {
          const {setMeshColorAdjustment} =
            await import('./js/mesh/mesh-color-state.js');
          setMeshColorAdjustment(window.modViewer.activeMeshes[0], {hue: 75}, {
            persist: false,
          });
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function(
            "window.__fakeApi.calls.saveTextureColor.length === 0")
        assert "Save to Texture" in page.locator(
            "#texture-bake-title").inner_text()
        assert page.locator("#texture-bake-confirm").is_visible()
    finally:
        context.close()


def test_texture_save_error_hides_consumed_save_action(
        edge_browser, frontend_url):
    payload, _tex_key = _bake_test_payload("BakeFailure", _PNG_URI)
    context, page = _page(edge_browser, frontend_url,
                           {"BakeFailure": payload})
    try:
        _open(page, "BakeFailure")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.save_texture_color = async () => ({
            status: 'error',
            error: 'The texture could not be saved safely.',
          });
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("""() => document.querySelector(
          '#texture-bake-error').textContent.includes('could not be saved')""")
        assert page.locator("#texture-bake-confirm").is_hidden()

        page.locator("#texture-bake-close").click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.save_texture_color = undefined;
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("""() => document.querySelector(
            '#texture-bake-error').textContent === 'Texture saving is unavailable.'""")
        assert page.locator("#texture-bake-confirm").is_hidden()
    finally:
        context.close()


def test_successful_texture_save_syncs_stale_mesh_after_selection_changes(
        edge_browser, frontend_url):
    payload, tex_key = _bake_test_payload(
        "BakeSelectionRace", f"{frontend_url}/bake-selection-race.png")
    first = payload["meshes"]["Body-BakeSelectionRace-0"]
    second = copy.deepcopy(first)
    second["component"] = "Face BakeSelectionRace"
    second["display_name"] = "Face target"
    payload["meshes"]["Face-BakeSelectionRace-0"] = second
    context, page = _page(edge_browser, frontend_url,
                           {"BakeSelectionRace": payload})
    try:
        page.route("**/bake-selection-race.png", lambda route: (
            route.fulfill(
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
                content_type="image/png",
                headers={"Cache-Control": "no-store"})))
        page.evaluate("""async () => {
          const THREE = await import('three');
          THREE.Cache.enabled = false;
        }""")
        _open(page, "BakeSelectionRace")
        page.locator(".draw-item").nth(1).wait_for()
        page.wait_for_function(
            "window.modViewer.getMaterialState(0).diffuseBound")
        page.evaluate("""async () => {
          const {getGameMaterialTexture} =
            await import('./js/mesh/material-profile.js');
          window.__initialBakeTextureUuid = getGameMaterialTexture(
            window.modViewer.activeMeshes[0].material, 'diffuse').uuid;
        }""")
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").nth(0).click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        page.evaluate("""() => {
          window.pywebview.api.save_texture_color = async () =>
            new Promise(resolve => { window.__releaseTextureSave = resolve; });
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("window.__releaseTextureSave !== undefined")
        page.evaluate("import('./js/scene/selection.js').then(({selectMesh}) => "
                      "selectMesh(window.modViewer.activeMeshes[1]))")
        assert "Face BakeSelectionRace" in page.locator(
            "#selected-mesh-status").inner_text()

        page.evaluate("""() => window.__releaseTextureSave({
          status: 'ok', tex_key: %s, affected_tex_keys: [%s],
          saved_meshes: [{semantic_key: 'Body-BakeSelectionRace-0',
            metadata_key: 'Body BakeSelectionRace::3,0,0'}],
          texture: {file: 'BakeSelectionRace-bake.dds'},
          backup: {file: 'BakeSelectionRace-bake.dds.modviewer.bak'},
        })""" % (json.dumps(tex_key), json.dumps(tex_key)))
        page.wait_for_function(
            "window.modViewer.activeMeshes[0].userData.colorAdjustment.hue === 0")
        page.wait_for_function("""async () => {
          const {getGameMaterialTexture} =
            await import('./js/mesh/material-profile.js');
          const texture = getGameMaterialTexture(
            window.modViewer.activeMeshes[0].material, 'diffuse');
          return texture?.uuid !== window.__initialBakeTextureUuid;
        }""")
        assert "Face BakeSelectionRace" in page.locator(
            "#selected-mesh-status").inner_text()
    finally:
        context.close()


def test_successful_texture_save_does_not_reload_a_new_mod_after_switch(
        edge_browser, frontend_url):
    first_payload, first_key = _bake_test_payload(
        "BakeSwitchA", f"{frontend_url}/bake-switch-a.png")
    second_payload, _second_key = _bake_test_payload(
        "BakeSwitchB", f"{frontend_url}/bake-switch-b.png", hue=0)
    requests = {"a": 0, "b": 0}
    context, page = _page(edge_browser, frontend_url, {
        "BakeSwitchA": first_payload, "BakeSwitchB": second_payload,
    })
    try:
        page.route("**/bake-switch-a.png", lambda route: (
            requests.__setitem__("a", requests["a"] + 1),
            route.fulfill(
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
                content_type="image/png",
                headers={"Cache-Control": "no-store"})))
        page.route("**/bake-switch-b.png", lambda route: (
            requests.__setitem__("b", requests["b"] + 1),
            route.fulfill(
                body=base64.b64decode(_PNG_URI.split(",", 1)[1]),
                content_type="image/png",
                headers={"Cache-Control": "no-store"})))
        _open(page, "BakeSwitchA")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.evaluate("""() => {
          window.pywebview.api.save_texture_color = async () =>
            new Promise(resolve => { window.__releaseTextureSave = resolve; });
        }""")
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").wait_for()
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("window.__releaseTextureSave !== undefined")

        page.evaluate("""async () => {
          await window.modViewer.switchMod('BakeSwitchB');
        }""")
        page.locator(".draw-item").first.wait_for()
        page.wait_for_timeout(300)
        b_requests_after_load = requests["b"]
        assert page.evaluate("window.modViewer.getCurrentSource().path") == (
            "BakeSwitchB")

        page.evaluate("""() => window.__releaseTextureSave({
          status: 'ok', tex_key: %s, affected_tex_keys: [%s],
          saved_meshes: [{semantic_key: 'Body-BakeSwitchA-0',
            metadata_key: 'Body BakeSwitchA::3,0,0'}],
          texture: {file: 'BakeSwitchA-bake.dds'},
          backup: {file: 'BakeSwitchA-bake.dds.modviewer.bak'},
        })""" % (json.dumps(first_key), json.dumps(first_key)))
        page.wait_for_function(
            "document.querySelector('#texture-bake-modal-backdrop').classList.contains('show') === false")
        page.wait_for_timeout(300)
        assert requests["b"] == b_requests_after_load
    finally:
        context.close()


def test_texture_save_resets_all_committed_meshes_and_refreshes_affected_keys(
        edge_browser, frontend_url):
    payload = _payload("BakeConfirm")
    first = payload["meshes"]["Body-BakeConfirm-0"]
    old_key = first["tex_key"]
    dds_key = "diffuse::BakeConfirm-one.dds"
    first["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body BakeConfirm::3,0,0": {"hue": 30},
    }
    second = copy.deepcopy(first)
    second["component"] = "Face BakeConfirm"
    second["display_name"] = "Face Confirm"
    payload["meshes"]["Face-BakeConfirm-0"] = second
    payload["metadata"]["mesh_color_adjustments"][
        "Face BakeConfirm::3,0,0"] = {"hue": 45}
    payload["textureSaveResult"] = {
        "status": "ok", "warning": "color_state_reset_failed",
        "tex_key": dds_key,
        "affected_tex_keys": [dds_key],
        "saved_meshes": [{
            "semantic_key": "Body-BakeConfirm-0",
            "metadata_key": "Body BakeConfirm::3,0,0",
        }, {
            "semantic_key": "Face-BakeConfirm-0",
            "metadata_key": "Face BakeConfirm::3,0,0",
        }],
        "texture": {"file": "BakeConfirm-one.dds"},
        "backup": {"file": "BakeConfirm-one.dds.modviewer.bak"},
    }
    context, page = _page(edge_browser, frontend_url, {"BakeConfirm": payload})
    try:
        _open(page, "BakeConfirm")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        bake = page.locator(".inspector-texture-bake")
        assert bake.is_enabled()
        bake.click()
        page.locator("#texture-bake-confirm").wait_for()
        page.locator("#texture-bake-confirm").click()
        page.locator("#texture-bake-body", has_text="TEXTURE SAVED").wait_for()
        assert page.evaluate(
            "window.__fakeApi.calls.saveTextureColor[0]") == [
                "BakeConfirm", dds_key,
                [{
                    "semantic_key": "Body-BakeConfirm-0",
                    "metadata_key": "Body BakeConfirm::3,0,0",
                    "adjustment": {
                        "hue": 30, "saturation": 1, "brightness": 1, "contrast": 1,
                        "red": 1, "green": 1, "blue": 1, "tint": "#ffffff",
                        "tint_strength": 0,
                    },
                }, {
                    "semantic_key": "Face-BakeConfirm-0",
                    "metadata_key": "Face BakeConfirm::3,0,0",
                    "adjustment": {
                        "hue": 45, "saturation": 1, "brightness": 1, "contrast": 1,
                        "red": 1, "green": 1, "blue": 1, "tint": "#ffffff",
                        "tint_strength": 0,
                    },
                }],
                [{
                    "semantic_key": "Body-BakeConfirm-0",
                    "texture_keys": {
                        "diffuse": dds_key,
                        "normal_map": None,
                        "normal_data": None,
                        "light_map": None,
                        "material_map": None,
                        "emission_map": None,
                    },
                }, {
                    "semantic_key": "Face-BakeConfirm-0",
                    "texture_keys": {
                        "diffuse": dds_key,
                        "normal_map": None,
                        "normal_data": None,
                        "light_map": None,
                        "material_map": None,
                        "emission_map": None,
                    },
                }],
            ]
        assert page.evaluate(
            "window.modViewer.activeMeshes[0].userData.colorAdjustment.hue") == 0
        assert page.evaluate(
            "window.modViewer.activeMeshes[1].userData.colorAdjustment.hue") == 0
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length") == 2
        assert "Color metadata" not in page.locator(
            "#texture-bake-body").inner_text()
        page.wait_for_function(
            "window.__fakeApi.calls.diagnostics.length >= 2")
    finally:
        context.close()


def test_texture_save_keeps_warning_when_metadata_recovery_fails(
        edge_browser, frontend_url):
    payload, _tex_key = _bake_test_payload("BakeCleanupFailure", _PNG_URI)
    payload["textureSaveResult"]["warning"] = "color_state_reset_failed"
    context, page = _page(edge_browser, frontend_url,
                           {"BakeCleanupFailure": payload})
    try:
        _open(page, "BakeCleanupFailure")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.evaluate("""() => {
          window.pywebview.api.save_mesh_color_adjustment = async (...args) => {
              window.__fakeApi.calls.saveMeshColorAdjustment.push(args);
              return {error: 'metadata write failed'};
            };
        }""")
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function(
            "window.__fakeApi.calls.saveTextureColor.length === 1")
        page.locator("#texture-bake-body", has_text="TEXTURE SAVED").wait_for()
        details = page.locator("#texture-bake-body").inner_text()
        assert "Color metadata" in details
        assert "Resolve the metadata write failure" in details
        assert "Reopen the mod to retry" not in details
        assert page.evaluate(
            "window.__fakeApi.calls.saveMeshColorAdjustment.length") == 1
    finally:
        context.close()


def test_forced_health_refresh_keeps_newer_backup_report(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url,
                           {"HealthRace": _payload("HealthRace")})
    try:
        _open(page, "HealthRace")
        page.locator(".draw-item").first.wait_for()
        page.wait_for_function(
            "window.__fakeApi.calls.diagnostics.length >= 1")
        page.evaluate("""async () => {
          const health = await import('./js/panels/health-report.js');
          window.__healthResolvers = [];
          health.setHealthLoader(() => new Promise(resolve => {
            window.__healthResolvers.push(resolve);
          }));
          window.__healthOld = health.refreshHealthReport();
          window.dispatchEvent(new Event('mod-viewer-texture-baked'));
        }""")
        page.wait_for_function("window.__healthResolvers.length === 2")

        page.evaluate("""() => {
          window.__healthResolvers[1]({
            summary: {issues: 1, warnings: 1, errors: 0},
            files: {referenced: 0, inactive_only: 0, viewer_only: 0},
            issues: [{severity: 'warning', category: 'asset',
              message: 'Fresh timestamped backup is unreferenced.'}],
          });
        }""")
        page.wait_for_function("""() =>
          document.querySelector('#health-count').textContent === '1' &&
          document.querySelector('#health-btn').classList.contains('warning')""")

        page.evaluate("""() => {
          window.__healthResolvers[0]({
            summary: {issues: 0, warnings: 0, errors: 0},
            files: {referenced: 0, inactive_only: 0, viewer_only: 0},
            issues: [],
          });
        }""")
        page.wait_for_timeout(100)
        assert page.locator("#health-count").inner_text() == "1"
        assert page.locator("#health-btn").get_attribute("class").find(
            "warning") >= 0

        page.locator("#health-btn").click()
        assert page.locator("#health-list .health-message").inner_text() == (
            "Fresh timestamped backup is unreferenced.")
    finally:
        context.close()


def test_texture_save_error_clears_saving_message(
        edge_browser, frontend_url):
    payload = _payload("Error")
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body Error::3,0,0": {"hue": 30},
    }
    old_key = payload["meshes"]["Body-Error-0"]["tex_key"]
    dds_key = "diffuse::Error-one.dds"
    payload["meshes"]["Body-Error-0"]["tex_key"] = dds_key
    payload["texture_pools"]["p0"][0]["tex_key"] = dds_key
    payload["textures"][dds_key] = payload["textures"].pop(old_key)
    payload["textureSaveResult"] = {
        "status": "error", "code": "coverage_incomplete",
        "error": "Texture save could not be completed safely.",
    }
    context, page = _page(edge_browser, frontend_url, {"Error": payload})
    try:
        _open(page, "Error")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-confirm").click()
        page.wait_for_function("document.querySelector('#texture-bake-error').textContent.length > 0")
        assert page.locator("#texture-bake-error").inner_text() == (
            "Texture save could not be completed safely.")
        assert "Saving color changes" not in page.locator(
            "#texture-bake-body").inner_text()
    finally:
        context.close()


def test_texture_coverage_action_explains_non_dds_without_backend_call(
        edge_browser, frontend_url):
    context, page = _page(edge_browser, frontend_url, {"NonDDS": _payload("NonDDS")})
    try:
        _open(page, "NonDDS")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        color = page.locator(".inspector-color-section")
        assert color.locator(".inspector-texture-bake").count() == 0
        assert "requires a DDS source" in color.inner_text()
    finally:
        context.close()


def test_texture_save_does_not_call_analysis_api(
        edge_browser, frontend_url):
    payload = _payload("Stale")
    payload["metadata"]["mesh_color_adjustments"] = {
        "Body Stale::3,0,0": {"hue": 30},
    }
    key = "diffuse::Stale-one.dds"
    old_key = payload["meshes"]["Body-Stale-0"]["tex_key"]
    payload["meshes"]["Body-Stale-0"]["tex_key"] = key
    payload["texture_pools"]["p0"][0]["tex_key"] = key
    payload["textures"][key] = payload["textures"].pop(old_key)
    context, page = _page(edge_browser, frontend_url, {"Stale": payload})
    try:
        _open(page, "Stale")
        page.locator(".draw-item").first.wait_for()
        page.locator("#inspector-tab").click()
        page.locator(".draw-item").first.click()
        page.locator(".inspector-texture-bake").click()
        page.locator("#texture-bake-modal-backdrop.show").wait_for()
        assert "SAVE TO TEXTURE" in page.locator(
            "#texture-bake-body").inner_text()
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
        toon_button = page.locator("#toon-btn")
        assert toon_button.get_attribute("title") == "Toon shadows: off"
        assert toon_button.get_attribute("aria-label") == "Toon shadows: off"
        assert toon_button.get_attribute("aria-pressed") == "false"
        assert toon_button.evaluate(
            "button => button.classList.contains('off')")
        active_colors = page.evaluate("""() => {
          const color = selector => getComputedStyle(
            document.querySelector(selector)).backgroundColor;
          return {
            grid: color('#grid-btn'),
            light: color('#light-btn'),
          };
        }""")
        assert active_colors == {
            "grid": "rgba(31, 111, 235, 0.22)",
            "light": "rgba(227, 179, 65, 0.18)",
        }
        assert page.locator("#light-btn").evaluate(
            "button => button.classList.contains('partial')")
        tool_order = page.evaluate(
            "() => [...document.querySelectorAll('#tool-buttons > .tool-btn')]"
            ".map(button => button.id)")
        assert tool_order.index("toon-btn") == tool_order.index("grid-btn") - 1
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
        assert page.evaluate(
            "getComputedStyle(document.querySelector('#texture-btn')).backgroundColor"
        ) == "rgba(227, 179, 65, 0.18)"

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
        assert page.locator("#light-btn").get_attribute("aria-haspopup") == "dialog"
        assert page.locator("#light-btn").get_attribute("aria-controls") == "light-popover"
        assert page.locator("#light-btn").get_attribute("aria-label") == "Key light: 67%"
        slider = page.locator("#light-slider")
        assert slider.get_attribute("min") == "0"
        assert slider.get_attribute("max") == "100"
        assert slider.get_attribute("step") == "1"
        assert slider.input_value() == "67"
        assert page.locator("#light-btn").get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.activeElement.id") == "light-slider"
        for level in (0, 33, 67, 100):
            before = page.evaluate("window.modViewer.getRenderCount()")
            page.evaluate("""value => {
              const slider = document.querySelector('#light-slider');
              slider.value = String(value);
              slider.dispatchEvent(new Event('input', {bubbles: true}));
            }""", level)
            page.wait_for_function(
                "count => window.modViewer.getRenderCount() > count", arg=before)
            assert slider.input_value() == str(level)
            assert page.locator("#light-value").text_content() == f"{level}%"
            expected_label = "Key light: Off" if level == 0 else f"Key light: {level}%"
            assert page.locator("#light-btn").get_attribute("aria-label") == expected_label
            assert page.locator("#light-btn").get_attribute("title") == expected_label
            assert page.locator("#light-btn").evaluate(
                "(button, level) => button.classList.contains('active') === (level === 100)",
                level,
            )
            assert page.locator("#light-btn").evaluate(
                "(button, level) => button.classList.contains('partial') === (level > 0 && level < 100)",
                level,
            )
            assert page.locator("#light-btn").evaluate(
                "(button, level) => button.classList.contains('off') === (level === 0)",
                level,
            )
            assert page.evaluate("""async () => {
              const {getKeyLightIntensity} = await import('./js/scene/scene.js');
              return getKeyLightIntensity();
            }""") == pytest.approx(level / 100 * 1.5)
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
