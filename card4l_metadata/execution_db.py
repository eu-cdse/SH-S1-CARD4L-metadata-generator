"""Reads the per-task execution database and feature manifest from S3.

Downloads the per-task ``execution-<taskId>.sqlite`` and
``featureManifest-<taskId>.gpkg`` files from S3, reads the DONE feature
executions from the SQLite ``features`` table, and pulls each tile's polygon
geometry from the GeoPackage feature manifest.

The ``features`` table is read with Python's ``sqlite3`` (columns id, status,
name, error, delivered) and the manifest with GDAL/OGR, which reads GeoPackage
natively.
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from osgeo import ogr
from pathlib import Path
from typing import List, Optional

from . import path_template
from .model import GridTile

log = logging.getLogger(__name__)

ogr.UseExceptions()

# The status name stored in the sanitized features table.
_FEATURE_STATUS_DONE = "DONE"


class ExecutionDbReader:
    """Reads processed tiles for a batch task from its execution + manifest files."""

    def __init__(self, s3):
        self._s3 = s3

    def get_processed_tiles(self, batch_task) -> List[GridTile]:
        """The tiles of ``batch_task`` that finished processing."""
        delivery = batch_task.request.delivery
        task_id = batch_task.identifier

        temp_dir = Path(tempfile.mkdtemp(prefix="card4l_exec_"))

        feature_manifest_uri = path_template.create_file_uri(
            delivery.url, task_id, "featureManifest", "gpkg"
        )
        manifest_file = temp_dir / _basename(feature_manifest_uri.key)

        execution_uri = path_template.create_file_uri(
            delivery.url, task_id, "execution", "sqlite"
        )
        execution_file = temp_dir / _basename(execution_uri.key)

        try:
            self._s3.download_file(feature_manifest_uri, manifest_file)
            self._s3.download_file(execution_uri, execution_file)
            return self._read_processed_tiles(execution_file, manifest_file)
        except Exception:
            log.error(
                "Failed to read processed tiles from the execution SQLite file %s "
                "and feature manifest file %s.",
                execution_uri,
                feature_manifest_uri,
                exc_info=True,
            )
            raise
        finally:
            _delete_dir(temp_dir)

    @staticmethod
    def _read_processed_tiles(execution_file: Path, manifest_file: Path) -> List[GridTile]:
        """The processed tiles, given the downloaded execution db and manifest."""
        feature_names = _get_done_feature_names(execution_file)

        data_source = ogr.Open(str(manifest_file))
        if data_source is None:
            raise IOError("Could not open GeoPackage: " + str(manifest_file))
        try:
            layer = data_source.GetLayer(0)  # the first feature table
            processed: List[GridTile] = []
            for name in feature_names:
                geometry = _get_tile_geometry(layer, name)
                processed.append(GridTile(name=name, geometry=geometry))
            return processed
        finally:
            data_source = None


def _get_done_feature_names(execution_file: Path) -> List[str]:
    """Read the ``name`` of every feature with status DONE from the SQLite file.

    Only features that finished processing are returned.
    """
    conn = sqlite3.connect(str(execution_file))
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT name FROM features WHERE status = ?", (_FEATURE_STATUS_DONE,)
        )
        return [row["name"] for row in cursor.fetchall()]
    finally:
        conn.close()


def _get_tile_geometry(layer, identifier: str):
    """The polygon of one feature, by identifier.

    Returns a shapely Polygon, or ``None`` if it is missing or not polygonal
    (logged in that case).
    """
    from shapely import wkt as shapely_wkt

    layer.SetAttributeFilter("identifier = '%s'" % identifier.replace("'", "''"))
    try:
        feature = layer.GetNextFeature()
        if feature is None:
            log.warning("Geometry of the tile %s is not defined.", identifier)
            return None
        geom_ref = feature.GetGeometryRef()
        if geom_ref is None:
            log.warning("Geometry of the tile %s is not defined.", identifier)
            return None
        geometry = shapely_wkt.loads(geom_ref.ExportToWkt())
        if geometry.geom_type == "Polygon":
            return geometry
        log.warning("Tile %s has non-polygon geometry.", identifier)
        return None
    finally:
        layer.SetAttributeFilter(None)


def _basename(key: str) -> str:
    return key[key.rfind("/") + 1:]


def _delete_dir(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
