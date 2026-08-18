"""Discovery of `[Key...]` cycle toggles and `[Constants]` variable defaults."""

import re

from .ini_sections import canonical_var_names, line_source


def _format_key_combo(combo):
    """Turn 'no_ctrl no_Shift no_alt l' into a short display like 'L'."""
    mods, main = [], None
    for tok in combo.split():
        tl = tok.lower()
        if tl.startswith("no_"): continue
        if tl in ("ctrl", "shift", "alt"): mods.append(tl.capitalize())
        else: main = tok
    if main is None:
        return combo
    key_part = main.upper() if len(main) == 1 else main
    return "+".join(mods + [key_part])


def extract_toggle_keys(sections, var_prefix=None, source=None,
                        canonical_vars=None):
    """Return {group: {name, key, key_display, vars, source}} for cycle-type
    [Key...] sections, where `vars` is {variable: [cycle values]}.

    A single Key section can drive several variables at once (3DMigoto advances
    them all on one keypress), so entries are keyed by section rather than by
    variable and `vars` may hold more than one. When multiple ini files share a
    folder (AllInOne mods), the same section/variable name can appear in more
    than one file — var_prefix keeps keys unique, while `source` (e.g. the ini
    filename) tags each entry so the UI can group same-named keys into per-ini
    sub-sections instead of lengthening every display name."""
    keys = {}
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    for name, lines in sections.items():
        if not name.startswith("Key"): continue
        key_combo, back_combo, ktype, cvars = None, None, None, {}
        src = None
        for line in lines:
            if src is None:
                src = line_source(line)
            if "=" not in line: continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            kl = k.lower()
            if   kl == "key":  key_combo = v
            elif kl == "back": back_combo = v
            elif kl == "type": ktype = v.lower()
            elif k.startswith("$"):
                var = k[1:].strip()
                var = canon.get(var.lower(), var)
                values = [p.strip() for p in v.split(",") if p.strip()]
                if var and values:
                    cvars[f"{var_prefix}{var}" if var_prefix else var] = values
        if ktype == "cycle" and cvars:
            label = name[3:] if name[:3].lower() == "key" else name
            keys[f"{var_prefix}{name}" if var_prefix else name] = {
                "name": label,
                "key": key_combo or "",
                "back": back_combo or "",
                "key_display": _format_key_combo(key_combo) if key_combo else "",
                "vars": cvars,
                "source": source,
                "ini_path": (src or {}).get("ini_path"),
                "section": name,
            }
    return keys


def extract_toggle_var_names(sections, var_prefix=None, toggle_keys=None,
                             canonical_vars=None):
    """Flat set of every variable driven by a cycle-type [Key...] section."""
    toggle_keys = (toggle_keys if toggle_keys is not None else
                   extract_toggle_keys(sections, var_prefix=var_prefix,
                                       canonical_vars=canonical_vars))
    return {var
            for info in toggle_keys.values()
            for var in info["vars"]}


_DEFAULT_VAR_RE = re.compile(r'^(?:global\s+)?(?:persist\s+)?\$(\w+)\s*=\s*([^,]+)$', re.I)


def extract_variable_defaults(sections, var_prefix=None, canonical_vars=None):
    """Return {variable: default_value} from `global [persist] $var = value` lines
    (comma-separated cycle-list assignments inside Key sections don't match).
    var_prefix namespaces keys so same-named vars from different ini files don't collide."""
    defaults = {}
    canon = (canonical_vars if canonical_vars is not None
             else canonical_var_names(sections))
    for lines in sections.values():
        for line in lines:
            m = _DEFAULT_VAR_RE.match(line.strip())
            if m:
                var = canon.get(m.group(1).lower(), m.group(1))
                var_key = f"{var_prefix}{var}" if var_prefix else var
                if var_key not in defaults:
                    defaults[var_key] = m.group(2).strip()
    return defaults
