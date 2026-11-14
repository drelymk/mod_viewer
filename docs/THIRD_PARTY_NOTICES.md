# Third-party notices

## NumPy

- Package: `numpy`
- Versions: 2.2.6 on Python 3.10/3.11; 2.5.2 on Python 3.12+
- Source: https://numpy.org/
- License: BSD-3-Clause; the installed distribution's license and bundled
  component notices are included in its packaged distribution metadata.

The geometry packer uses NumPy for bounded static vertex and index conversion.

## Three.js

- Package: `three`
- Version: 0.186.1
- Source: https://github.com/mrdoob/three.js
- License: MIT; see `src/assets/LICENSE`

This application vendors the WebGPU and TSL builds, controls, sky, SSAO,
depth-aware blur, bloom, and GLTFExporter addons without modifying upstream files.
The viewer's deterministic soft PCF shadow filter is adapted from Three.js
r185 under the same MIT license.

## Ace Editor

- Package: `ace-builds`
- Version: 1.44.0 (`184177de1dcc5946b093edba0b0fe1c29c2a127a`)
- Source: https://github.com/ajaxorg/ace-builds
- License: BSD-3-Clause; see `src/web/lib/ace/LICENSE`

This application vendors the minified no-conflict Ace core, INI mode,
Tomorrow Night theme, and search-box extension without modifying those
upstream files.

## Game extraction helper

- Mod Viewer builds a minimal helper directly against CUE4Parse revision
  `cb909733773daea9015a67f6955d6f2ec24d3629` (Apache-2.0), preserving the parser
  revision previously used by the extractor. Source: https://github.com/FabianFG/CUE4Parse
- ACL `414689d5cff4286a7898487a46dc5e48005d38da` and RTM
  `d7982f2b2524feeb322f424b29cf43df30b5d5b7` use MIT licenses.
- Oodle decompression uses the same pinned OodleUE 2026-06-04-1357 binary selected
  by that parser, from https://github.com/WorkingRobot/OodleUE. Oodle is an
  Epic Games/RAD Game Tools component; its upstream terms apply.
- zlib-ng uses the pinned decoder distributed by Zlib-ng.NET 1.0.0,
  https://github.com/NotOfficer/Zlib-ng.NET (MIT wrapper; zlib license for zlib-ng).
- The self-contained .NET runtime and locked NuGet libraries retain their
  upstream licenses. ImageSharp 3.1.12 retains its Six Labors Split License and
  known upstream advisories; the helper accepts no images and exports no textures.
- The bundled `assets/game-extract/licenses` directory contains source licenses,
  NuGet license texts, package metadata and a package inventory. Dependency pins
  and source archive checksums are maintained in `tools/build_game_extract.py`
  and `native/game-extract/locks/`.

The helper runs only for explicit game extraction/index jobs and writes native
mesh/property/animation data into private staging. CUE4Parse-MCP is no longer
distributed or invoked.
