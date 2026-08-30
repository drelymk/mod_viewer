"""
Build 3DMigoto Mod Viewer into a standalone portable app.
---------------------------------------------------------
Produces a self-contained executable that needs no Python install and, because
Three.js is vendored, no internet connection.

    py -3 build.py                # single-file portable .exe (default)
    py -3 build.py --onedir       # folder build (much faster startup)
    py -3 build.py --skip-deps    # don't touch pip
    py -3 build.py --refresh-assets   # re-download Three.js
    py -3 build.py --rebuild-bootloader   # recompile PyInstaller's bootloader

--rebuild-bootloader compiles PyInstaller's bootloader from source in an
isolated venv. The stock prebuilt bootloader is byte-identical across every
PyInstaller app in the world, so antivirus heuristics key on it; a locally
compiled one usually clears those false positives. Requires a C compiler
(MinGW-w64 gcc or MSVC).

Output lands in dist/.
"""

import argparse
import configparser
import glob
import hashlib
import ntpath
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from app.settings.paths import APP_VERSION

ASSETS = os.path.join(HERE, "assets")
WEB = os.path.join(HERE, "web")
FEATURES_FILE = os.path.join(HERE, "features.ini")
# Generated at build time from FEATURES_FILE and deleted again right after --
# see resolve_features()/write_baked_features()/clean_baked_features(). Never
# bundled as data and never committed (see .gitignore): the resolved flags are
# compiled into the exe as ordinary Python code instead of shipping
# features.ini itself, so there's no plain, end-user-editable config file
# sitting next to a distributed build.
BAKED_FEATURES_MODULE = os.path.join(
    HERE, "app", "settings", "_baked_features.py")
APP_NAME = "3DMigotoModViewer"
APP_PUBLISHER = "3DMigoto Mod Viewer"
APP_DESCRIPTION = "3DMigoto Mod Viewer - preview mod meshes in 3D"
ENTRY = "viewer_app.py"
# Actual PyInstaller --name / dist/ output name -- version is baked into the
# build folder and exe filename so more than one build can sit in dist/ at once.
BUILD_NAME = f"{APP_NAME}-{APP_VERSION}"
REQUIREMENTS_FILE = os.path.abspath(os.path.join(HERE, os.pardir,
                                                 "requirements.txt"))
MIN_PYTHON = (3, 10, 1)

# Pinned so the compiled bootloader always matches the PyInstaller doing the
# packaging — a mismatch produces subtly broken executables.
PYINSTALLER_VERSION = "6.21.0"
PYINSTALLER_SDIST_SHA256 = (
    "bb9fab705983e393a2d1cac77d6972513057ad800215fd861dc15ff5272e98fd")

# Venv used only for --rebuild-bootloader, so patching PyInstaller's bootloader
# never touches the user's system Python.
VENV_DIR = os.path.join(HERE, ".venv-build")

# Kept in sync with THREE_VERSION in app/runtime/server.py. The layout below mirrors the
# CDN so the import map can resolve the WebGPU and TSL entry points entirely
# offline.
THREE_VERSION = "0.185.0"
ASSET_FILES = {
    "three.core.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.core.js",
        "sha256": "78b2c4218834ca8670547ed2315bfc21a00ff4dc3403bbffc8c31493d31d14de",
    },
    "three.webgpu.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.webgpu.js",
        "sha256": "eb77cbb7759853dd9cd8661c38795ff4fcd0182a8852f942f687b9954f87d5eb",
    },
    "three.tsl.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/build/three.tsl.js",
        "sha256": "c3844718a7becd4d2d10fef29a503e6a8789bd1636e2e62767f2cf00fa56f8c1",
    },
    "addons/controls/ArcballControls.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/controls/ArcballControls.js",
        "sha256": "f80db7fe66685fc7f5438e3be44fed6684aed33a7b92b2442fe6958291156fb3",
    },
    "addons/objects/SkyMesh.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/objects/SkyMesh.js",
        "sha256": "a44cb7c543d04b2690b0079b8b473da9a36660629b2eb091f7eab8b4111d7b7a",
    },
    "addons/tsl/display/GTAONode.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/tsl/display/GTAONode.js",
        "sha256": "516e86dc398b8e2d93f693a168144fdd7420f261a72aaa7da6eb7f110cc8e3ef",
    },
    "addons/tsl/display/BloomNode.js": {
        "url": f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}/examples/jsm/tsl/display/BloomNode.js",
        "sha256": "f80f40d013f4d94aa089c68c673cae6a8ab45fb290970a696bc1dded312e3fc8",
    },
}


