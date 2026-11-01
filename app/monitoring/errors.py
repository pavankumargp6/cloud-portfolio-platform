"""SSRF-related exceptions.

`SSRFError` carries a stable `code` attribute used by tests and by the
API layer to convert rejection into a 422 response.
"""


class SSRFError(Exception):
    """Raised when a URL fails SSRF validation."""

    def __init__(self, code: str, message: str = "", url: str | None = None):
        self.code = code
        self.url = url
        super().__init__(f"{code}: {message}" if message else code)
