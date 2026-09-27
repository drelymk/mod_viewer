"""Run check-only audits; hygiene fixers operate on disposable file copies."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VENDOR = "src/web/lib/"


def run_audit(output, name, command, *, cwd=ROOT):
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    (output / f"{name}.txt").write_text(
        result.stdout + result.stderr,
        encoding="utf-8",
    )
    print(f"{name}: {'clean' if result.returncode == 0 else 'findings'}")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".quality-reports")
    parser.add_argument("--base", help="Git ref to check for whitespace errors")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\0")
    files = [name for name in tracked if name and not name.startswith(VENDOR)]
    python_files = [name for name in files if name.endswith(".py")]
    web_files = [
        name
        for name in files
        if Path(name).suffix
        in {
            ".js",
            ".mjs",
            ".css",
            ".html",
            ".json",
            ".md",
            ".yml",
            ".yaml",
        }
    ]
    python = sys.executable
    ruff = [python, "-m", "ruff"]
    eslint = ["node", "node_modules/eslint/bin/eslint.js"]
    audits = {
        "ruff-lint": run_audit(
            output,
            "ruff-lint",
            [
                *ruff,
                "check",
                "--config",
                "ruff.toml",
                "--no-cache",
                "--output-format=json",
                *python_files,
            ],
        ),
        "ruff-format": run_audit(
            output,
            "ruff-format",
            [
                *ruff,
                "format",
                "--config",
                "ruff.toml",
                "--no-cache",
                "--check",
                *python_files,
            ],
        ),
        "prettier": run_audit(
            output,
            "prettier",
            [
                "node",
                "node_modules/prettier/bin/prettier.cjs",
                "--check",
                *web_files,
            ],
        ),
        "eslint": run_audit(
            output,
            "eslint",
            [
                *eslint,
                "src/web/js",
                "--format=json",
            ],
        ),
    }
    failures = {
        "audit-tool-errors": int(any(status > 1 for status in audits.values())),
        "python-correctness": run_audit(
            output,
            "python-correctness",
            [
                *ruff,
                "check",
                "--config",
                "ruff.toml",
                "--no-cache",
                "--select=F821,F822,F823,E9",
                *python_files,
            ],
        ),
        "javascript-correctness": run_audit(
            output,
            "javascript-correctness",
            [
                *eslint,
                "src/web/js",
                "--rule=no-unused-vars:off",
                "--rule=no-useless-assignment:off",
            ],
        ),
    }
    text_files = []
    for name in files:
        data = (ROOT / name).read_bytes()
        if b"\0" in data:
            continue
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        text_files.append(name)
    failures["codespell"] = run_audit(
        output,
        "codespell",
        [
            python,
            "-m",
            "codespell_lib",
            "--config=.codespellrc",
            *text_files,
        ],
    )
    failures["check_merge_conflict"] = run_audit(
        output,
        "check_merge_conflict",
        [
            python,
            "-m",
            "pre_commit_hooks.check_merge_conflict",
            "--assume-in-merge",
            *text_files,
        ],
    )
    hooks = {
        "trailing_whitespace_fixer": ([], text_files),
        "end_of_file_fixer": ([], text_files),
        "mixed_line_ending": (["--fix=no"], text_files),
        "check_json": ([], [name for name in files if name.endswith(".json")]),
        "check_yaml": (
            [],
            [name for name in files if name.endswith((".yml", ".yaml"))],
        ),
    }
    with tempfile.TemporaryDirectory(prefix="quality-audit-") as temporary:
        copies = Path(temporary)
        for name in text_files:
            target = copies / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for hook, (options, selected) in hooks.items():
            if selected:
                failures[hook] = run_audit(
                    output,
                    hook,
                    [
                        python,
                        "-m",
                        f"pre_commit_hooks.{hook}",
                        *options,
                        *selected,
                    ],
                    cwd=copies,
                )
    diff = ["git", "diff", "--check"]
    if args.base:
        diff.append(args.base)
    failures["diff-check"] = run_audit(output, "diff-check", diff)
    (output / "summary.json").write_text(
        json.dumps({"audits": audits, "required_checks": failures}, indent=2) + "\n",
        encoding="utf-8",
    )
    return int(any(failures.values()))


if __name__ == "__main__":
    raise SystemExit(main())
