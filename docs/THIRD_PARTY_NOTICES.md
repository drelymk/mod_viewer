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
