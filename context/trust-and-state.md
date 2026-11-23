# Trust boundaries and shared state

- Keep core analysis GUI-free. The bridge authorizes native-picker paths and
  canonical descendants of registered Mod Folder roots before loading, metadata
  writes or export; browser-invented paths are never authorization. Promoted
  exact session paths survive registry edits/removal; adding or changing a root
  needs the native picker. Keep the native window private at `_window`.
- Keep three separate boundaries and never merge or duplicate their rules:
  picker/root authorization, `core.resource_paths.safe_resource_path()` for mod
  resources (rejects absolute/drive paths and excessive traversal), and the
  server's static-root join.
- Serve the privileged UI from ephemeral `127.0.0.1`: no `NavigateToString`,
  runtime CDN or third-party scripts. Publish geometry through one shared
  localhost blob, with no base64 round trip or internal builder keys in payloads.
  MCP inspection uses the viewer's lazy texture publications with absolute
  localhost URLs and its own lifetime.
- One canonical analysis per INI feeds controls, draws, shapes and resources.
  Never share discovery state across sibling INIs or repeat semantic stages on
  the normal load path.
- Mod loading packs geometry before reading the Asset registry or resolving
  Asset/material textures, reuses those bytes for material-dependent grouping and
  returns one ordinary editable payload. If Asset materials change baked frame
  families, discard the provisional ranges before rebuilding; keep earlier blob data.
- Diagnostics are lazy, read-only, detached reports cached by edit-session
  revision; commits invalidate them and their failures never fail a mod load.
- App config is versioned and atomically replaced. Every mutation, including
  Asset registry/index commit and rollback, holds the shared Python config
  transaction from first read to replacement: bridge calls run on separate
  threads, so frontend save ordering cannot prevent lost updates. Missing config
  uses defaults; malformed or unsupported files stay untouched.
- Language, panel opacity, environment and tool preferences live under
  `viewerPreferences` (ignore and preserve old top-level values). They are
  omitted until changed, then persist explicitly even when reset to defaults.
  Save them globally, including gizmo visibility, never camera/model orientation
  or capability suppression. Panel opacity fades surfaces and fills only, never
  text, content previews, active/selected states, editor selections or viewport
  annotations.
- Mod Folder browsing lists immediate children deterministically and skips
  symlink escapes. Navigation never loads/validates mods, discovers INIs or shows
  root Edit/Delete on ordinary children.
- ZIP, 7z and RAR mods are virtual, read-only `ModSource`s: normalize member
  paths, reject traversal and case-ambiguous members, keep resource lookup inside
  the archive. Preview and diagnostics may read them; Export, metadata mutation,
  mesh editing and texture saving stay unavailable.
