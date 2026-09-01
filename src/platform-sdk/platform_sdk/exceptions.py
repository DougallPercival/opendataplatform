"""One exception type for every non-2xx response — catalog-service's error
body shape is consistently `{"detail": "..."}` (FastAPI's default, and every
handler in that service either raises HTTPException or lets one bubble up),
so there's exactly one place that needs to know that shape, not one per
client method."""
from __future__ import annotations


class PlatformAPIError(Exception):
    """Raised for any catalog-service response with status >= 400.

    status_code and detail are pulled straight out of the response so
    calling code can branch on them (e.g. `except PlatformAPIError as e: if
    e.status_code == 404: ...`) without parsing the message string.
    """

    def __init__(self, status_code: int, detail: str, *, method: str, url: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.url = url
        super().__init__(f"{method} {url} -> {status_code}: {detail}")
