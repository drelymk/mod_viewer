# Quality audits

Install the pinned development tools with Python 3.12 and Node.js 24:

```sh
python -m pip install -r requirements-quality.txt
npm ci --ignore-scripts
python tools/quality_audit.py
```

The audit checks tracked authored files and excludes vendored JavaScript. Reports
go to `.quality-reports/`, which is ignored by Git; CI uploads them as the
`quality-audit` artifact. Checks do not rewrite source files. Hygiene fixers run
on temporary copies.

| Tool             | Scope and purpose                                                              | CI policy                 |
| ---------------- | ------------------------------------------------------------------------------ | ------------------------- |
| Ruff             | Lint all tracked Python; format-check Python tools                            | Reject findings           |
| Prettier         | `src/web/js/**/*.js` and `eslint.config.mjs`                                   | Reject formatting changes |
| ESLint           | JavaScript correctness under `src/web/js`                                     | Reject errors and warnings |
| codespell        | Common spelling errors in authored text                                       | Reject findings           |
| pre-commit-hooks | Whitespace, final newlines, line endings, conflict markers, JSON and YAML      | Reject findings           |

CSS and `src/web/index.html` retain their compact authored layout and receive
the whole-tree hygiene checks. The Ruff formatter check stays on Python tooling;
full-tree Python formatting would create a large rewrite of existing compact
code. Ruff lint still covers every tracked Python file.

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
