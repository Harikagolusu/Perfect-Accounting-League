"""Safe exceptions raised by the TallyPrime integration.

Exception messages are deliberately generic because API routers may return
them to callers. Raw Tally XML, response bodies and connection internals are
kept in logs, never in the message.
"""

from __future__ import annotations


class TallyError(Exception):
    """Base class for every TallyPrime integration failure.

    ``code`` is a short, stable reason callers can branch on. ``detail`` is
    optional diagnostic text for logs only; it is never serialised into an
    API response.
    """

    code = "tally_error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class TallyConfigurationError(TallyError):
    """The server-side TallyPrime configuration is invalid."""

    code = "configuration_error"


class TallyConnectionError(TallyError):
    """TallyPrime could not be reached."""

    code = "connection_error"


class TallyTimeoutError(TallyError):
    """TallyPrime did not answer within the configured timeout."""

    code = "timeout"


class TallyHTTPError(TallyError):
    """TallyPrime answered with a non-success HTTP status."""

    code = "http_error"


class TallyResponseError(TallyError):
    """TallyPrime answered with malformed or unexpected XML."""

    code = "invalid_response"
