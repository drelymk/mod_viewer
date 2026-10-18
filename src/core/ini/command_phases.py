"""Shared pre/post command selection without interpreting command payloads.

3DMigoto splits each section into two command lists. Ordinary commands default
to pre; an unqualified CommandList call participates in both phases. Explicit
phase calls and CustomShader calls execute both target phases together.
"""

from dataclasses import dataclass
import re


_PHASE_RE = re.compile(r"^(pre|post)\s+(.+)$", re.I)
_RUN_RE = re.compile(r"run\s*=\s*(\S+)", re.I)


@dataclass(frozen=True, slots=True)
class CommandLine:
    raw: str
    text: str
    phase: str | None

    def runs_in(self, phase):
        if self.phase is not None:
            return self.phase == phase
        low = self.text.casefold()
        if low.startswith(("if ", "elif ", "else if ")) or low in {"else", "endif"}:
            return True
        run = _RUN_RE.match(self.text)
        if run and run[1].casefold().startswith(("commandlist", "builtincommandlist")):
            return True
        return phase == "pre"


def parse_command_line(raw):
    """Strip comments and a phase prefix while retaining the authored source."""
    text = raw.split(";", 1)[0].strip()
    match = _PHASE_RE.match(text)
    if match:
        return CommandLine(raw, match[2].strip(), match[1].casefold())
    return CommandLine(raw, text, None)


def phase_commands(commands, phase):
    """Select one phase in authored order, including its control structure."""
    return (command for command in commands if command.runs_in(phase))


def run_target_phases(command, target, phase):
    """Return target phases for a call made during the given caller phase."""
    if command.phase is not None or target.casefold().startswith(
            ("customshader", "builtincustomshader")):
        return ("pre", "post")
    return (phase,)
