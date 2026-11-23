# Builds and test discipline

- Portable builds store native helpers in one content-addressed compressed
  `assets/extractors.zip` (all runtime dependencies and licenses, no debug symbols,
  identical files stored once). Materialize only the requested helper into a
  private process-lifetime temp directory during explicit extraction, with
  cancellation, containment, size and digest checks, then run the pinned runtime
  validation. Never unpack on startup/model-load paths. Source builds keep ordinary
  publish folders.
- Native helper builds check the SDK selected by each `global.json` via PATH or an
  explicit `--dotnet`; never assume a machine-specific SDK cache. Let .NET resolve
  stable roll-forward in the project directory; never reject a newer selected SDK.
  The app build checks this before installing dependencies or downloading assets; a
  missing SDK prints guidance and stops without a traceback.
- `features.ini` is build-time only: bake a temporary module and remove it after
  building. Source enables all features; frozen UI gates never replace backend
  authorization; missing/malformed values default to enabled.
- The default pytest suite is self-contained; real-mod corpus checks are opt-in via
  `MOD_VIEWER_TEST_CORPUS`. Test environment and test design rules are in AGENTS.md.
  Avoid tests that merely mirror constants; add regressions to the nearest existing
  module.
- Browser UI/rendering tests use the real local server, vendored assets and a
  compatible Edge, with generic skips when unavailable. Pure JS contracts use the
  lightweight module fixture. Bypass ambient proxies for loopback; prefer observable
  readiness over sleeps. Mock `window.pywebview.api` instead of importing GUI-bound
  `app.bridge.api` into browser fixtures.
- Benchmark texture concurrency, lazy loading or transport changes with
  `tools/benchmark_texture_pipeline.py`.
- The context files have a size budget enforced by `tools/quality_audit.py`. When a
  file nears it, tighten or split it rather than raising the limit.
