"""Complete, versioned ZZZ metadata index; native reads still prove each binding."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import hashlib
import json
import os
from pathlib import Path
import queue
import threading

from app.settings import config, paths
from .runtime import check_cancelled
from .zzz_runtime import INDEX_VERSION, archives, validate_folder

MAX_BYTES = 256 * 1024 * 1024
MAX_BONE_REFERENCES = 16 * 1024 * 1024
MAX_BONE_PATHS = 1000000
MAX_BONE_PATH_LENGTH = 128 * 1024
FORMAT = 5
MAX_FILES = 2000000
LOCATOR_KEYS = ("block", "offset", "cab", "file_ordinal", "path_id")
SCAN_BATCH = 16
# Block scans are CPU-bound per process; more helpers than this stop scaling on typical disks.
MAX_SCAN_WORKERS = 4


def identity(row):
    return hashlib.sha256(json.dumps([row[key] for key in LOCATOR_KEYS], separators=(",", ":")).encode()).hexdigest()


def fingerprint(folder):
    root = validate_folder(folder)
    digest = hashlib.sha256(INDEX_VERSION.encode())
    for file in archives(root):
        info = file.stat()
        digest.update(f"{file.relative_to(root).as_posix()}|{info.st_size}|{info.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def index_path(folder):
    key = os.path.normcase(os.path.realpath(folder))
    return Path(paths.asset_index_dir()) / f"game-zzmi-{hashlib.sha256(key.encode()).hexdigest()[:32]}.json"


class IndexRows(list):
    """Indexed object rows plus the bundle locations of files holding materials or textures.

    ``files`` maps a case-folded CAB name to its (block, offset, file_ordinal, cab)
    copies, so pointers into bundles without indexed objects can still be read.
    """

    def __init__(self, rows=(), files=None):
        super().__init__(rows)
        self.files = files or {}


def _file_table(entries):
    files = {}
    for block, offset, ordinal, cab in entries:
        files.setdefault(cab.casefold(), []).append((block, offset, ordinal, cab))
    return files


def _pack_files(files):
    blocks = sorted({copy[0] for copies in files.values() for copy in copies})
    number = {block: index for index, block in enumerate(blocks)}
    entries = [[number[block], offset, ordinal, cab] for _key, copies in sorted(files.items())
               for block, offset, ordinal, cab in copies]
    return dict(blocks=blocks, entries=entries)


def _unpack_files(value):
    blocks, entries = (value.get("blocks"), value.get("entries")) if isinstance(value, dict) else (None, None)
    if (not isinstance(blocks, list) or not isinstance(entries, list) or len(entries) > MAX_FILES
            or any(not isinstance(block, str) or len(block) > 1024 for block in blocks)):
        raise ValueError("The ZZZ file table is invalid.")
    files = []
    for entry in entries:
        if (not isinstance(entry, list) or len(entry) != 4 or type(entry[0]) is not int or not 0 <= entry[0] < len(blocks)
                or type(entry[1]) is not int or entry[1] < 0 or type(entry[2]) is not int or not 0 <= entry[2] < 4096
                or not isinstance(entry[3], str) or not 0 < len(entry[3]) <= 256):
            raise ValueError("The ZZZ file table is invalid.")
        files.append((blocks[entry[0]], entry[1], entry[2], entry[3]))
    return _file_table(files)


def valid_rows(rows):
    if not isinstance(rows, list) or len(rows) > 1000000:
        return False
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str) or len(row["name"]) > 1024
                or row.get("type") not in ("Mesh", "Avatar", "AnimationClip", "AnimatorController", "SkinnedMeshRenderer")
                or not isinstance(row.get("block"), str) or len(row["block"]) > 1024
                or not isinstance(row.get("cab"), str) or len(row["cab"]) > 256
                or any(type(row.get(key)) is not int for key in ("offset", "file_ordinal", "path_id"))
                or row["offset"] < 0 or not 0 <= row["file_ordinal"] < 4096):
            return False
        shape = row.get("shape")
        if row["type"] == "Mesh" and (not isinstance(shape, list) or len(shape) > 4096 or any(
                not isinstance(item, list) or len(item) != 2 or any(type(number) is not int or not 0 <= number <= 1500000 for number in item)
                for item in shape)):
            return False
        key = identity(row)
        if key in seen:
            return False
        seen.add(key)
    return True


def _bone_lists(rows):
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("The ZZZ metadata index is incomplete.")
        renderer = row.get("renderer") if row.get("type") == "SkinnedMeshRenderer" else None
        if isinstance(renderer, dict) and "bone_path_ids" in renderer:
            raise ValueError("The ZZZ renderer bone paths are invalid.")
        if not isinstance(renderer, dict) or "bone_paths" not in renderer:
            continue
        bones = renderer["bone_paths"]
        if (not isinstance(bones, list) or len(bones) > 4096
                or any(bone is not None and (not isinstance(bone, str) or len(bone) > MAX_BONE_PATH_LENGTH) for bone in bones)):
            raise ValueError("The ZZZ renderer bone paths are invalid.")
        total += len(bones)
        if total > MAX_BONE_REFERENCES:
            raise ValueError("The ZZZ renderer bone paths exceed their limits.")
        yield renderer, bones


def _pack_rows(rows):
    # Preserve exact hierarchy strings and their order, sharing repeated paths
    # across renderers instead of serializing every hierarchy for every copy.
    packed = [{**row, "renderer": dict(row["renderer"])} if isinstance(row.get("renderer"), dict) else row for row in rows]
    strings, lookup = [], {}
    for renderer, bones in _bone_lists(packed):
        ids = []
        for bone in bones:
            if bone not in lookup:
                if len(strings) >= MAX_BONE_PATHS:
                    raise ValueError("The ZZZ renderer bone paths exceed their limits.")
                lookup[bone] = len(strings)
                strings.append(bone)
            ids.append(lookup[bone])
        del renderer["bone_paths"]
        renderer["bone_path_ids"] = ids
    return packed, strings


def _unpack_rows(value):
    rows = value["rows"]
    if "bone_paths" not in value:
        # Existing unpooled format-3 indexes remain reusable without a rescan.
        if any("bone_path_ids" in row.get("renderer", {}) for row in rows if isinstance(row.get("renderer"), dict)):
            raise ValueError("The ZZZ renderer path table is missing.")
        for _renderer, _bones in _bone_lists(rows):
            pass
        return rows
    strings = value["bone_paths"]
    if (not isinstance(strings, list) or len(strings) > MAX_BONE_PATHS
            or any(bone is not None and (not isinstance(bone, str) or len(bone) > MAX_BONE_PATH_LENGTH) for bone in strings)):
        raise ValueError("The ZZZ renderer path table is invalid.")
    total = 0
    for row in rows:
        renderer = row.get("renderer")
        if not isinstance(renderer, dict) or "bone_path_ids" not in renderer:
            continue
        ids = renderer["bone_path_ids"]
        if (row["type"] != "SkinnedMeshRenderer" or "bone_paths" in renderer or not isinstance(ids, list) or len(ids) > 4096
                or any(type(i) is not int or not 0 <= i < len(strings) for i in ids)):
            raise ValueError("The ZZZ renderer path references are invalid.")
        total += len(ids)
        if total > MAX_BONE_REFERENCES:
            raise ValueError("The ZZZ renderer bone paths exceed their limits.")
        renderer["bone_paths"] = [strings[i] for i in ids]
        del renderer["bone_path_ids"]
    for _renderer, _bones in _bone_lists(rows):
        pass
    return rows


def read(folder):
    try:
        file = index_path(folder)
        if file.stat().st_size > MAX_BYTES:
            return "stale", None, 0
        value = json.loads(file.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("format") != FORMAT or not valid_rows(value.get("rows"))
                or value.get("fingerprint") != fingerprint(folder)):
            return "stale", None, 0
        rows = IndexRows(_unpack_rows(value), _unpack_files(value.get("files")))
        return "ready", rows, sum(row["type"] == "Mesh" for row in rows)
    except FileNotFoundError:
        return "missing", None, 0
    except (OSError, ValueError, TypeError):
        return "stale", None, 0


def status(folder):
    state, _index, count = read(folder)
    return dict(state=state, meshes=count)


def remove(folder):
    try:
        index_path(folder).unlink(missing_ok=True)
    except OSError:
        pass


class _Stop:
    """Cancellation for scan helpers: the job's own cancel or an abandoned scan."""

    def __init__(self, cancel):
        self.cancel, self.event = cancel, threading.Event()

    def is_set(self):
        return self.event.is_set() or self.cancel.is_set()

    def set(self):
        self.event.set()


