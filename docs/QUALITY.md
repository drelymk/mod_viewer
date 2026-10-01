# Quality audits

Install the development and quality tools with Python 3.12 and Node.js 24:

```sh
python -m pip install -r tools/requirements-dev.txt
npm --prefix tools ci --ignore-scripts
python tools/quality_audit.py
```

Before every commit, the full audit must pass locally on the final contents being
committed. Fix findings or missing tools, and re-run after any further change.
CI continues to enforce the same checks.

The audit checks tracked and untracked authored files, skips deleted files, and
excludes vendored JavaScript. Reports go to `.quality-reports/`, which is ignored
by Git; CI uploads them as the
`quality-audit` artifact. Checks do not rewrite source files. Hygiene fixers run
on temporary copies.

| Tool             | Scope and purpose                                                              | CI policy                 |
| ---------------- | ------------------------------------------------------------------------------ | ------------------------- |
| Ruff             | Lint all authored Python; format-check normalized Python tooling under `tools/` | Reject findings           |
| Prettier         | `src/web/js/**/*.js` and `tools/config/eslint.config.mjs`                          | Reject formatting changes |
| ESLint           | JavaScript correctness under `src/web/js`                                     | Reject errors and warnings |
| codespell        | Common spelling errors in authored text                                       | Reject findings           |
| pre-commit-hooks | Whitespace, final newlines, line endings, conflict markers, JSON and YAML      | Reject findings           |

CSS and `src/web/index.html` retain their compact authored layout and receive
the whole-tree hygiene checks. Ruff formatting is checked only for the Python
tooling under `tools/` that has been normalized; application, core, and test
Python retain their existing compact formatting. Ruff lint still covers every
tracked Python file.

Compatibility imports in `app/mods/loader.py` and `core/ini/parser.py` have
local F401 annotations; `tests/web/conftest.py` uses them for pytest fixture
registration. `src/build.py` keeps its narrow E402 exception because it adjusts
`sys.path` before an import that depends on that change.

The codespell exclusions are `te` (the toggle-editing alias), `currentY` (a
coordinate variable), `abD` (a vector dot product), and `indicies` (the field
spelling used by the external HLSL format fixture). Non-English locale catalogs
are outside the English spelling check.

Browser UI and WebGPU tests remain excluded from GitHub Actions. The normal CI
test job runs the unit, core, app and integration suite.

Python lint, test discovery, and spelling settings live in `pyproject.toml`.
The npm manifest, lockfile, and installed development packages live under
`tools/`. Run `npm --prefix tools run lint` or
`npm --prefix tools run format:check` from the repository root. Both scripts
check application files from the repository root.

Prettier settings live in `tools/package.json`; its ignore list and the ESLint
config live in `tools/config/`. The npm scripts and audit use those paths
explicitly so Prettier also applies these settings to files under `src/`.
