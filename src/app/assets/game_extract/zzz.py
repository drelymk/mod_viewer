"""Zenless Zone Zero adapter using the shared extraction jobs and installer."""

from . import zzz_index, zzz_native, zzz_pipeline, zzz_runtime

requires_index = True

validate_folder = zzz_runtime.validate_folder
index_status = zzz_index.status
remove_index = zzz_index.remove
build_index = zzz_index.build
ensure_index = zzz_index.ensure
capture = zzz_native.capture
build = zzz_pipeline.build


def open_client(folder, work, cancel):
    return zzz_runtime.Client(zzz_runtime.ensure_runtime(cancel), folder, work, cancel)
