"""HTTP client for the Qualys Version API.

Security note: this module never logs the Authorization header, the
password, or full response bodies. Callers should only log the small,
structured summaries this module returns.
"""

from __future__ import annotations

import time

import requests
from requests.auth import HTTPBasicAuth

PORTAL_VERSION_PATH = "/qps/rest/portal/version"

# Transient failures worth retrying.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
# Never retry these -- retrying will not fix bad credentials/permissions.
AUTH_FAILURE_STATUS_CODES = {401, 403}


class QualysAPIError(Exception):
    """Base class for all Qualys API failures."""


class QualysAuthError(QualysAPIError):
    """401/403 -- credentials or permissions are wrong. Not retried."""


class QualysTimeoutError(QualysAPIError):
    """The request(s) timed out after all retries were exhausted."""


class QualysRetryExhaustedError(QualysAPIError):
    """A retryable error (429/5xx) persisted through all attempts."""


class QualysClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        backoff_base_seconds: float = 1.0,
        sleep_fn=time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(username, password)
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep_fn

    def get_portal_version(self) -> dict:
        """Fetch the raw JSON body of GET /qps/rest/portal/version.

        Retries transient failures (429, 5xx, network/timeout errors) with
        exponential backoff. Authentication failures (401/403) fail fast.
        """
        url = f"{self._base_url}{PORTAL_VERSION_PATH}"
        headers = {"Accept": "application/json"}

        last_exception: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    auth=self._auth,
                    timeout=self._timeout,
                )
            except requests.Timeout as exc:
                last_exception = exc
                if attempt == self._max_retries:
                    raise QualysTimeoutError(
                        f"Request timed out after {attempt} attempts"
                    ) from exc
                self._backoff(attempt)
                continue
            except requests.RequestException as exc:
                last_exception = exc
                if attempt == self._max_retries:
                    raise QualysAPIError(
                        f"Network error after {attempt} attempts: {type(exc).__name__}"
                    ) from exc
                self._backoff(attempt)
                continue

            if response.status_code in AUTH_FAILURE_STATUS_CODES:
                raise QualysAuthError(
                    f"Authentication failed with HTTP {response.status_code}"
                )

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_exception = QualysAPIError(f"HTTP {response.status_code}")
                if attempt == self._max_retries:
                    raise QualysRetryExhaustedError(
                        f"HTTP {response.status_code} persisted after {attempt} attempts"
                    )
                self._backoff(attempt, response=response)
                continue

            if not response.ok:
                raise QualysAPIError(f"Unexpected HTTP {response.status_code}")

            try:
                return response.json()
            except ValueError as exc:
                raise QualysAPIError("Response body was not valid JSON") from exc

        # Should be unreachable, but keep mypy/pyright and tests happy.
        raise QualysAPIError(
            f"Retries exhausted: {last_exception}"
        ) if last_exception else QualysAPIError("Retries exhausted")

    def _backoff(self, attempt: int, response: requests.Response | None = None) -> None:
        delay = self._backoff_base * (2 ** (attempt - 1))
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
        self._sleep(delay)
