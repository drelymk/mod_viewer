"""Turn a 3DMigoto mod folder into the JSON payload the UI renders.

Deliberately free of any GUI imports so it can be exercised from tests and
scripts without pywebview or a display.

The payload is a flat dict keyed by mesh name, plus three reserved keys:
    __textures__   {texture key: data URI}
    __toggles__    the Toggle panel model (see build_toggle_panel)
    __menu__       the Menu panel model  (see build_menu_panel)
"""

import os
import traceback

from core.ini_condition import is_namespaced
from core.ini_parser import (build_draw_groups, extract_menu_toggles,
                        extract_resources, extract_toggle_keys,
                        extract_variable_defaults, find_inis, merge_sections)
from core.mesh_builder import build_mesh_payload

RESERVED_KEYS = ("__textures__", "__toggles__", "__menu__", "__mesh_names__",
                 "__geometry__")


def _ini_scope(ini_path, folder_path, multi):
    """Namespace an ini's variables so sibling inis can't collide.

    Multiple inis in one folder (an "AllInOne" mod) routinely reuse the same
    [Key...] section and variable names for different things. Prefixing by
    subfolder (or filename) keeps their toggle vars and conditions apart.

    Returns (var_prefix, source); both None for a single-ini folder. `source`
    is the bare tag the UI groups same-named keys under.
    """
    if not multi:
        return None, None
    parent_dir = os.path.dirname(ini_path)
    if os.path.normpath(parent_dir) != os.path.normpath(folder_path):
        source = os.path.basename(parent_dir)
    else:
        source = os.path.splitext(os.path.basename(ini_path))[0]
    return f"{source}::", source


def _parse_inis(ini_paths, folder_path, overrides=None):
    """Parse each ini independently, so one mod's resource definitions never
    overwrite another's. Returns (draw groups, toggle keys, menu slots,
    variable defaults).

    `overrides`, if given, is {ini_path: text} — see merge_sections; used to
    preview pending, not-yet-exported edits instead of stale on-disk content.
    """
    groups = []
    toggle_keys, menu_slots, toggle_defaults = {}, {}, {}
    multi = len(ini_paths) > 1

    # Shared across every ini (not reset per file): two sibling inis reusing
    # the same generic component name get disambiguated ("Component0_2")
    # instead of one silently overwriting the other's mesh entry.
    seen_labels = {}

    for ini_path in ini_paths:
        secs = merge_sections([ini_path], overrides=overrides)
        var_prefix, source = _ini_scope(ini_path, folder_path, multi)

        groups.extend(build_draw_groups(secs, extract_resources(secs),
                                        var_prefix=var_prefix, source=source,
                                        seen=seen_labels))
        toggle_keys.update(extract_toggle_keys(secs, var_prefix=var_prefix,
                                               source=source))
        menu_slots.update(extract_menu_toggles(secs, var_prefix=var_prefix,
                                               source=source))
        for var, val in extract_variable_defaults(secs, var_prefix=var_prefix).items():
            toggle_defaults.setdefault(var, val)

    return groups, toggle_keys, menu_slots, toggle_defaults


def _gating_vars(payload):
    """Variables that actually decide some mesh's visibility OR its texture.

    `conditions`/`texture_variants[].conditions` are each a list of OR'd
    AND-groups: [[{var,value,negate}, ...], ...]. A var that only ever swaps
    a diffuse (e.g. Key$SuitCL) never appears in `conditions`, but must still
    count as "wired" here or build_toggle_panel drops it from the Toggle
    panel entirely even though it visibly does something.
    """
    found = set()
    for entry in payload.values():
        if not isinstance(entry, dict):
            continue
        for group in entry.get("conditions", []):
            for cond in group:
                found.add(cond["var"])
        for variant in entry.get("texture_variants", []):
            for group in variant.get("conditions", []):
                for cond in group:
                    found.add(cond["var"])
    return found