def log(msg):
    print(f"[build] {msg}", flush=True)


def run(cmd, env=None, cwd=None):
    log(" ".join(cmd))
    subprocess.check_call(cmd, env=env, cwd=cwd)


def install_deps():
    run([sys.executable, "-m", "pip", "install", "--upgrade", "-r",
         REQUIREMENTS_FILE])
    run([sys.executable, "-m", "pip", "install",
         f"pyinstaller=={PYINSTALLER_VERSION}"])


def sha256_bytes(data):
    """Return the lowercase SHA-256 digest for an in-memory artifact."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    """Return the lowercase SHA-256 digest for a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_path(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _within_path(target, root):
    try:
        return os.path.commonpath([target, root]) == root
    except ValueError:
        return False


def safe_extract_tar(tar, destination):
    """Extract only regular files and directories under *destination*."""
    root = _canonical_path(destination)
    for member in tar.getmembers():
        name = member.name
        if (os.path.isabs(name) or ntpath.isabs(name)
                or os.path.splitdrive(name)[0]
                or ntpath.splitdrive(name)[0]):
            raise RuntimeError(f"unsafe archive member: {name!r}")

        # Validate both separators so a Windows archive cannot smuggle a
        # traversal through a POSIX build host, or vice versa.
        normalized_name = name.replace("\\", "/")
        target = _canonical_path(os.path.join(root, normalized_name))
        if not _within_path(target, root):
            raise RuntimeError(f"unsafe archive member: {name!r}")

        if (member.issym() or member.islnk() or member.isdev()
                or not (member.isfile() or member.isdir())):
            raise RuntimeError(f"unsupported archive member: {name!r}")

        # Some PyInstaller test fixtures contain names Windows cannot create.
        # All security checks above must happen before this compatibility path.
        try:
            tar.extract(member, destination)
        except (OSError, ValueError):
            continue


# ── Bootloader rebuild ────────────────────────────────────────────────────────
# Every stock PyInstaller install ships the *same* prebuilt bootloader binary.
# Because that exact byte pattern appears in real malware built with
# PyInstaller, heuristic AV engines flag it (e.g. Trend Micro
# "Troj.Win32.TRX.*"). Recompiling it locally yields a binary that doesn't match
# those signatures and usually clears the false positive.

# WinLibs MinGW-w64 installs here via winget but doesn't join PATH immediately.
_WINGET_MINGW_GLOB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
    "BrechtSanders.WinLibs*", "mingw64", "bin")


def find_compiler():
    """Return (kind, extra_path_dir) for an available C compiler, else None.

    Prefers gcc since it needs no environment priming; MSVC is only reported
    when cl.exe is already on PATH (i.e. a Developer Command Prompt).
    """
    if shutil.which("gcc"):
        return "gcc", None
    for cand in glob.glob(_WINGET_MINGW_GLOB):
        if os.path.isfile(os.path.join(cand, "gcc.exe")):
            return "gcc", cand
    if shutil.which("cl"):
        return "msvc", None
    return None


def _venv_python():
    return os.path.join(VENV_DIR, "Scripts" if os.name == "nt" else "bin", "python.exe"
                        if os.name == "nt" else "python")


