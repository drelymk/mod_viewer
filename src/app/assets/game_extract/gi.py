"""Genshin Impact adapter using the shared extraction jobs and installer."""

from functools import partial

from . import gi_index, gi_pipeline, gi_runtime, zzz_native

requires_index = True

validate_folder = gi_runtime.validate_folder
index_status = gi_index.status
remove_index = gi_index.remove
build_index = gi_index.build
ensure_index = gi_index.ensure
capture = partial(zzz_native.capture, asset_type="GIMI")
build = gi_pipeline.build


def open_client(folder, work, cancel):
    return gi_runtime.Client(gi_runtime.ensure_runtime(cancel), folder, work, cancel)
