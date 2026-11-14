"""Build the small direct CUE4Parse helper; never executed by runtime extraction."""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "native/game-extract"
PARSER_COMMIT = "cb909733773daea9015a67f6955d6f2ec24d3629"
VERSION = "direct-1-" + PARSER_COMMIT[:12]
PINS = {
    "parser": (
        f"https://codeload.github.com/FabianFG/CUE4Parse/zip/{PARSER_COMMIT}",
        "c9e44e1c2ce76d4332d97e75b2d16fb99120a7c521ca24b88c0361402842792f",
    ),
    "acl": (
        "https://codeload.github.com/nfrechette/acl/zip/414689d5cff4286a7898487a46dc5e48005d38da",
        "39b71afe930388067b127d8deaf7cd00021b89b8428d73664af06ae1551a3fda",
    ),
    "rtm": (
        "https://codeload.github.com/nfrechette/rtm/zip/d7982f2b2524feeb322f424b29cf43df30b5d5b7",
        "734c5665f2b49d6213b6e235c20b741aa73c48ca2ffdf390ea11bccf75eb4c19",
    ),
    "oodle": (
        "https://github.com/WorkingRobot/OodleUE/releases/download/2026-06-04-1357/clang-cl-x64-release.zip",
        "37f816a3e8f5a65a11f6defc8c28c0508287602767741ba4803c6a21a62a877b",
    ),
    "zlib": (
        "https://github.com/NotOfficer/Zlib-ng.NET/releases/download/1.0.0/zlib-ng2.dll.gz",
        "e11f814f64821c482fb81c05fb20d9e5a3be0feb0e2f8fd9480d1f341163e2c1",
    ),
}


