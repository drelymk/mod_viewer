# Quality audits

Install the pinned development tools with Python 3.12 and Node.js 24:

```sh
python -m pip install -r requirements-quality.txt
npm ci --ignore-scripts
python tools/quality_audit.py --base origin/main
```

The runner audits tracked, authored files. Vendored JavaScript is excluded.
Reports go to `.quality-reports/`, which is ignored by Git. CI uploads the same
reports as the `quality-audit` artifact. No audit rewrites source files; the
standard whitespace and final-newline fixers operate on temporary copies.

## Checks and remaining findings

| Tool             | Check                                                                      | CI policy                                                                                                         |
| ---------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Ruff             | Python lint and formatting                                                 | Reject syntax errors, undefined names and invalid exports; retain the full lint and formatting reports for review |
| Prettier         | JS, CSS, HTML, JSON, YAML and Markdown formatting                          | Report formatting differences without rewriting files                                                             |
| ESLint           | Recommended browser-module rules                                           | Reject correctness errors; report unused variables and redundant assignments for review                           |
| pre-commit-hooks | Whitespace, final newlines, mixed endings, conflict markers, JSON and YAML | Reject findings                                                                                                   |
| codespell        | Common misspellings                                                        | Reject findings after documented terminology exclusions                                                           |
| Git              | Whitespace errors relative to the supplied base                            | Reject findings                                                                                                   |

Formatting adoption remains an audit: existing compact Python expressions and
JavaScript layouts differ extensively from the formatter output. Review those
conventions before applying a repository-wide formatting change. Tool/config
errors fail CI even when the corresponding formatting audit is informational.

Unused-import reports also need context. `core/ini/parser.py` and several loader
imports preserve compatibility APIs, while `tests/web/conftest.py` registers
imported pytest fixtures. Keep those exports and fixtures. The audit deliberately
retains these findings instead of deleting code based only on a linter warning.

The spelling exclusions are `te` (the toggle-editing module alias), `currentY`
(a coordinate variable), and `abD` (a vector dot product). Non-English locale
catalogs are outside the English spelling check; the English catalog is checked.

The cleanup corrects a verified rig-root regression: changing a component root
called an undefined helper after mutating the forest. Selection now uses the
existing model-joint session and resolved joint ID. The existing browser rig
lifecycle covers changing and restoring that root without changing Weight
selection. Browser and WebGPU tests remain excluded from GitHub Actions.