def scan_batches(client, files, progress, cancel, consume):
    """Pass each batch's scanned rows to consume, in archive order.

    Separate helper processes scan up to four batches at once; results are still
    consumed in order, so the index stays deterministic. Any failure or
    cancellation stops every helper before the error propagates.
    """
    batches = [files[number:number + SCAN_BATCH] for number in range(0, len(files), SCAN_BATCH)]
    workers = min(len(batches), MAX_SCAN_WORKERS, max(1, (os.cpu_count() or 1) // 2))
    clone = getattr(client, "clone", None)
    if workers <= 1 or clone is None:
        for number, batch in enumerate(batches):
            progress("index", number * SCAN_BATCH, len(files))
            check_cancelled(cancel)
            consume(client.scan_many(batch))
        return
    stop = _Stop(cancel)
    idle = queue.SimpleQueue()
    helpers = []

    def run(batch):
        helper = idle.get()
        try:
            return helper.scan_many(batch)
        finally:
            idle.put(helper)

    try:
        for number in range(workers):
            work = client.work / f"scan-{number}"
            work.mkdir(exist_ok=True)
            helpers.append(clone(work, stop))
            idle.put(helpers[-1])
        with ThreadPoolExecutor(workers) as pool:
            todo = iter(batches)
            # Keep two batches per helper queued so helpers never wait on consume.
            pending = deque(pool.submit(run, batch) for batch in islice(todo, 2 * workers))
            try:
                for number in range(len(batches)):
                    progress("index", number * SCAN_BATCH, len(files))
                    check_cancelled(cancel)
                    future = pending.popleft()
                    pending.extend(pool.submit(run, batch) for batch in islice(todo, 1))
                    consume(future.result())
            finally:
                stop.set()
                for future in pending:
                    future.cancel()
    finally:
        for helper in helpers:
            helper.close()


def scan(client, folder, progress, cancel):
    root = validate_folder(folder)
    files = archives(root)
    rows = []
    entries = []
    strings = {}
    references = 0

    def consume(batch):
        nonlocal references
        entries.extend((row["block"], row["offset"], row["file_ordinal"], row["cab"]) for row in batch if row["type"] == "File")
        if len(entries) > MAX_FILES:
            raise ValueError("The ZZZ file table exceeds its limits.")
        batch = [row for row in batch if row["type"] != "File"]
        for renderer, bones in _bone_lists(batch):
            references += len(bones)
            if references > MAX_BONE_REFERENCES:
                raise ValueError("The ZZZ renderer bone paths exceed their limits.")
            renderer["bone_paths"] = [strings.setdefault(bone, bone) for bone in bones]
            if len(strings) > MAX_BONE_PATHS:
                raise ValueError("The ZZZ renderer bone paths exceed their limits.")
        rows.extend(batch)

    scan_batches(client, files, progress, cancel, consume)
    progress("index", len(files), len(files))
    if not valid_rows(rows):
        raise ValueError("The ZZZ metadata index is incomplete or ambiguous.")
    return IndexRows(rows, _file_table(entries))


def build(client, folder, progress, cancel):
    current = fingerprint(folder)
    rows = scan(client, folder, progress, cancel)
    if fingerprint(folder) != current:
        raise ValueError("The ZZZ installation changed during indexing. Retry after the game update finishes.")
    packed, strings = _pack_rows(rows)
    payload = json.dumps(dict(format=FORMAT, fingerprint=current, rows=packed, bone_paths=strings,
                              files=_pack_files(rows.files)), separators=(",", ":")).encode()
    if len(payload) > MAX_BYTES:
        raise ValueError("The ZZZ index exceeds its size limit.")
    file = index_path(folder)
    file.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes_atomic(str(file), payload)
    return rows, sum(row["type"] == "Mesh" for row in rows)


def ensure(client, folder, progress, cancel, rebuild=False):
    if not rebuild:
        state, rows, _count = read(folder)
        if state == "ready":
            return rows
    return build(client, folder, progress, cancel)[0]