def download(name, cache):
    url, digest = PINS[name]
    target = cache / (name + ".download")
    if (
        not target.is_file()
        or hashlib.sha256(target.read_bytes()).hexdigest() != digest
    ):
        with urlopen(url, timeout=60) as response:
            data = response.read(128 * 1024 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Pinned {name} download checksum mismatch")
        target.write_bytes(data)
    return target.read_bytes()


def source(name, cache):
    data = download(name, cache)
    destination = cache / name
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            target = (destination / entry.filename.replace("\\", "/")).resolve()
            if (
                not target.is_relative_to(destination.resolve())
                or entry.external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise ValueError("Unsafe source archive member")
        archive.extractall(destination)
    return next(path for path in destination.iterdir() if path.is_dir())


def run(command, **options):
    subprocess.run([str(part) for part in command], check=True, **options)


def dependency_notices(package_cache, output):
    """Keep upstream license texts and package metadata alongside the helper."""
    notices = output / "licenses/packages"
    inventory = []
    for package in sorted(package_cache.glob("*/*/*.nupkg")):
        with zipfile.ZipFile(package) as archive:
            spec_name = next(
                name for name in archive.namelist() if name.endswith(".nuspec")
            )
            spec = ET.fromstring(archive.read(spec_name))
            metadata = next(node for node in spec if node.tag.endswith("metadata"))
            fields = {node.tag.rsplit("}", 1)[-1]: node.text for node in metadata}
            identity = fields["id"] + "-" + fields["version"]
            directory = notices / identity
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "package.nuspec").write_bytes(archive.read(spec_name))
            for name in archive.namelist():
                leaf = Path(name).name
                if any(
                    term in leaf.lower() for term in ("license", "notice", "copying")
                ):
                    (directory / leaf).write_bytes(archive.read(name))
            inventory.append(
                {
                    key: fields.get(key)
                    for key in ("id", "version", "license", "licenseUrl", "projectUrl")
                }
            )
    (output / "licenses/packages.json").write_text(
        json.dumps(inventory, indent=2) + "\n", encoding="utf-8"
    )


def build(dotnet="dotnet", *, update_locks=False, verify_format=False):
    cache = ROOT / ".quality-reports/game-extract-build"
    cache.mkdir(parents=True, exist_ok=True)
    parser, acl, rtm = (source(name, cache) for name in ("parser", "acl", "rtm"))
    environment = {
        **os.environ,
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_CLI_HOME": str(cache / "dotnet-home"),
        "NUGET_PACKAGES": str(cache / "nuget"),
        "CUE4ParseRoot": str(parser),
    }
    # Preserve the established wrapper's security patch without changing parser code.
    (parser / "Directory.Build.targets").write_text(
        '<Project><ItemGroup><PackageReference Include="Microsoft.Bcl.Memory" '
        'VersionOverride="9.0.14" /></ItemGroup></Project>\n',
        encoding="utf-8",
    )
    projects = {
        "viewer": PROJECT,
        "parser": parser / "CUE4Parse",
        "conversion": parser / "CUE4Parse-Conversion",
    }
    locks = PROJECT / "locks"
    for name, directory in projects.items():
        if not update_locks:
            shutil.copyfile(locks / (name + ".json"), directory / "packages.lock.json")
    properties = [
        f"-p:CUE4ParseRoot={parser}",
        "-p:CUE4PARSE_SKIP_NATIVE=true",
        "-p:RestorePackagesWithLockFile=true",
    ]
    run(
        [dotnet, "restore", *properties, *([] if update_locks else ["--locked-mode"])],
        cwd=PROJECT,
        env=environment,
    )
    if update_locks:
        locks.mkdir(exist_ok=True)
        for name, directory in projects.items():
            (locks / (name + ".json")).write_text(
                (directory / "packages.lock.json").read_text(encoding="utf-8").rstrip()
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
    if verify_format:
        run(
            [
                dotnet,
                "format",
                "whitespace",
                "--no-restore",
                "--verify-no-changes",
                "--include",
                "Program.cs",
            ],
            cwd=PROJECT,
            env=environment,
        )
    decoder = cache / "decoder"
    run(
        [
            "cmake",
            "-S",
            PROJECT / "decoder",
            "-B",
            decoder,
            f"-DPARSER={parser}",
            f"-DACL={acl}",
            f"-DRTM={rtm}",
        ]
    )
    run(["cmake", "--build", decoder, "--config", "Release"])
    output = Path(tempfile.mkdtemp(prefix="publish-", dir=cache))
    run(
        [dotnet, "publish", "-c", "Release", "--no-restore", "-o", output, *properties],
        cwd=PROJECT,
        env=environment,
    )
    native = next(decoder.rglob("CUE4Parse-Natives.dll"))
    shutil.copyfile(native, output / native.name)
    with zipfile.ZipFile(io.BytesIO(download("oodle", cache))) as archive:
        (output / "oodle-data-shared.dll").write_bytes(
            archive.read("bin/oodle-data-shared.dll")
        )
    (output / "zlib-ng2.dll").write_bytes(gzip.decompress(download("zlib", cache)))
    notices = output / "licenses"
    notices.mkdir(exist_ok=True)
    for name, directory in (("CUE4Parse", parser), ("ACL", acl), ("RTM", rtm)):
        license_file = next(directory.glob("LICENSE*"))
        shutil.copyfile(license_file, notices / (name + "-LICENSE.txt"))
    shutil.copyfile(
        ROOT / "docs/THIRD_PARTY_NOTICES.md", notices / "THIRD_PARTY_NOTICES.md"
    )
    dependency_notices(Path(environment["NUGET_PACKAGES"]), output)
    run([output / "ModViewer.GameExtract.exe", "--self-test"])
    files = {
        path.relative_to(output).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "runtime.json"
    }
    (output / "runtime.json").write_text(
        json.dumps(
            {"version": VERSION, "parser": PARSER_COMMIT, "files": files}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    destination = ROOT / "src/assets/game-extract"
    # Replace only this generated asset directory after all build checks pass.
    if not destination.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Generated helper directory escaped the workspace")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(output, destination)
    print(f"Built {VERSION}: {len(files)} bundled files")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dotnet", default="dotnet")
    ap.add_argument(
        "--update-locks",
        action="store_true",
        help="explicit dependency-lock maintenance",
    )
    ap.add_argument("--verify-format", action="store_true")
    args = ap.parse_args()
    build(args.dotnet, update_locks=args.update_locks, verify_format=args.verify_format)


if __name__ == "__main__":
    main()
