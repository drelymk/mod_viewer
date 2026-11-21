"""Genshin Impact archive validation and the shared bounded native transport."""

from pathlib import Path

from . import zzz_runtime

DATA = "GenshinImpact_Data"
BLOCK_DIRS = (DATA + "/StreamingAssets/AssetBundles/blocks", DATA + "/Persistent/AssetBundles/blocks")
# Discovery metadata has its own version; clip-only helper changes keep a ready index.
# gi-2 adds SkinnedMeshRenderer routes for facial curves.
INDEX_VERSION = "gi-2-" + zzz_runtime.PARSER_COMMIT[:12]
ensure_runtime = zzz_runtime.ensure_runtime


def validate_folder(folder):
    requested = Path(folder).absolute()
    root = requested.resolve()
    if root != requested:
        raise ValueError("Choose the Genshin Impact game folder containing GenshinImpact_Data/StreamingAssets/AssetBundles/blocks.")
    if not (root / BLOCK_DIRS[0]).is_dir():
        if (root / "YuanShen_Data").is_dir():
            raise ValueError("Only the global Genshin Impact client layout (GenshinImpact_Data) is supported.")
        raise ValueError("Choose the Genshin Impact game folder containing GenshinImpact_Data/StreamingAssets/AssetBundles/blocks.")
    archives(root)
    return root


def archives(root):
    root = Path(root).resolve()
    effective = {}
    for name in BLOCK_DIRS:
        directory = root / name
        if not directory.exists():
            continue
        for member in (directory, *directory.parents):
            if member.is_symlink() or member.is_junction():
                raise ValueError("Game archives must not use linked directories.")
            if member == root:
                break
        for sub in sorted(directory.iterdir()):
            if not sub.is_dir():
                continue
            if sub.is_symlink() or sub.is_junction():
                raise ValueError("Game archives must not use linked directories.")
            for file in sorted(sub.glob("*.blk")):
                if file.is_symlink() or not file.resolve().is_relative_to(root) or not file.is_file():
                    raise ValueError("Game archives must remain inside the selected game folder.")
                # Persistent files are processed last and replace StreamingAssets entries with the same relative path.
                effective[file.relative_to(directory).as_posix().casefold()] = file
    if not effective:
        raise ValueError("The selected Genshin Impact installation contains no block archives.")
    return sorted(effective.values())


class Client(zzz_runtime.Client):
    index_files = False

    def __init__(self, executable, folder, work, cancel):
        self.root = validate_folder(folder)
        self.executable, self.work, self.cancel = Path(executable), Path(work), cancel
        self.serial, self.process = 0, None

    def command(self):
        return [str(self.executable), "--gi", str(self.root), "--work", str(self.work)]
