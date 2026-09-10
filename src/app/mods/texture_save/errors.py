"""Stable errors and result helpers for Save to Texture."""


class TextureSaveError(ValueError):
    """An expected, stable failure from a texture save request."""

    def __init__(self, code, message, status="error", details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = {} if details is None else details


def error_result(code, message, status="error", **details):
    result = {"status": status, "code": code, "error": message}
    result.update(details)
    return result
