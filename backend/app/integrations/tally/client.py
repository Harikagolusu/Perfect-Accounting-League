"""HTTP transport for TallyPrime's XML-over-HTTP server.

The client only sends server-configured requests. It never accepts a URL from
an API caller, never logs XML payloads or response bodies, and converts every
transport failure into a small Tally-specific exception.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.integrations.tally.exceptions import (
    TallyConfigurationError,
    TallyConnectionError,
    TallyHTTPError,
    TallyResponseError,
    TallyTimeoutError,
)

logger = logging.getLogger(__name__)

MAX_TIMEOUT_SECONDS = 60.0


def _validated_base_url(value: str) -> str:
    candidate = (value or "").strip().rstrip("/")
    if not candidate:
        raise TallyConfigurationError("TallyPrime URL is not configured.")

    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TallyConfigurationError(
            "TallyPrime URL must be an absolute http(s) URL."
        )
    if parsed.username or parsed.password:
        raise TallyConfigurationError(
            "TallyPrime URL must not contain credentials."
        )
    try:
        parsed.port
    except ValueError:
        raise TallyConfigurationError(
            "TallyPrime URL has an invalid port."
        ) from None
    if parsed.query or parsed.fragment:
        raise TallyConfigurationError(
            "TallyPrime URL must not contain a query or fragment."
        )
    if parsed.path not in ("", "/"):
        raise TallyConfigurationError(
            "TallyPrime URL must not contain a path."
        )
    return candidate


def safe_display_url(value: str) -> str:
    """Return a URL safe to show in an API response.

    Credentials, query strings and fragments are removed. The configured URL
    is normally already clean; this protects the status endpoint if a local
    deployment misconfigures it.
    """
    parsed = urlsplit(value or "")
    if not parsed.hostname:
        return value
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _validated_timeout(value: float) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise TallyConfigurationError(
            "TallyPrime timeout must be a number."
        ) from None
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise TallyConfigurationError(
            f"TallyPrime timeout must be between 0 and {MAX_TIMEOUT_SECONDS:g} seconds."
        )
    return timeout


def _decode_response(response: httpx.Response) -> str:
    """Decode Tally's body, including UTF-16 responses without a charset."""
    content = response.content
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if content.startswith(b"\xef\xbb\xbf"):
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8", "utf-16", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TallyResponseError("TallyPrime returned an unreadable response.")


class TallyClient:
    """Small synchronous client for the local TallyPrime XML endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self._base_url = _validated_base_url(
            settings.tally_base_url if base_url is None else base_url
        )
        self._timeout = _validated_timeout(
            settings.tally_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=transport,
            follow_redirects=False,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    def post_xml(self, payload: str) -> str:
        """POST one XML payload and return the decoded response body.

        Raises:
            TallyTimeoutError: The request exceeded the configured timeout.
            TallyConnectionError: The connection could not be established.
            TallyHTTPError: TallyPrime returned a non-2xx HTTP status.
            TallyResponseError: The body was empty or could not be decoded.
        """
        if not payload or not payload.strip():
            raise TallyConfigurationError("Tally XML request is empty.")

        try:
            response = self._http.post(
                "/",
                content=payload.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "Accept": "text/xml",
                },
            )
        except httpx.TimeoutException as exc:
            logger.warning("TallyPrime request timed out: %s", exc)
            raise TallyTimeoutError(
                "TallyPrime did not respond before the timeout."
            ) from None
        except httpx.RequestError as exc:
            logger.warning("TallyPrime connection failed: %s", exc)
            raise TallyConnectionError("Unable to connect to TallyPrime.") from None

        if not 200 <= response.status_code < 300:
            logger.warning("TallyPrime returned HTTP %s", response.status_code)
            raise TallyHTTPError(
                f"TallyPrime returned HTTP {response.status_code}."
            )

        body = _decode_response(response)
        if not body.strip():
            raise TallyResponseError("TallyPrime returned an empty response.")
        return body

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TallyClient:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
