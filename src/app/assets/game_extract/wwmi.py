"""Wuthering Waves adapter for the shared extraction registry."""

from . import game_index, native, pipeline, runtime


validate_folder = runtime.find_paks
index_status = game_index.status
remove_index = game_index.remove
build_index = game_index.build
ensure_index = game_index.ensure
capture = native.capture
build = pipeline.build


def open_client(folder, work, cancel):
    return runtime.Client(runtime.ensure_runtime(cancel), folder, work, cancel)
