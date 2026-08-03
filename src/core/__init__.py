"""Domain/business-logic package: ini reading, writing and mesh building.

GUI-free by design (no pywebview import anywhere in here) so every module can
be exercised headlessly from tests and scripts. The `app` package is the only
thing that imports from here to bridge it to the UI.

Layout:
    ini_condition.py  condition syntax tree: parse, DNF simplify, partial eval
    ini_document.py   lossless line-preserving ini model (WRITE path)
    ini_parser.py     ini parsing, build_draw_groups (READ path)
    mesh_builder.py   buffer readers, build_mesh_payload
    toggle_editor.py  toggle add/edit/delete: mutates an IniDocument
    record_editor.py  record mode: rewrites if/elif/endif gates from recorded
                      per-position visibility
"""
