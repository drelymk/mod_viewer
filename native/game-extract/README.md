# Direct game extraction helper

Mod Viewer owns this small process. It calls CUE4Parse directly, using the exact
parser revision previously exercised through CUE4Parse-MCP 1.2.0:
`cb909733773daea9015a67f6955d6f2ec24d3629`. Replacing the wrapper does not upgrade
the parser. No upstream MCP or hosting code is built, invoked or downloaded.

The private protocol supports only WuWa package search (`SkeletalMesh` and
`AnimSequence`), child folders, property JSON, highest-detail UEFormat skeletal
meshes and ActorX animations. Mesh exports disable materials, textures and morph
targets. One provider remains mounted until the extraction job finishes; closing
or cancelling the job terminates the process before staging cleanup. Responses
are atomic files in private staging, with bounded JSON requests and responses.
Only staged descendants can receive exports. Public AES data is bundled as
`keys.json` beside the helper and verified by the existing runtime manifest.

Build from the repository root:

```text
python tools/build_game_extract.py --verify-format
```

Build prerequisites are .NET SDK 10.0.100, CMake 3.16+ and a Windows x64 C++
compiler (MSVC or MinGW). The parser already requires .NET 10; the helper bundles
the same 10.0.13 runtime used by the previous extractor. End users need no SDK.
`src/build.py` builds and includes the helper automatically. Generated files live
under the ignored assets and quality-report directories.

Parser, ACL, RTM, public WuWa AES keys and decoder archives have fixed revisions and SHA-256 checksums
in the build tool. The native library contains only the required ACL decoder;
Oodle and zlib use pinned upstream decoder binaries. NuGet restores use committed
lock files in `locks/`, including transitive content hashes. To intentionally
change dependencies, review the pins, run the build tool with `--update-locks`,
and verify geometry, skeletons and animation samples before accepting the change.
Never select a floating/latest parser or trim its reflection-based serializers.
No network access is needed for extraction. A game update may require a new
viewer build with updated keys or parser support; no runtime key fallback exists.

The unchanged conversion library carries ImageSharp 3.1.12 and its upstream
license and [vulnerability advisories](https://www.nuget.org/packages/SixLabors.ImageSharp/3.1.12).
At the pinned parser revision, ImageSharp operations belong to landscape
heightmap conversion (`LandscapeMeshDto` and `LandscapeMeshExporter`), which
the helper never instantiates. Mesh export explicitly uses `SkinnedAssetExporter`
and UEFormat; `MeshExporter.EnqueueMaterials` returns immediately when
`ExportMaterials` is false. Animation export uses ActorX. Search and property
JSON deserialize packages without invoking image decoding, encoding or processing.
The affected TIFF, ICC and histogram operations are therefore unreachable through
these extraction operations. Retain the locked dependency and visible restore
warnings; reassess this conclusion before enabling landscape or image exports.
Package metadata and license texts are shipped in `licenses/`.
