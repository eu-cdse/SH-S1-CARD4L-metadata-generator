"""S3 access via boto3.

Only the read-side methods used by the metadata flow are implemented; metadata
is written to local files instead of back to S3 (see ``tile_producer``).
"""

from __future__ import annotations

import boto3
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_S3_PATH_PATTERN = re.compile(r"^s3://(?P<bucket>[\w\-_]*)/(?P<key>.*)$")

MAX_OUTPUT_TIFF_HEADER_LENGTH = 30000


def create_url(bucket: str, key: str) -> str:
    """Build an ``s3://bucket/key`` URL."""
    return "s3://%s/%s" % (bucket, key)


def _get_url_part(s3_url: str, group: str) -> str:
    m = _S3_PATH_PATTERN.match(s3_url)
    if not m:
        raise ValueError("Invalid S3 URL: " + s3_url)
    return m.group(group)


class Card4lS3:
    """Read access to the S3 objects the metadata flow needs."""

    def __init__(self, s3_client=None, region: Optional[str] = None,
                 endpoint: Optional[str] = None,
                 aws_profile: Optional[str] = None):
        if s3_client is not None:
            self._s3 = s3_client
        else:
            kwargs = {}
            if region:
                kwargs["region_name"] = region
            if endpoint:
                kwargs["endpoint_url"] = endpoint
            if aws_profile:
                session = boto3.Session(profile_name=aws_profile)
                self._s3 = session.client('s3', **kwargs)
            else:
                self._s3 = boto3.client("s3", **kwargs)

    # --- open helpers --------------------------------------------------------

    def open_file_url(self, s3_url: str, requester_pays: bool = False):
        """Open an object by ``s3://`` URL; returns a stream."""
        return self.open_file(
            _get_url_part(s3_url, "bucket"),
            _get_url_part(s3_url, "key"),
            requester_pays=requester_pays,
        )

    def open_tiff_header(self, bucket: str, key: str):
        """Open the first num_bytes bytes of an object (enough for a TIFF header)."""
        return self.open_file(bucket, key, num_bytes=MAX_OUTPUT_TIFF_HEADER_LENGTH)

    def open_file(
        self,
        bucket: str,
        key: str,
        num_bytes: Optional[int] = None,
        requester_pays: bool = False,
    ):
        """Open an object, or a leading byte range of it, as a stream.

        The returned object is the botocore ``StreamingBody`` (``.read()``-able,
        context-manageable) from ``get_object``.
        """
        log.info("Retrieving file s3://%s/%s", bucket, key)
        kwargs = {"Bucket": bucket, "Key": key}
        if num_bytes is not None:
            kwargs["Range"] = "bytes=0-%d" % (num_bytes - 1)
        if requester_pays:
            kwargs["RequestPayer"] = "requester"
        try:
            return self._s3.get_object(**kwargs)["Body"]
        except Exception as e:
            raise RuntimeError("Failed to get file with key %s from the bucket %s" % (key, bucket)) from e

    def read_bytes(
        self,
        bucket: str,
        key: str,
        num_bytes: Optional[int] = None,
        requester_pays: bool = False,
    ) -> bytes:
        """Convenience: read an object (or byte range) fully into memory."""
        with self.open_file(bucket, key, num_bytes, requester_pays) as body:
            return body.read()

    # --- listing / download --------------------------------------------------

    def list_xml_files_as_urls(self, s3_url: str) -> list:
        """List the .xml objects under a prefix, as ``s3://`` URLs."""
        bucket = _get_url_part(s3_url, "bucket")
        prefix = _get_url_part(s3_url, "key")
        resp = self._s3.list_objects_v2(
            Bucket=bucket,
            RequestPayer="requester",
            Prefix=prefix,
            Delimiter="/",
            MaxKeys=100,
        )
        urls = [create_url(bucket, obj["Key"]) for obj in resp.get("Contents", [])]
        return [u for u in urls if u.endswith(".xml")]

    def download_file(self, uri, destination_path) -> None:
        """Download an object to a local path."""
        dest = str(destination_path)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._s3.download_file(uri.bucket, uri.key, dest)
        except Exception as e:
            raise RuntimeError("Failed to download file with key %s from the bucket %s" % (uri.key, uri.bucket)) from e