def _compile_bootloader(py, env, workdir):
    """Download the pinned PyInstaller sdist and compile its bootloader.

    Returns the directory holding the freshly built binaries.

    Note: `pip install --no-binary pyinstaller` is NOT sufficient — the sdist
    ships the prebuilt bootloaders and reuses them, producing byte-identical
    output. waf must be invoked explicitly.
    """
    log(f"downloading PyInstaller {PYINSTALLER_VERSION} source")
    run([py, "-m", "pip", "download", "--no-binary", "pyinstaller", "--no-deps",
         "--no-cache-dir", "-d", workdir, f"pyinstaller=={PYINSTALLER_VERSION}"], env=env)

    expected_tarball = os.path.join(
        workdir, f"pyinstaller-{PYINSTALLER_VERSION}.tar.gz")
    tarballs = [path for path in glob.glob(expected_tarball)
                if os.path.isfile(path)]
    if len(tarballs) != 1:
        raise RuntimeError(
            "expected exactly one PyInstaller source tarball: "
            f"{expected_tarball}")
    actual_hash = sha256_file(tarballs[0])
    if actual_hash != PYINSTALLER_SDIST_SHA256:
        raise RuntimeError(
            "PyInstaller source SHA-256 mismatch: "
            f"expected {PYINSTALLER_SDIST_SHA256}, got {actual_hash}")

    log("extracting source")
    with tarfile.open(tarballs[0]) as tf:
        safe_extract_tar(tf, workdir)

    src = os.path.join(workdir, f"pyinstaller-{PYINSTALLER_VERSION}")
    bootloader_src = os.path.join(src, "bootloader")
    if not os.path.isdir(bootloader_src):
        raise RuntimeError(f"bootloader sources missing at {bootloader_src}")

    built = os.path.join(src, "PyInstaller", "bootloader", "Windows-64bit-intel")
    before = {}
    for f in glob.glob(os.path.join(built, "*.exe")):
        with open(f, "rb") as fh:
            before[os.path.basename(f)] = fh.read()

    log("compiling bootloader (this takes a minute)")
    run([py, "waf", "all", "--gcc"], env=env, cwd=bootloader_src)

    # Prove the binaries actually changed — a silent no-op here would leave the
    # stock, AV-flagged bootloader in place while appearing to succeed.
    changed = []
    for f in sorted(glob.glob(os.path.join(built, "*.exe"))):
        with open(f, "rb") as fh:
            data = fh.read()
        name = os.path.basename(f)
        if before.get(name) != data:
            changed.append(f"{name} ({len(data):,} B)")
    if not changed:
        raise RuntimeError("bootloader compile produced no changes — still the stock binary")
    log("rebuilt: " + ", ".join(changed))
    return built


def prepare_bootloader_venv(force=False):
    """Create an isolated venv whose PyInstaller uses a locally compiled
    bootloader, and return its python executable."""
    py = _venv_python()
    stamp = os.path.join(VENV_DIR, "bootloader.stamp")
    # Compiling takes minutes, so reuse a venv that already holds a custom
    # bootloader for this exact PyInstaller version.
    if not force and os.path.isfile(py) and os.path.isfile(stamp):
        with open(stamp, encoding="utf-8") as fh:
            if fh.read().strip() == PYINSTALLER_VERSION:
                log(f"reusing build venv with custom bootloader ({VENV_DIR})")
                return py

    found = find_compiler()
    if not found:
        raise RuntimeError(
            "no C compiler found — --rebuild-bootloader needs MinGW-w64 or MSVC.\n"
            "        install with:  winget install BrechtSanders.WinLibs.POSIX.UCRT\n"
            "        then open a new terminal so PATH updates.")
    kind, extra_path = found
    log(f"using {kind} compiler" + (f" from {extra_path}" if extra_path else ""))
    if kind == "msvc":
        raise RuntimeError(
            "MSVC detected, but waf needs gcc for this path.\n"
            "        install MinGW-w64:  winget install BrechtSanders.WinLibs.POSIX.UCRT")

    env = dict(os.environ)
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")

    if os.path.isdir(VENV_DIR):
        log("removing stale build venv")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
    log("creating isolated build venv")
    run([sys.executable, "-m", "venv", VENV_DIR])
    py = _venv_python()

    run([py, "-m", "pip", "install", "--upgrade", "-q", "pip", "setuptools", "wheel"], env=env)
    run([py, "-m", "pip", "install", "-q", "-r", REQUIREMENTS_FILE], env=env)
    run([py, "-m", "pip", "install", "-q",
         f"pyinstaller=={PYINSTALLER_VERSION}"], env=env)

    with tempfile.TemporaryDirectory(prefix="pyi-bootloader-") as workdir:
        built = _compile_bootloader(py, env, workdir)
        dest = subprocess.check_output(
            [py, "-c",
             "import os,PyInstaller;print(os.path.join(os.path.dirname("
             "PyInstaller.__file__),'bootloader','Windows-64bit-intel'))"],
            env=env, text=True).strip()
        log(f"installing bootloader into {dest}")
        for f in glob.glob(os.path.join(built, "*.exe")):
            shutil.copy2(f, dest)

    with open(stamp, "w", encoding="utf-8") as fh:
        fh.write(PYINSTALLER_VERSION)
    return py


