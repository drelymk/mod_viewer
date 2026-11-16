"""Pack native helpers once per file content for the portable application."""

import hashlib
import json
from pathlib import Path
import zipfile


KINDS = ("game-extract", "zzz-extract")


def pack_helpers(assets, destination):
    assets, destination = Path(assets), Path(destination)
    catalog, blobs = {}, {}
    original = 0
    for kind in KINDS:
        root = assets / kind
        manifest = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        files = manifest["files"]
        entries = {}
        for relative, expected in sorted(files.items()):
            file = (root / relative).resolve()
            if not file.is_relative_to(root.resolve()) or not file.is_file():
                raise ValueError("Unsafe or missing helper build file")
            data = file.read_bytes()
            original += len(data)
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected:
                raise ValueError("Native helper build checksum mismatch")
            # Debug symbols are unnecessary for execution. Every shipped DLL,
            # dependency license and runtime file retains its existing identity.
            if file.suffix.lower() == ".pdb":
                continue
            entries[relative] = digest
            blobs.setdefault(digest, file)
        data = (json.dumps({**manifest, "files": entries}, indent=2) + "\n").encode()
        digest = hashlib.sha256(data).hexdigest()
        entries["runtime.json"] = digest
        blobs.setdefault(digest, data)
        catalog[kind] = entries
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_LZMA) as archive:
        archive.writestr(
            "catalog.json",
            json.dumps(dict(format=1, runtimes=catalog), separators=(",", ":")),
        )
        for digest, source in sorted(blobs.items()):
            data = source if isinstance(source, bytes) else source.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("Native helper files changed during packaging")
            archive.writestr(digest, data)
    return dict(original=original, packed=destination.stat().st_size, files=len(blobs))
