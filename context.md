# 3DMigoto Mod Viewer - Project Context

A pywebview desktop viewer for ZZZ/ZZMI, Genshin/GIMI, WuWa/WWMI and HSR/SRMI
mods: Three.js/WebGPU rendering, archive previews, animation reconstruction and
staged INI/mesh editing.

The durable contracts are split by area under `context/`. Read this index, then
only the area files your change or review touches. When a change spans areas,
read each one it touches.

Keep these files limited to durable contracts and non-obvious failure modes. Use
source, tests and README for implementation details and usage. Keep credentials,
private environment details, machine-specific paths and local test results out of
documentation, comments and tests; use portable fixtures instead.

## Always applies

- Trust: only native-picker paths and descendants of registered roots authorize
  loading, metadata writes or export; browser paths never do. Keep picker/root
  authorization, `core.resource_paths.safe_resource_path()` and the server's
  static-root join separate. The privileged UI stays on localhost with no
  third-party runtime scripts.
- Editing: `core.ini.parser` is read-only; every edit goes through `IniDocument`
  and the shared edit session with begin/commit/rollback, and files are written
  only at Export.
- Identity: resources are mod-relative, never basename-only; textures are
  `role::mod-relative-path`; toggles are their `[Key...]` section plus the complete
  variable tuple; resource discovery stays per INI.
- Evidence: prove, never guess. Names, slot numbers, numeric ID ranges and file
  ordering only discover candidates; ambiguity stays unresolved and visible.
- Shared state: app config mutations hold the shared config transaction from read
  to replacement; archives are read-only.

## Area files

| File | Read when touching |
| --- | --- |
| [trust-and-state.md](context/trust-and-state.md) | bridge authorization, server, config, Mod Folders, archives, load pipeline order |
| [editing.md](context/editing.md) | INI edit session, Export, GLB export, toggles, menus delete, Record, mesh edits, Import Mod |
| [parsing.md](context/parsing.md) | INI parsing, DNF, draw phases, menus/control flow, texture bindings and slots |
| [geometry-textures.md](context/geometry-textures.md) | geometry decode, draws, shapes, texture keys/roles, candidate pools, DDS publication |
| [mod-animation.md](context/mod-animation.md) | INI-driven baked/compute/sparse animation and its playback controls |
| [assets.md](context/assets.md) | Asset index/preview, Asset texture saves, Asset Fill, legacy face alignment |
| [asset-capture.md](context/asset-capture.md) | Add/Update Asset from frame dumps, capture texture roles, GIMI face placement, Manager captures |
| [texture-save.md](context/texture-save.md) | color adjustment preview and Save to Texture |
| [materials.md](context/materials.md) | renderer, TSL materials, game material channels, AO/shadows |
| [weight-rig.md](context/weight-rig.md) | Weight data, Rig.json, mod palette proofs, model rig, posing, Physics, humanoid controls |
| [animation-playback.md](context/animation-playback.md) | game-clip catalogs, playback, sequences, root motion, secondary motion, shared libraries, props |
| [game-extract.md](context/game-extract.md) | any game-folder extraction (shared runtime, trust, install rules) |
| [game-extract-wuwa.md](context/game-extract-wuwa.md) | WuWa reader, index, shared-library build, montages, blueprint/effect props |
| [game-extract-zzz.md](context/game-extract-zzz.md) | ZZZ reader, index, Avatar proof, facial curves, props and visibility windows |
| [game-extract-gimi.md](context/game-extract-gimi.md) | Genshin reader, index, attachments, Mecanim motion, controller sequences, props, skill objects |
| [frontend.md](context/frontend.md) | panels, selection, mesh-edit UI, UV inspection, environment, layout, i18n |
| [builds-tests.md](context/builds-tests.md) | portable builds, native helper SDKs, features.ini, test infrastructure |

`tools/quality_audit.py` enforces a size budget for this index and each area file
and checks that every area file is listed here.
