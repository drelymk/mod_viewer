"""Read and edit the authoritative in-memory INI documents.

No function here writes disk. ``edit_session.export`` remains the only write
boundary and supplies the existing backup/atomic-save guarantees.
"""

import traceback

from . import edit_session


def _unexpected_error():
    traceback.print_exc()
    return {"error": "Unexpected backend error. See the application log for details."}


def list_inis(mod_dir):
    return [{"value": name, "label": name,
             "dirty": name in edit_session.dirty_documents(mod_dir)}
            for name in edit_session.list_documents(mod_dir)]


def get_text(mod_dir, ini_name):
    try:
        key, doc = edit_session.document(mod_dir, ini_name)
        return {"ok": True, "ini": key, "text": edit_session.editable_text(doc),
                "dirty": key in edit_session.dirty_documents(mod_dir)}
    except KeyError as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()


def update_text(mod_dir, ini_name, text):
    try:
        changed = edit_session.update_text(mod_dir, ini_name, text)
        return {"ok": True, "ini": ini_name, "changed": changed,
                "pending": edit_session.has_pending(mod_dir)}
    except KeyError as exc:
        return {"error": str(exc)}
    except Exception:
        return _unexpected_error()
