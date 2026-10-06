"""Runtime configuration for the standalone CARD4L metadata producer.

A plain object holding the endpoints and credentials the producer needs.

Values come from constructor arguments, falling back to environment variables:

``BATCH_BASE_URI``   base URI of the BatchV2 service
``SH_TOKEN``            Sentinel Hub bearer token for the BatchV2 API
``AWS_REGION`` / ``AWS_DEFAULT_REGION``   AWS region for the default S3 client
``S3_ENDPOINT``         optional S3 endpoint override
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

DEFAULT_BATCH_BASE_URI = "https://services.sentinel-hub.com/batch/v2"


@dataclass
class Config:
    """Holds the settings required to run the metadata producer."""

    batch_base_uri: str = DEFAULT_BATCH_BASE_URI
    sh_token: Optional[str] = None
    aws_region: Optional[str] = None
    s3_endpoint: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        *,
        batch_base_uri: Optional[str] = None,
        sh_token: Optional[str] = None,
        aws_region: Optional[str] = None,
        s3_endpoint: Optional[str] = None,
    ) -> "Config":
        """Build a config from explicit args, falling back to environment variables."""
        return cls(
            batch_base_uri=(
                batch_base_uri
                or os.environ.get("BATCH_BASE_URI")
                or DEFAULT_BATCH_BASE_URI
            ),
            sh_token=sh_token or os.environ.get("SH_TOKEN"),
            aws_region=(
                aws_region
                or os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
            ),
            s3_endpoint=s3_endpoint or os.environ.get("S3_ENDPOINT"),
        )