def fetch_assets(refresh=False):
    """Download Three.js so the built app runs fully offline."""
    for rel, spec in ASSET_FILES.items():
        dest = os.path.join(ASSETS, *rel.split("/"))
        if os.path.isfile(dest) and not refresh:
            actual = sha256_file(dest)
            if actual != spec["sha256"]:
                raise RuntimeError(
                    f"asset {rel} SHA-256 mismatch: expected "
                    f"{spec['sha256']}, got {actual}; use --refresh-assets "
                    "if replacement is intentional")
            log(f"asset verified: {rel}")
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        log(f"downloading {rel}")
        with urllib.request.urlopen(spec["url"], timeout=60) as resp:
            data = resp.read()
        if len(data) < 1024:
            raise RuntimeError(f"suspiciously small download for {rel} ({len(data)} bytes)")
        actual = sha256_bytes(data)
        if actual != spec["sha256"]:
            raise RuntimeError(
                f"downloaded asset {rel} SHA-256 mismatch: expected "
                f"{spec['sha256']}, got {actual}; existing asset was not "
                "replaced")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="wb", dir=os.path.dirname(dest),
                    prefix=f".{os.path.basename(dest)}.",
                    suffix=".tmp", delete=False) as fh:
                temporary = fh.name
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, dest)
            temporary = None
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
        log(f"  {len(data):,} bytes")


def verify_assets():
    missing = []
    mismatched = []
    for rel, spec in ASSET_FILES.items():
        path = os.path.join(ASSETS, *rel.split("/"))
        if not os.path.isfile(path):
            missing.append(rel)
        else:
            actual = sha256_file(path)
            if actual != spec["sha256"]:
                mismatched.append(
                    f"{rel} (expected {spec['sha256']}, got {actual})")
    if missing:
        raise RuntimeError(f"missing vendored assets: {missing}")
    if mismatched:
        raise RuntimeError(f"vendored asset SHA-256 mismatch: {mismatched}")


def verify_web():
    """The UI is data, not code, so PyInstaller can't catch a missing file for
    us — a typo'd path would only surface as a blank window at runtime."""
    required = [
        "index.html", "css/app.css", "js/main.js",
        "js/scene/environment.js",
        "lib/ace/ace.js", "lib/ace/mode-ini.js",
        "lib/ace/theme-tomorrow_night.js", "lib/ace/ext-searchbox.js",
        "lib/ace/LICENSE",
    ]
    missing = [rel for rel in required
               if not os.path.isfile(os.path.join(WEB, *rel.split("/")))]
    if missing:
        raise RuntimeError(f"missing web UI files: {missing}")


