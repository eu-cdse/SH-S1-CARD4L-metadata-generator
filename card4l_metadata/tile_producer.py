"""Generates the CARD4L metadata of a single processed tile.

Generates the CARD4L STAC JSON + XML metadata for one processed tile and writes
them to a local output directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from . import geom as geom_ops
from . import json_producer, path_template, xml_producer
from .gdal_geom import TiffGeometry
from .model import (
    S1Annotation,
    S1Manifest,
    TileData,
    TileId,
    Userdata,
    utc_now,
)
from .s1_constants import METERS_TO_DEGREES, get_pixel_spacing
from .s1_parsers import S1AnnotationParser, S1ManifestParser
from .s3 import MAX_OUTPUT_TIFF_HEADER_LENGTH

log = logging.getLogger(__name__)

_IDENTIFIER_USERDATA = "userdata"
_METADATA_FILENAME = "metadata"
# Sanity thresholds for the generated documents.
_MIN_JSON_LENGTH = 1000
_MIN_XML_LENGTH = 4000


class TileMetadataProducer:
    """Generates one tile's metadata and writes it to local files."""

    def __init__(self, card4l_task, batch_task, tile, grid, s3, output_dir: Path,
                 s1data_s3=None):
        self._card4l_task = card4l_task
        self._batch_task = batch_task
        self._tile = tile
        self._grid = grid
        self._s3 = s3
        self._s1data_s3 = s1data_s3 or s3
        self._output_dir = output_dir

    def generate_metadata(self) -> None:
        """Generate and write the tile's JSON and XML metadata.

        Nothing is written until both documents exist and have passed their
        checks, so a tile that fails leaves no partial output behind.
        """
        s1_collection = self._require_orthorectified()

        userdata = self._read_userdata()
        input_tiles = userdata.tiles
        if not input_tiles:
            log.info(
                "No input tiles in userdata for task %s, tile %s",
                self._card4l_task.batch_task_id,
                self._tile.name,
            )
            return

        output_ids = self._get_output_ids()
        tiling_grid_input = self._batch_task.request.input
        delivery = self._batch_task.request.delivery

        path_uri = path_template.get_path_uri(
            delivery, "", self._tile.name, "json"
        )
        tile_id = TileId.from_s3_key(path_uri.key)
        tile_data = TileData(
            batch_grid=self._grid,
            id=tile_id,
            geometry=self._read_tiff_geometry(output_ids),
            resolution=tiling_grid_input.resolution,
            metadata_generation_time=utc_now(),
            delivery=delivery,
        )

        manifests: List[S1Manifest] = []
        annotations: List[S1Annotation] = []
        for input_tile in input_tiles:
            try:
                manifests.append(
                    S1ManifestParser(input_tile.data_path, self._s1data_s3).parse()
                )
                annotations.append(
                    S1AnnotationParser(input_tile.data_path, self._s1data_s3).parse()
                )
            except Exception as ex:
                log.error(
                    "Error parsing manifest or annotations for tile %s",
                    input_tile.data_path,
                    exc_info=True,
                )
                raise RuntimeError("Error parsing manifest or annotations for tile") from ex

        output_geom = self._calc_output_geom(input_tiles, manifests, tile_data.geometry)

        json_metadata = json_producer.produce_json(
            tile_data, output_ids, output_geom, input_tiles, manifests
        )
        xml_metadata = xml_producer.produce_xml(
            tile_data,
            output_ids,
            output_geom,
            input_tiles,
            manifests,
            annotations,
            s1_collection,
            userdata.service_version,
            self._s3,
        )

        self._check_not_truncated(json_metadata, xml_metadata)

        self._write_metadata(_METADATA_FILENAME, "json", json_metadata)
        self._write_metadata(_METADATA_FILENAME, "xml", xml_metadata)

    # --- helpers -------------------------------------------------------------

    def _require_orthorectified(self):
        """The task's S1 collection, once confirmed CARD4L-compliant.

        Checked before any S3 read or document generation: a non-compliant task
        must fail without producing metadata.
        """
        s1_collection = self._batch_task.request.process_request.s1_collection
        if not (s1_collection and s1_collection.processing.orthorectify is True):
            log.error(
                "Task %s is not CARD4L-compliant due to no orthorectification",
                self._card4l_task.batch_task_id,
            )
            raise RuntimeError(
                "Task is not CARD4L-compliant due to no orthorectification"
            )
        return s1_collection

    def _check_not_truncated(self, json_metadata: str, xml_metadata: str) -> None:
        """Guard against silently truncated documents."""
        if (
            len(json_metadata) >= _MIN_JSON_LENGTH
            and len(xml_metadata) >= _MIN_XML_LENGTH
        ):
            return
        msg = (
            "Suspiciously short output for task %s, tile %s: json of %d and xml of %d"
            % (
                self._card4l_task.batch_task_id,
                self._tile.name,
                len(json_metadata),
                len(xml_metadata),
            )
        )
        log.error(msg)
        raise RuntimeError(msg)

    def _read_userdata(self) -> Userdata:
        """Read the tile's userdata document from S3."""
        userdata_uri = path_template.get_path_uri(
            self._batch_task.request.delivery,
            _IDENTIFIER_USERDATA,
            self._tile.name,
            "json",
        )
        log.info("Retrieving tile userdata file: %s", userdata_uri)
        data = self._s3.read_bytes(userdata_uri.bucket, userdata_uri.key)
        return Userdata.from_json(json.loads(data))

    def _read_tiff_geometry(self, output_ids: List[str]) -> TiffGeometry:
        """Read the geometry from the tile's output GeoTIFF header."""
        tiff_output_id = next(
            oid for oid in output_ids if oid != _IDENTIFIER_USERDATA
        )
        tiff_uri = path_template.get_path_uri(
            self._batch_task.request.delivery,
            tiff_output_id,
            self._tile.name,
            "tif",
        )
        data = self._s3.read_bytes(tiff_uri.bucket, tiff_uri.key, num_bytes=MAX_OUTPUT_TIFF_HEADER_LENGTH)
        return TiffGeometry.from_tiff_bytes(data)

    def _get_output_ids(self) -> List[str]:
        """The output ids the batch request asked for."""
        return list(self._batch_task.request.process_request.response_identifiers)

    def _calc_output_geom(self, input_tiles, manifests, output_tiff_geom):
        """Compute the tile's output geometry."""
        buffer = (
            10 * METERS_TO_DEGREES * get_pixel_spacing(manifests[0].product_info)
        )

        buffered = [
            geom_ops.buffer_mitre(geom_ops.to_shape(t.data_geometry), buffer)
            for t in input_tiles
        ]
        input_geom_buffered = geom_ops.cascaded_union(buffered)
        input_geom_unbuffered = geom_ops.buffer_mitre(input_geom_buffered, -buffer)
        tiff_extent = output_tiff_geom.wgs84_extent

        output_geom = input_geom_unbuffered.intersection(tiff_extent)
        if output_geom.is_empty:
            output_geom = input_geom_buffered.intersection(tiff_extent)
        if output_geom.is_empty:
            raise RuntimeError("No intersection between batch tile and input tiles")

        simplified = geom_ops.simplify(output_geom, buffer)
        return output_geom if simplified.is_empty else simplified

    def _write_metadata(self, output_id: str, extension: str, content: str) -> None:
        """Write metadata to ``<output_dir>/<tileName>/<output_id>.<ext>``.

        """
        tile_dir = self._output_dir / self._tile.name
        tile_dir.mkdir(parents=True, exist_ok=True)
        out_path = tile_dir / ("%s.%s" % (output_id, extension))
        out_path.write_text(content, encoding="utf-8")
        log.info("Wrote %s", out_path)
