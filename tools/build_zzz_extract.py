"""Build the pinned, offline ZZZ reader; never called by runtime extraction."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

from build_game_extract import (
    ROOT,
    dependency_notices,
    download,
    inputs_fingerprint,
    resolve_dotnet,
    run,
    sdk_version,
    source,
    up_to_date,
    PINS,
    MissingDotnetSdk,
)


COMMIT = "76234e9f6f5a839504f41f9612719529fe2309a5"
VERSION = "zzz-10-" + COMMIT[:12]
PINS["animestudio"] = (
    f"https://codeload.github.com/Escartem/AnimeStudio/zip/{COMMIT}",
    "46c186a273af125bf9ab9a005997809d9ebf6678f3a4f6aa917f989323039023",
)


def replace(file, before, after):
    text = file.read_text(encoding="utf-8-sig")
    if before not in text:
        raise ValueError("Pinned reader adaptation no longer applies")
    file.write_text(text.replace(before, after), encoding="utf-8")


def curate(root):
    """Keep reads scoped to one bundle; exclude image and unrelated game helpers."""
    manager = root / "AnimeStudio/AssetsManager.cs"
    text = manager.read_text(encoding="utf-8-sig")
    start = text.index(
        "            if (Game?.Type == GameType.AFKJourney && AFKJourneyUtils."
    )
    end = text.index("            var reader = new FileReader(fullName);", start)
    text = text[:start] + text[end:]
    start = text.index("        private bool LoadAfkJourneyLpakFile(")
    end = text.index("        private void LoadFile(FileReader reader)", start)
    text = text[:start] + text[end:]
    manager.write_text(text, encoding="utf-8")
    replace(manager, "if (!assetsFileListHash.Contains(reader.FileName))", "if (true)")
    replace(
        manager,
        "assetsFileIndexCache.Add(assetsFile.fileName, assetsFileList.Count - 1);",
        "assetsFileIndexCache.TryAdd(assetsFile.fileName, assetsFileList.Count - 1);",
    )
    replace(
        manager,
        "(manualOffsets != null && manualOffsets.Count > 0) && Game.Type.IsArknightsEndfieldGroup()",
        "(manualOffsets != null && manualOffsets.Count > 0)",
    )
    replace(
        manager,
        "resourceFileReaders.TryAdd(innerFile.fileName, cabReader)",
        'resourceFileReaders.TryAdd($"{originalPath ?? reader.FullPath}|{originalOffset}|{innerFile.fileName}", cabReader)',
    )
    resource = root / "AnimeStudio/ResourceReader.cs"
    text = resource.read_text(encoding="utf-8-sig")
    start = text.index("                var assetsFileDirectory =")
    end = text.index("                throw new FileNotFoundException", start)
    text = text[:start] + text[end:]
    text = text.replace(
        "TryGetValue(resourceFileName, out reader)",
        'TryGetValue($"{assetsFile.originalPath}|{assetsFile.offset}|{resourceFileName}", out reader)',
    )
    resource.write_text(text, encoding="utf-8")
    obj = root / "AnimeStudio/Classes/Object.cs"
    text = obj.read_text(encoding="utf-8-sig")
    start = text.index("            if (type == ClassIDType.Texture2D && overrides)")
    end = text.index("            return ComputeRawHash()", start)
    obj.write_text(text[:start] + text[end:], encoding="utf-8")
    mesh = root / "AnimeStudio/Classes/Mesh.cs"
    replace(
        mesh,
        "public Mesh(ObjectReader reader) : base(reader)",
        "public Mesh(ObjectReader reader, bool decodeGeometry = true) : base(reader)",
    )
    replace(
        mesh,
        "            ProcessData();",
        "            if (decodeGeometry) ProcessData();",
    )
    # The helper proves both serialized root-motion flag layouts before reading
    # paths. Keep optional authoring metadata outside the minimal extraction path.
    avatar = root / "AnimeStudio/Classes/Avatar.cs"
    replace(
        avatar,
        "public AvatarConstant(ObjectReader reader, bool isZZZ = false)",
        "public AvatarConstant(ObjectReader reader, bool isZZZ = false, bool readRootMotionFlag = true)",
    )
    replace(
        avatar,
        "if (isZZZ)\n            {\n                m_UseNextLevelForRootMotionSkeleton",
        "if (isZZZ && readRootMotionFlag)\n            {\n                m_UseNextLevelForRootMotionSkeleton",
    )
    # Genshin props are exported only when the prefab draws them by default, which
    # needs the renderer's serialized enabled flag that the reader otherwise discards.
    renderer = root / "AnimeStudio/Classes/Renderer.cs"
    replace(
        renderer,
        "        public List<PPtr<Material>> m_Materials;",
        "        public bool? m_Enabled;\n        public List<PPtr<Material>> m_Materials;",
    )
    replace(
        renderer,
        "var m_Enabled = reader.ReadBoolean();",
        "m_Enabled = reader.ReadBoolean();",
    )
    # AssetStudio normally logs parser failures and returns partial data. Jobs must fail closed.
    logger = root / "AnimeStudio/Logger.cs"
    text = logger.read_text(encoding="utf-8-sig")
    start = text.index("        public static void Error(string message, Exception e)")
    text = (
        text[:start]
        + """        public static void Error(string message, Exception e)
        {
            throw new System.IO.InvalidDataException(message, e);
        }
    }
}
"""
    )
    logger.write_text(text, encoding="utf-8")
    # Reuse WuWa's pinned Oodle decoder and ABI instead of shipping AnimeStudio's
    # unrelated Ooz compressor/decompressor distribution.
    oodle = root / "AnimeStudio/OodleHelper.cs"
    replace(
        oodle,
        '[DllImport(@"AnimeStudio.Ooz.dll", CallingConvention = CallingConvention.StdCall)]',
        '[DllImport("oodle-data-shared.dll", CallingConvention = CallingConvention.Cdecl, EntryPoint = "OodleLZ_Decompress")]',
    )
    replace(
        oodle,
        "static extern int Ooz_Decompress(ref byte compressedBuffer, int compressedBufferSize, ref byte decompressedBuffer, int decompressedBufferSize,",
        "static extern nint Ooz_Decompress(ref byte compressedBuffer, nint compressedBufferSize, ref byte decompressedBuffer, nint decompressedBufferSize,",
    )
    replace(
        oodle,
        "IntPtr rawBuffer, int rawBufferSize,",
        "IntPtr rawBuffer, nint rawBufferSize,",
    )
    replace(oodle, "numWrite = Ooz_Decompress(", "numWrite = (int)Ooz_Decompress(")


def build(dotnet, update_locks=False, verify_format=False, force=False):
    project = ROOT / "native/zzz-extract"
    dotnet = resolve_dotnet(project, dotnet)
    sdk = sdk_version(dotnet, project)
    destination = ROOT / "src/assets/zzz-extract"
    scripts = (Path(__file__),)
    if not (force or update_locks) and up_to_date(
        destination, inputs_fingerprint(project, sdk, scripts), verify_format
    ):
        print(f"Helper {VERSION} is up to date; skipping rebuild", flush=True)
        return
    cache = ROOT / ".quality-reports/zzz-extract-build"
    cache.mkdir(parents=True, exist_ok=True)
    upstream = source("animestudio", cache)
    curate(upstream)
    environment = {
        **os.environ,
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_CLI_HOME": str(cache / "dotnet-home"),
        "NUGET_PACKAGES": str(cache / "nuget"),
        "AnimeStudioRoot": str(upstream),
    }
    properties = [f"-p:AnimeStudioRoot={upstream}"]
    if not update_locks:
        for name in ("viewer", "reader"):
            shutil.copyfile(
                project / "locks" / (name + ".json"),
                project
                / (
                    "packages.lock.json"
                    if name == "viewer"
                    else "reader/UnityReader.packages.lock.json"
                ),
            )
    run(
        [
            dotnet,
            "restore",
            "ModViewer.ZzzExtract.csproj",
            *properties,
            *([] if update_locks else ["--locked-mode"]),
        ],
        cwd=project,
        env=environment,
    )
    if update_locks:
        (project / "locks").mkdir(exist_ok=True)
        for name, file in (
            ("viewer", "packages.lock.json"),
            ("reader", "reader/UnityReader.packages.lock.json"),
        ):
            (project / "locks" / (name + ".json")).write_text(
                (project / file).read_text(encoding="utf-8-sig").rstrip() + "\n",
                encoding="utf-8",
                newline="\n",
            )
    if verify_format:
        run(
            [
                dotnet,
                "format",
                "whitespace",
                "ModViewer.ZzzExtract.csproj",
                "--no-restore",
                "--verify-no-changes",
                "--include",
                "Program.cs",
                "AvatarPaths.cs",
                "MorphSources.cs",
                "BlendShapePolicy.cs",
                "GiExport.cs",
                "PropMaterials.cs",
            ],
            cwd=project,
            env=environment,
        )
    output = cache / "publish"
    if output.exists():
        if not output.resolve().is_relative_to(cache.resolve()):
            raise ValueError("Generated helper directory escaped the build cache")
        shutil.rmtree(output)
    run(
        [
            dotnet,
            "publish",
            "ModViewer.ZzzExtract.csproj",
            "-c",
            "Release",
            "--no-restore",
            "-o",
            output,
            *properties,
        ],
        cwd=project,
        env=environment,
    )
    with zipfile.ZipFile(io.BytesIO(download("oodle", cache))) as archive:
        name = next(
            name
            for name in archive.namelist()
            if name.endswith("oodle-data-shared.dll")
        )
        (output / "oodle-data-shared.dll").write_bytes(archive.read(name))
    native = upstream / "AnimeStudio.ACL/AnimeStudio.ACL.ZZZV2"
    run(
        [
            "g++",
            "-std=c++20",
            "-O2",
            "-shared",
            "-msse4.1",
            "-I.",
            "-DNDEBUG",
            "-D_AS_DLL",
            "-DACLZZZV2_EXPORTS",
            "ACLLibrary.cpp",
            "dllmain.cpp",
            "pch.cpp",
            "-o",
            output / "AnimeStudio.ACL.ZZZV2.dll",
            "-static",
            "-static-libgcc",
            "-static-libstdc++",
        ],
        cwd=native,
    )
    # Genshin database ACL decoder: the same pinned source, unmodified. GCC rejects an
    # uninstantiated template body that MSVC accepts; the flag does not change codegen.
    database = upstream / "AnimeStudio.ACL/AnimeStudio.ACL.DB"
    run(
        [
            "g++",
            "-std=c++20",
            "-O2",
            "-shared",
            "-msse4.1",
            "-I.",
            "-DNDEBUG",
            "-D_AS_DLL",
            "-DACLDB_EXPORTS",
            "-Wno-template-body",
            "dllmain.cpp",
            "-o",
            output / "AnimeStudio.ACL.DB.dll",
            "-static",
            "-static-libgcc",
            "-static-libstdc++",
        ],
        cwd=database,
    )
    (output / "licenses").mkdir(exist_ok=True)
    shutil.copyfile(upstream / "LICENSE", output / "licenses/AnimeStudio-LICENSE")
    shutil.copyfile(
        upstream / "AnimeStudio.ACL/LICENSE.txt",
        output / "licenses/AnimeStudio.ACL-LICENSE.txt",
    )
    shutil.copyfile(
        ROOT / "docs/THIRD_PARTY_NOTICES.md", output / "licenses/THIRD_PARTY_NOTICES.md"
    )
    # The decoder embeds its own ACL/RTM license headers; retain those sources' notices.
    for file in (*native.rglob("LICENSE*"), *database.rglob("LICENSE*")):
        shutil.copyfile(
            file, output / "licenses" / (file.parent.name + "-" + file.name)
        )
    dependency_notices(cache / "nuget", output)
    run([output / "ModViewer.ZzzExtract.exe", "--self-test"])
    files = {
        file.relative_to(output).as_posix(): hashlib.sha256(
            file.read_bytes()
        ).hexdigest()
        for file in sorted(output.rglob("*"))
        if file.is_file() and file.name != "runtime.json"
    }
    (output / "runtime.json").write_text(
        json.dumps(
            dict(
                version=VERSION,
                parser=COMMIT,
                inputs=inputs_fingerprint(project, sdk, scripts),
                format_verified=bool(verify_format),
                files=files,
            ),
            indent=2,
        )
        + "\n"
    )
    if not destination.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Generated helper directory escaped the workspace")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(output, destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--update-locks", action="store_true")
    parser.add_argument("--verify-format", action="store_true")
    parser.add_argument(
        "--check-sdk",
        action="store_true",
        help="check the required SDK without building",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild even when the published helper matches its inputs",
    )
    args = parser.parse_args()
    try:
        if args.check_sdk:
            resolve_dotnet(ROOT / "native/zzz-extract", args.dotnet)
        else:
            build(args.dotnet, args.update_locks, args.verify_format, force=args.force)
    except MissingDotnetSdk as error:
        parser.exit(1, f"[build] WARNING: {error}\n")
