# AGENTS.md

The durable project invariants live in `context.md`. Read that file before substantial implementation or review work, and treat those invariants as authoritative unless the change intentionally updates them.

Keep deterministic formatting, lint, and other mechanical checks in CI. Code review should focus on consequential regressions and repository-specific behavior.

## Code Review Rules

### Preserve lossless, staged INI editing

- Flag changes that write through the lossy read parser, bypass the shared edit session, reread disk over active staged state, write before Export, or make multi-document edits non-atomic. Safe path: keep `ini_parser` read-only, keep `IniDocument`/`edit_session` authoritative, use begin/commit/rollback for edits, and let Export write dirty documents once while failed documents remain pending.

### Preserve trust boundaries and hot-path performance

- Flag changes that let browser-invented filesystem paths reach loaders, metadata, or export; move the privileged UI away from localhost or introduce runtime third-party scripts; duplicate resource-path sandbox rules; base64-round-trip geometry on the normal load path; eagerly render model textures during backend loading; or raise production texture-render concurrency above 2 without controlled benchmark evidence. Safe path: use native-picker-authorized roots, the existing localhost/blob transport, shared `core.resource_paths.safe_resource_path` rules, the server's separate static-root join, and lazy role-aware texture loading.

### Preserve execution-order and identity semantics

- Flag changes that identify resources by basename instead of mod-relative path, collapse role-specific texture identity, share resource discovery across sibling INIs, treat a toggle as one variable instead of its `[Key...]` section and full variable tuple, or lose per-draw source/execution-order semantics. Safe path: keep parsing/resource state per INI, path-relative identities, `role::mod-relative-path` texture keys, ordered texture assignments, complete-tuple toggle cycling, and all merged draw sources.
