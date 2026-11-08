"""Clean up section boundaries after staged INI deletions."""

from ..ini.command_phases import parse_command_line
from ..ini.document import BLANK, COMMENT, IF, ELIF, ELSE, ENDIF


_EMPTY_KINDS = {BLANK, COMMENT, IF, ELIF, ELSE, ENDIF}


class DeletionCleanup:
    """Track populated header identities so existing empty placeholders survive."""

    def __init__(self, doc):
        self.original = doc.to_string()
        self.unsafe = {issue["section"].lower() for issue in doc.structure_errors() + doc.syntax_errors()
                       if issue.get("section")}
        self.populated = {doc.lines[section.header_no]: tuple(
            line.raw for line in section.lines if line.kind not in (BLANK, COMMENT))
            for section in doc.sections}

    def finish(self, doc):
        if doc.to_string() == self.original:
            return []
        removed_names = []
        while True:
            empty = [section for section in doc.sections
                     if section.name.lower() not in self.unsafe
                     and self.populated.get(doc.lines[section.header_no])
                     and not any(line.kind not in _EMPTY_KINDS for line in section.lines)
                     and tuple(line.raw for line in section.lines if line.kind not in (BLANK, COMMENT))
                     != self.populated[doc.lines[section.header_no]]]
            if not empty:
                break
            helper_names = {section.name.lower() for section in empty
                            if section.name.lower().startswith(("commandlist", "customshader"))}
            # Keep comments and their line endings. Conditional wrappers must
            # leave with the header rather than leak into its preceding section.
            for section in reversed(empty):
                removed_names.append(section.name)
                for no in reversed([section.header_no] + [line.no for line in section.lines
                                   if line.kind not in (BLANK, COMMENT)]):
                    doc.delete_lines(no, no + 1)
            helper_names -= {section.name.lower() for section in doc.sections}
            for line in reversed(doc.lines):
                command = parse_command_line(line.raw)
                key, separator, value = command.text.partition("=")
                if separator and key.strip().lower() == "run" and value.strip().lower() in helper_names:
                    doc.delete_lines(line.no, line.no + 1)
        # Insert missing separators without collapsing existing blank lines or
        # changing the bytes of any surviving source line.
        for section in reversed(doc.sections[1:]):
            if doc.lines[section.header_no - 1].kind != BLANK:
                doc.insert_lines(section.header_no, [""])
        return removed_names