def verify_features():
    """features.ini controls which optional actions this specific build
    exposes (app/settings/features.py); a silently-missing file would ship a build
    that shows everything, which may not be what whoever configured it
    intended — fail loudly instead."""
    if not os.path.isfile(FEATURES_FILE):
        raise RuntimeError(f"missing feature-flags config: {FEATURES_FILE}")


_FEATURE_DEFAULTS = {
    "export": True,
    "modify_toggle": True,
    "open_disabled_mod": True,
}


def resolve_features(ini_path):
    """Parse features.ini into the resolved optional feature flags.

    This is the one place that ini gets read — the resolved booleans are
    baked into the exe at build time (write_baked_features()) instead of
    shipping the file itself, so app/settings/features.py no longer parses it at
    runtime. A missing file, missing section, missing key, or malformed value
    each independently fall back to True (shown) — a broken config should
    never silently hide a feature nobody deliberately disabled.
    """
    flags = dict(_FEATURE_DEFAULTS)
    parser = configparser.ConfigParser()
    read_ok = parser.read(ini_path, encoding="utf-8")
    if not read_ok or not parser.has_section("features"):
        return flags
    for key in flags:
        # ConfigParser lowercases option names on read (its default
        # optionxform), so "Export"/"Modify_Toggle" in the ini file are looked
        # up here in lowercase to match.
        if parser.has_option("features", key):
            try:
                flags[key] = parser.getboolean("features", key)
            except ValueError:
                pass  # keep the safe default
    return flags


def write_baked_features(flags, path=BAKED_FEATURES_MODULE):
    """Generate a tiny module holding the resolved flags as plain constants.

    Written into app/settings/ right before invoking PyInstaller so its normal static
    import analysis picks up app/settings/features.py's `from . import _baked_features`
    and compiles this module into the exe like any other app code — the
    flags end up in the same bytecode archive as the rest of the app's logic,
    not as a loose file an end user could open in a text editor.
    """
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Auto-generated by build.py from features.ini -- do not edit or\n"
            "# commit this file. It is deleted again immediately after each\n"
            "# build (see build()'s finally); .gitignore also excludes it as\n"
            "# a safety net should a build ever be interrupted first.\n"
            f"EXPORT = {flags['export']!r}\n"
            f"MODIFY_TOGGLE = {flags['modify_toggle']!r}\n"
            f"OPEN_DISABLED_MOD = {flags['open_disabled_mod']!r}\n"
        )


def clean_baked_features(path=BAKED_FEATURES_MODULE):
    if os.path.isfile(path):
        os.remove(path)