def build_toggle_panel(toggle_keys, toggle_defaults, gating_vars, mod_dir=None,
                       pending_new_sections=None):
    """Model for the Toggle panel, keyed by [Key...] section rather than by
    variable, since one section can cycle several variables at once.

    A section that already gates something visible shows only its gating
    var(s) ("wired": True). A section that gates nothing yet is only shown
    ("wired": False, listing its writable/non-namespaced vars instead) if
    it's in `pending_new_sections` — {ini basename: {section name, ...}}
    from edit_session.new_sections_for() — meaning the user added it THIS
    session and hasn't Recorded it yet, so its Record button still needs to
    be reachable. Any other not-yet-gating section (e.g. a long-standing
    $menu/$skin utility key) is never shown at all: there's no reliable way
    to tell "about to be wired" apart from "will never gate anything".
    """
    pending_new_sections = pending_new_sections or {}
    panel = {}
    for section, info in toggle_keys.items():
        gated = {v: vals for v, vals in info["vars"].items() if v in gating_vars}
        wired = bool(gated)
        if wired:
            shown_vars = gated
        else:
            ini_name = os.path.basename(info["ini_path"]) if info.get("ini_path") else None
            is_pending_new = bool(ini_name) and info.get("section") in pending_new_sections.get(ini_name, ())
            if not is_pending_new:
                continue
            shown_vars = {v: vals for v, vals in info["vars"].items() if not is_namespaced(v)}
        if not shown_vars:
            continue
        panel[section] = {
            "name": info["name"],
            "key": info["key_display"] or info["key"],
            "source": info["source"],
            "ini": (os.path.relpath(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")),
            "section": info.get("section"),
            "wired": wired,
            "vars": [
                {
                    "var": var,
                    "values": values,
                    "default": toggle_defaults.get(var, values[0]),
                }
                for var, values in shown_vars.items()
            ],
        }
    return panel


def build_menu_panel(menu_slots, toggle_defaults, mod_dir=None):
    """Model for the Menu panel: the slots of a mod's in-game clickable menu
    (see core/ini_menu.py), ordered as they appear in the menu.

    Read-only, and deliberately unfiltered — unlike the Toggle panel, a slot
    that gates no mesh is still shown, since the menu is the mod's own
    documentation of what it can change and hiding entries would misrepresent
    it. `effects` are the mutual-exclusion assignments a real click also
    applies, replayed by the UI so cycling here behaves like the game does.
    """
    panel = {}
    for key in sorted(menu_slots, key=lambda k: (menu_slots[k]["source"] or "",
                                                 menu_slots[k]["slot"])):
        info = menu_slots[key]
        panel[key] = {
            "name": info["name"],
            "slot": info["slot"],
            "source": info["source"],
            "ini": (os.path.relpath(info["ini_path"], mod_dir)
                    if info.get("ini_path") and mod_dir else info.get("ini_path")),
            "section": info["section"],
            "var": info["var"],
            "values": info["values"],
            "default": toggle_defaults.get(info["var"], info["values"][0]),
            "effects": info["effects"],
        }
    return panel


def _gating_vars_from_groups(groups):
    """Same notion as _gating_vars, computed directly from build_draw_groups'
    draws instead of the (buffer-file-dependent) mesh payload — matches
    _gating_vars(payload) but never needs buffer files to exist on disk.
    Used by unwired_pending_sections, which must stay cheap and reliable
    even when mesh geometry can't be resolved.
    """
    found = set()
    for group in groups:
        for draw in group.get("draws", []):
            for cond_group in draw.get("conditions", []):
                for cond in cond_group:
                    found.add(cond["var"])
            for variant in draw.get("texture_variants", []):
                for cond_group in variant.get("conditions", []):
                    for cond in cond_group:
                        found.add(cond["var"])
    return found


def unwired_pending_sections(folder_path, overrides, pending_new_sections):
    """{ini basename: [section name, ...]} — the subset of
    `pending_new_sections` (toggles added this session, not yet exported)
    that still don't gate any mesh. {} once every freshly-added toggle is
    wired or nothing is pending.

    Reuses only the cheap, buffer-file-free half of the load pipeline
    (_parse_inis, _gating_vars_from_groups) rather than the full
    load_mod/build_mesh_payload, since toggle_api.export_changes calls this
    on every export attempt and shouldn't pay for full geometry resolution.
    Each pending ini is parsed on its own, so names are never
    namespace-prefixed here, matching how edit_session/toggle_api track them.
    """
    if not pending_new_sections:
        return {}
    by_name = {os.path.basename(p): p for p in find_inis(folder_path)}

    result = {}
    for ini_name, sections in pending_new_sections.items():
        if not sections:
            continue
        ini_path = by_name.get(ini_name)
        if ini_path is None:
            continue
        groups, toggle_keys, _menu, _ = _parse_inis([ini_path], folder_path, overrides)
        gating = _gating_vars_from_groups(groups)
        still_unwired = [
            section for section in sections
            if not any(v in gating for v in toggle_keys.get(section, {}).get("vars", {}))
        ]
        if still_unwired:
            result[ini_name] = still_unwired
    return result


def load_mod(folder_path, overrides=None, pending_new_sections=None):
    """Parse a mod folder and return the in-memory mesh payload.

    `overrides`, if given, is {ini_path: text} — read that ini from this
    in-memory text instead of disk, to preview a pending edit (see
    app/edit_session.py) without writing anything. Defaults to reading
    everything from disk.

    `pending_new_sections`, if given, is {ini basename: {section name, ...}}
    (edit_session.new_sections_for), passed through to build_toggle_panel so
    a freshly-added, not-yet-wired toggle stays visible.

    Errors are returned as {"error": ...} rather than raised, since this is
    called across the JS bridge where an exception surfaces as an opaque
    rejection.
    """
    ini_paths = find_inis(folder_path)
    if not ini_paths:
        return {"error": "No active .ini files found in this folder."}

    try:
        groups, toggle_keys, menu_slots, toggle_defaults = _parse_inis(
            ini_paths, folder_path, overrides)
        if not groups:
            return {"error": f"No mesh geometry found across {len(ini_paths)} ini file(s)."}

        payload = build_mesh_payload(groups, folder_path)
        if not payload:
            return {"error": "No mesh data could be extracted (buffer files missing?)."}

        payload["__toggles__"] = build_toggle_panel(
            toggle_keys, toggle_defaults, _gating_vars(payload), folder_path,
            pending_new_sections)
        payload["__menu__"] = build_menu_panel(menu_slots, toggle_defaults, folder_path)
        return payload
    except Exception:
        traceback.print_exc()
        return {"error": "Unexpected backend error. See the application log for details."}
