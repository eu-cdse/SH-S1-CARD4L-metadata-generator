"""Local S3 doubles used by the tile-metadata tests.

Stand-ins for ``Card4lS3`` covering the read API the producer uses
(``read_bytes`` / ``open_file`` / ``list_xml_files_as_urls``). Metadata is
written to a local output directory, so no write hook is needed.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import List, Optional

from card4l_metadata.s3 import MAX_OUTPUT_TIFF_HEADER_LENGTH


class LocalOutputS3:
    """Serves delivery-bucket objects (userdata.json, *.tif) from a grid dir.

    A key maps to a resource by dropping everything up to and including the
    *last* underscore, so ``..._userdata.json`` -> ``userdata.json`` and
    ``..._VV.tif`` -> ``VV.tif``.
    """

    def __init__(self, grid_dir: str | os.PathLike):
        self.grid_dir = Path(grid_dir)

    def _resource_for_key(self, key: str) -> Path:
        return self.grid_dir / key.rsplit("_", 1)[-1]

    def read_bytes(self, bucket: str, key: str, num_bytes: Optional[int] = None,
                   requester_pays: bool = False) -> bytes:
        path = self._resource_for_key(key)
        if not path.is_file():
            raise FileNotFoundError("No fixture %s for key %s" % (path, key))
        data = path.read_bytes()
        return data[:num_bytes] if num_bytes else data

    def open_file(self, bucket: str, key: str, num_bytes: Optional[int] = None,
                  requester_pays: bool = False):
        return io.BytesIO(self.read_bytes(bucket, key, num_bytes, requester_pays))

    def open_tiff_header(self, bucket: str, key: str):
        return self.open_file(bucket, key, num_bytes=MAX_OUTPUT_TIFF_HEADER_LENGTH)


class LocalS1S3:
    """Serves S1 manifest/annotation from ``<grid_dir>/s1/<productId>/``.

    Two annotation layouts are accepted::

        <grid_dir>/s1/<productId>/manifest.safe
        <grid_dir>/s1/<productId>/annotation/<anything>.xml   # mirrors the bucket
        <grid_dir>/s1/<productId>/annotation.xml              # single flat file

    They otherwise come from the requester-pays ``sentinel-s1-l1c`` bucket; this
    double keeps the test offline when the fixtures are present.
    """

    def __init__(self, grid_dir: str | os.PathLike):
        self.s1_dir = Path(grid_dir) / "s1"

    @staticmethod
    def _product_id_from_path(path: str) -> str:
        parts = [p for p in path.rstrip("/").split("/") if p]
        if "annotation" in parts:
            return parts[parts.index("annotation") - 1]
        if parts[-1] == "manifest.safe":
            return parts[-2]
        return parts[-1]

    def _annotation_dir(self, product_id: str) -> Path:
        return self.s1_dir / product_id / "annotation"

    def _flat_annotation(self, product_id: str) -> Path:
        return self.s1_dir / product_id / "annotation.xml"

    def _path_for_key(self, key: str) -> Path:
        product_id = self._product_id_from_path(key)
        parts = key.rstrip("/").split("/")
        if parts[-1] == "manifest.safe":
            return self.s1_dir / product_id / "manifest.safe"
        if "annotation" in parts:
            in_dir = self._annotation_dir(product_id) / parts[-1]
            if in_dir.is_file():
                return in_dir
            return self._flat_annotation(product_id)
        raise FileNotFoundError("No local S1 fixture for key %s" % key)

    def read_bytes(self, bucket: str, key: str, num_bytes: Optional[int] = None,
                   requester_pays: bool = False) -> bytes:
        path = self._path_for_key(key)
        if not path.is_file():
            raise FileNotFoundError("No local S1 fixture at %s" % path)
        data = path.read_bytes()
        return data[:num_bytes] if num_bytes else data

    def open_file(self, bucket: str, key: str, num_bytes: Optional[int] = None,
                  requester_pays: bool = False):
        return io.BytesIO(self.read_bytes(bucket, key, num_bytes, requester_pays))

    def list_xml_files_as_urls(self, s3_url: str) -> List[str]:
        """``s3_url`` ends with ``.../<productId>/annotation/``."""
        product_id = self._product_id_from_path(s3_url)
        base = s3_url.rstrip("/")

        directory = self._annotation_dir(product_id)
        if directory.is_dir():
            return [
                "%s/%s" % (base, p.name)
                for p in sorted(directory.iterdir())
                if p.name.endswith(".xml")
            ]

        # Flat layout: a single <productId>/annotation.xml.
        flat = self._flat_annotation(product_id)
        return ["%s/%s" % (base, flat.name)] if flat.is_file() else []

    def _has_annotation(self, product_id: str) -> bool:
        directory = self._annotation_dir(product_id)
        if directory.is_dir() and any(
                p.name.endswith(".xml") for p in directory.iterdir()
        ):
            return True
        return self._flat_annotation(product_id).is_file()

    def has_fixtures_for(self, product_ids) -> bool:
        return bool(product_ids) and all(
            (self.s1_dir / pid / "manifest.safe").is_file()
            and self._has_annotation(pid)
            for pid in product_ids
        )
