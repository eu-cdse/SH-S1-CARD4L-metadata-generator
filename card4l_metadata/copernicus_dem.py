"""Copernicus 10m DEM coverage lookup.

Reads the coverage polygon (WKT) once, builds a prepared shapely polygon, and
classifies an AOI as EEA-10 (fully covered), mixed, or GLO-30 (no overlap).

The coverage polygon is read from a local file —
``resources/copernicus-dem-10m-geometry.wkt``, shipped with the package — so no
S3 access (or credentials) are needed to produce metadata. It is a snapshot of
``s3://sh-copernicus-dem-10m/geometry.txt``; re-export it if the 10m footprint
changes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep
from typing import Optional, Union

log = logging.getLogger(__name__)

# The coverage WKT bundled with the package.
DEFAULT_COVERAGE_FILE = (
    Path(__file__).parent / "resources" / "copernicus-dem-10m-geometry.wkt"
)

EEA10 = "EEA-10"
GLO30 = "GLO-30"
MIXED = "EEA-10 where available, GLO-30 elsewhere"


class Copernicus10DemResolver:
    """Stateful resolver: initialize once, then resolve any number of AOIs."""

    _coverage = None  # prepared polygon
    _coverage_geom: Optional[BaseGeometry] = None

    @classmethod
    def initialize(cls, coverage_file: Union[str, "os.PathLike", None] = None) -> None:
        """Load the coverage WKT.

        ``coverage_file`` defaults to the bundled
        ``resources/copernicus-dem-10m-geometry.wkt``.
        """
        path = Path(coverage_file) if coverage_file is not None else DEFAULT_COVERAGE_FILE
        try:
            wkt_text = path.read_text(encoding="ascii")
            geometry = shapely_wkt.loads(wkt_text)
            if geometry.geom_type not in ("Polygon", "MultiPolygon"):
                raise ValueError(
                    "Expected polygonal geometry, got " + geometry.geom_type
                )
            cls._coverage_geom = geometry
            cls._coverage = prep(geometry)
            log.debug("Loaded %s coverage from %s", EEA10, path)
        except Exception:
            log.error("Error reading %s coverage from %s", EEA10, path, exc_info=True)
            raise

    @classmethod
    def resolve(cls, aoi: BaseGeometry) -> str:
        """Classify ``aoi`` against the coverage."""
        if cls._coverage is None:
            raise RuntimeError("Copernicus10DemResolver not initialized")
        if cls._coverage.contains(aoi):
            return EEA10
        if cls._coverage.intersects(aoi):
            return MIXED
        return GLO30
