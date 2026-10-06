"""HTTP client for the Batch Processing API.

Uses ``requests`` with a retry policy of
(5 retries, exponential backoff 100ms -> 2000ms, retry on 5xx and 429).
Authentication mirrors ``JaxRsAuthInjector.createForMagicTokenRoot()``: a
``Authorization: Bearer <token>`` header is added when a token is configured.
"""

from __future__ import annotations

import random
import requests
import time
from typing import Optional

from .dto import BatchProcessTaskDto, TilingGridDescriptorDto

_PROCESS_PATH = "process"
_TILING_GRIDS_PATH = "tilinggrids"

_MAX_RETRIES = 5
_BACKOFF_MIN_MS = 100
_BACKOFF_MAX_MS = 2000
_JITTER = 0.25
_RETRYABLE_STATUS = {429} | set(range(500, 600))


class BatchV2Client:
    def __init__(self, base_uri: str, token: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        # Ensure a single trailing slash so urljoin-style concatenation is clean.
        self._base_uri = base_uri.rstrip("/") + "/"
        self._session = session or requests.Session()
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            self._headers["Authorization"] = "Bearer " + token

    def _get_with_retry(self, url: str) -> requests.Response:
        attempt = 0
        while True:
            try:
                response = self._session.get(url, headers=self._headers, timeout=60)
                if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    self._sleep_backoff(attempt)
                    attempt += 1
                    continue
                return response
            except requests.RequestException:
                if attempt >= _MAX_RETRIES:
                    raise
                self._sleep_backoff(attempt)
                attempt += 1

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        # Exponential backoff capped at max, with +/- jitter.
        delay_ms = min(_BACKOFF_MIN_MS * (2 ** attempt), _BACKOFF_MAX_MS)
        jitter = delay_ms * _JITTER
        delay_ms += random.uniform(-jitter, jitter)
        time.sleep(max(delay_ms, 0) / 1000.0)

    def get_task(self, task_id: str) -> BatchProcessTaskDto:
        """GET /process/{taskId}."""
        url = self._base_uri + _PROCESS_PATH + "/" + str(task_id)
        response = self._get_with_retry(url)
        if response.status_code != 200:
            raise RuntimeError(
                "BatchV2 getTask failed (%d): %s" % (response.status_code, response.text)
            )
        return BatchProcessTaskDto.from_json(response.json())

    def get_grid_descriptor(self, tiling_grid_id) -> TilingGridDescriptorDto:
        """GET /tilinggrids/{id}."""
        url = self._base_uri + _TILING_GRIDS_PATH + "/" + str(tiling_grid_id)
        response = self._get_with_retry(url)
        if response.status_code != 200:
            raise RuntimeError(
                "BatchV2 getGridDescriptor failed (%d): %s"
                % (response.status_code, response.text)
            )
        return TilingGridDescriptorDto.from_json(response.json())

    def close(self) -> None:
        self._session.close()
