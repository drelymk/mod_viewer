"""Conservative, per-INI resolution of authored index ranges."""

import re


_DECLARATION = re.compile(r"global\s+\$(\w+)\s*=\s*(-?\d+)\s*$", re.I)
_WRITE = re.compile(
    r"^(?:(?:pre|post)\s+)?(?:(?:global(?:\s+persist)?|local)\s+)?"
    r"\$([\w\\]+)\s*(?:[+*/%-]?=(?!=)|$)", re.I)


def immutable_draw_constants(sections):
    """Accept one unconditional integer declaration with no other writes.

    Persisted values, expressions, duplicate declarations and runtime writes
    cannot be treated as constants. Qualified writes conservatively invalidate
    the matching local name as well; qualified reads are never resolved here.
    """
    candidates, writes = {}, {}
    for section, lines in sections.items():
        depth = 0
        for raw in lines:
            line = raw.split(";", 1)[0].strip()
            low = line.casefold()
            if low.startswith("if "):
                depth += 1
            elif low == "endif":
                depth = max(0, depth - 1)
            write = _WRITE.match(line)
            if write:
                key = write[1].rsplit("\\", 1)[-1].casefold()
                writes[key] = writes.get(key, 0) + 1
            declaration = _DECLARATION.fullmatch(line)
            if section.casefold() == "constants" and depth == 0 and declaration:
                candidates[declaration[1].casefold()] = int(declaration[2])
    return {name: value for name, value in candidates.items()
            if writes.get(name) == 1}


def resolve_drawindexed(arguments, constants):
    """Return a literal/resolved integer triple, or None for unsupported input."""
    parts = arguments.split(",")
    if len(parts) != 3:
        return None
    values = []
    for part in parts:
        token = part.strip()
        if re.fullmatch(r"-?\d+", token):
            value = int(token)
        elif re.fullmatch(r"\$\w+", token):
            value = constants.get(token[1:].casefold())
        else:
            return None
        if value is None:
            return None
        values.append(value)
    if values[0] < 0 or values[1] < 0:
        return None
    return tuple(values)