def write_version_file():
    """Emit a Windows version resource.

    An executable with no CompanyName/ProductName/FileVersion is a real input to
    antivirus heuristics — "unsigned, no metadata, self-extracting" scores far
    worse than the same binary with proper identification. This does not
    eliminate false positives (only code signing reliably does), but it removes
    one of the cheap signals.
    """
    parts = APP_VERSION.split(".")
    while len(parts) < 4:
        parts.append("0")
    quad = ", ".join(parts[:4])
    path = os.path.join(HERE, "build", "version_info.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName',      {APP_PUBLISHER!r}),
        StringStruct('FileDescription',  {APP_DESCRIPTION!r}),
        StringStruct('FileVersion',      {APP_VERSION!r}),
        StringStruct('InternalName',     {APP_NAME!r}),
        StringStruct('OriginalFilename', {BUILD_NAME + '.exe'!r}),
        StringStruct('ProductName',      {APP_PUBLISHER!r}),
        StringStruct('ProductVersion',   {APP_VERSION!r}),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")
    return path


def clean():
    for d in ("build", "dist"):
        p = os.path.join(HERE, d)
        if os.path.isdir(p):
            log(f"removing {d}/")
            shutil.rmtree(p, ignore_errors=True)
    spec = os.path.join(HERE, f"{BUILD_NAME}.spec")
    if os.path.isfile(spec):
        os.remove(spec)


def build(onedir=False, console=False, python=None):
    # Baked into app/settings/_baked_features.py (compiled into the exe as ordinary
    # Python code) rather than bundled as a data file — see
    # resolve_features()/write_baked_features(); cleaned up in the finally
    # below regardless of whether PyInstaller succeeds.
    write_baked_features(resolve_features(FEATURES_FILE))
    try:
        cmd = [
            python or sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean",
            "--name", BUILD_NAME,
            "--onedir" if onedir else "--onefile",
            "--console" if console else "--windowed",
            # Identify the binary. Blank version metadata is an antivirus heuristic
            # signal, so always stamp it.
            "--version-file", write_version_file(),
            # UPX-packed executables are flagged aggressively by AV engines; never
            # compress, even if upx happens to be on PATH.
            "--noupx",
            # Three.js, served to the webview from a localhost port at runtime.
            "--add-data", f"{ASSETS}{os.pathsep}assets",
            # The HTML/CSS/JS UI, served from that same port.
            "--add-data", f"{WEB}{os.pathsep}web",
            # Pulled in dynamically by the WebView2 backend, so PyInstaller's static
            # analysis can miss them.
            "--hidden-import", "webview.platforms.edgechromium",
            "--hidden-import", "clr_loader",
            # Trimming these keeps the single-file payload from ballooning.
            "--exclude-module", "tkinter",
            "--exclude-module", "numpy",
            "--exclude-module", "matplotlib",
            "--exclude-module", "pytest",
            os.path.join(HERE, ENTRY),
        ]
        icon = os.path.join(HERE, "icon.ico")
        if os.path.isfile(icon):
            cmd += ["--icon", icon]
        run(cmd)
    finally:
        clean_baked_features()


def report(onedir):
    dist = os.path.join(HERE, "dist")
    target = os.path.join(dist, BUILD_NAME) if onedir else os.path.join(dist, BUILD_NAME + ".exe")
    if not os.path.exists(target):
        raise RuntimeError(f"expected build output not found: {target}")
    if onedir:
        size = sum(os.path.getsize(os.path.join(r, f))
                   for r, _, fs in os.walk(target) for f in fs)
        log(f"OK  folder: {target}  ({size / 1048576:.1f} MB)")
        log(f"    launch: {os.path.join(target, BUILD_NAME + '.exe')}")
    else:
        log(f"OK  portable exe: {target}  ({os.path.getsize(target) / 1048576:.1f} MB)")


def main():
    ap = argparse.ArgumentParser(description="Build the portable 3DMigoto Mod Viewer.")
    ap.add_argument("--onedir", action="store_true",
                    help="build a folder instead of one file (starts much faster)")
    ap.add_argument("--console", action="store_true",
                    help="keep a console window open (useful for debugging)")
    ap.add_argument("--skip-deps", action="store_true", help="skip pip install")
    ap.add_argument("--refresh-assets", action="store_true", help="re-download Three.js")
    ap.add_argument("--rebuild-bootloader", action="store_true",
                    help="recompile PyInstaller's bootloader in an isolated venv "
                         "(clears most antivirus false positives; needs a C compiler). "
                         "Reuses the existing build environment if already compiled; "
                         "remove it to redo.")
    args = ap.parse_args()

    if sys.version_info < MIN_PYTHON:
        sys.exit("Python 3.10.1+ required.")

    os.chdir(HERE)
    python = None
    if args.rebuild_bootloader:
        python = prepare_bootloader_venv()
    elif not args.skip_deps:
        install_deps()
    fetch_assets(refresh=args.refresh_assets)
    verify_assets()
    verify_web()
    verify_features()
    clean()
    build(onedir=args.onedir, console=args.console, python=python)
    report(args.onedir)
    if args.rebuild_bootloader:
        log("built with a locally compiled bootloader")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(f"[build] FAILED: command exited with {exc.returncode}")
    except Exception as exc:
        sys.exit(f"[build] FAILED: {exc}")
