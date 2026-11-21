"""Wuthering Waves adapter for the shared extraction registry."""

from . import game_index, native, pipeline, runtime


validate_folder = runtime.find_paks
index_status = game_index.status
remove_index = game_index.remove
capture = native.capture
build = pipeline.build


def build_index(client, folder, progress, cancel):
    return game_index.build(client, folder, progress, cancel, game_index.shared_roots())


def ensure_index(client, folder, progress, cancel):
    return game_index.ensure(client, folder, progress, cancel, roots=game_index.shared_roots())


def open_client(folder, work, cancel):
    return runtime.Client(runtime.ensure_runtime(cancel), folder, work, cancel)
