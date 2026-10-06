"""Geometry metadata read from a GeoTIFF with the GDAL Python bindings.

Uses the GDAL Python bindings (``osgeo.gdal`` + ``osgeo.ogr`` + ``osgeo.osr``)
directly, reprojection included.

Produces, from a GeoTIFF file:
  * width / height
  * geo transform (GDAL order: [originX, pixelW, rowRot, originY, colRot, pixelH])
  * native CRS (EPSG code + WKT)
  * native envelope (min/max X/Y in native CRS)
  * WGS84 extent polygon (native envelope reprojected to CRS84 lon/lat, edges
    densified to within REPROJECT_TOLERANCE_DEG) and its bbox envelope
"""

from __future__ import annotations

from dataclasses import dataclass, field
from osgeo import gdal, ogr, osr
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon
from typing import List, Optional, Tuple

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

# Largest deviation tolerated between the emitted outline and the true curve.
_REPROJECT_TOLERANCE_DEG = 0.0001

# Segments each envelope edge is cut into before the transform. ``Segmentize``
# densifies at a fixed step and cannot know where the projection actually
# curves, so this has to be fine enough for the sharpest curvature we might
# meet; the vertices that turn out to carry no shape are dropped again
# afterwards. 64 is comfortably past the point where polar stereographic and
# UTM envelopes stop changing.
_SEGMENTS_PER_EDGE = 64


@dataclass
class Envelope:
    """An axis-aligned bounding box.

    The constructor sorts coordinates so ``min_x <= max_x`` and ``min_y <= max_y``.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @staticmethod
    def of(x1: float, y1: float, x2: float, y2: float) -> "Envelope":
        return Envelope(
            min_x=min(x1, x2),
            min_y=min(y1, y2),
            max_x=max(x1, x2),
            max_y=max(y1, y2),
        )

    def top_left(self) -> Tuple[float, float]:
        return (self.min_x, self.max_y)

    def bottom_right(self) -> Tuple[float, float]:
        return (self.max_x, self.min_y)


@dataclass
class TiffGeometry:
    """Geometry metadata read from a GeoTIFF."""

    width: int = 0
    height: int = 0
    epsg: Optional[int] = None
    native_wkt: Optional[str] = None
    native_pretty_wkt: Optional[str] = None
    is_geographic: bool = False
    native_envelope: Optional[Envelope] = None
    wgs84_extent: Optional[Polygon] = None
    wgs84_envelope: Optional[Envelope] = None
    geo_transform: List[float] = field(default_factory=list)

    @staticmethod
    def from_tiff_file(tiff_path: str) -> "TiffGeometry":
        ds = gdal.Open(tiff_path)
        if ds is None:
            raise IOError("Could not open TIFF: " + tiff_path)
        try:
            width = ds.RasterXSize
            height = ds.RasterYSize
            gt = list(ds.GetGeoTransform())  # GDAL order

            srs = osr.SpatialReference()
            srs.ImportFromWkt(ds.GetProjection())
            srs.AutoIdentifyEPSG()
            epsg = _epsg_of(srs)
            native_wkt = srs.ExportToWkt()
            # The CARD4L XML carries multiline WKT2:2019, not the WKT1 that
            # ExportToPrettyWkt() would produce.
            native_pretty_wkt = srs.ExportToWkt(
                ["FORMAT=WKT2_2019", "MULTILINE=YES"]
            )
            is_geographic = bool(srs.IsGeographic())

            # Corner coordinates from the geo transform (north-up assumption,
            # matching GDAL's cornerCoordinates upperLeft/lowerRight).
            ul_x, ul_y = _apply_geotransform(gt, 0, 0)
            lr_x, lr_y = _apply_geotransform(gt, width, height)
            native_env = Envelope.of(ul_x, ul_y, lr_x, lr_y)

            wgs84_extent = _reproject_envelope_to_wgs84(native_env, srs)
            minx, miny, maxx, maxy = wgs84_extent.bounds
            wgs84_env = Envelope.of(minx, miny, maxx, maxy)

            return TiffGeometry(
                width=width,
                height=height,
                epsg=epsg,
                native_wkt=native_wkt,
                native_pretty_wkt=native_pretty_wkt,
                is_geographic=is_geographic,
                native_envelope=native_env,
                wgs84_extent=wgs84_extent,
                wgs84_envelope=wgs84_env,
                geo_transform=gt,
            )
        finally:
            ds = None

    @staticmethod
    def from_tiff_bytes(data: bytes) -> "TiffGeometry":
        """Read from an in-memory TIFF (header) via a GDAL /vsimem file."""
        import uuid

        vsipath = "/vsimem/tiffgeom_%s.tif" % uuid.uuid4().hex
        gdal.FileFromMemBuffer(vsipath, data)
        try:
            return TiffGeometry.from_tiff_file(vsipath)
        finally:
            gdal.Unlink(vsipath)


def _epsg_of(srs: "osr.SpatialReference") -> Optional[int]:
    code = srs.GetAuthorityCode(None)
    if code is not None:
        try:
            return int(code)
        except ValueError:
            return None
    return None


def _apply_geotransform(gt: List[float], px: float, py: float) -> Tuple[float, float]:
    x = gt[0] + px * gt[1] + py * gt[2]
    y = gt[3] + px * gt[4] + py * gt[5]
    return (x, y)


def _reproject_envelope_to_wgs84(env: Envelope, srs: "osr.SpatialReference") -> Polygon:
    """Reproject the native envelope to CRS84 (lon/lat) as a polygon.

    The rectangle is densified in the native CRS, transformed, and then thinned
    back down: a vertex sitting within ``_REPROJECT_TOLERANCE_DEG`` of the
    outline it lies on describes no shape, so an envelope that survives the
    transform as a rectangle (a geographic source CRS) comes back as its four
    corners, while a genuinely curved edge keeps the vertices it needs.
    """
    polygon = _envelope_as_ogr_polygon(env)

    longest_edge = max(env.max_x - env.min_x, env.max_y - env.min_y)
    if longest_edge > 0:
        polygon.Segmentize(longest_edge / _SEGMENTS_PER_EDGE)
    polygon.Transform(_transform_to_wgs84(srs))

    densified = shapely_wkt.loads(polygon.ExportToWkt())
    thinned = densified.simplify(_REPROJECT_TOLERANCE_DEG, preserve_topology=False)
    return densified if thinned.is_empty else thinned


def _envelope_as_ogr_polygon(env: Envelope) -> "ogr.Geometry":
    """The envelope as a closed OGR polygon, corners counter-clockwise."""
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in (
        (env.min_x, env.min_y),
        (env.max_x, env.min_y),
        (env.max_x, env.max_y),
        (env.min_x, env.max_y),
        (env.min_x, env.min_y),
    ):
        ring.AddPoint_2D(x, y)
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return polygon


def _transform_to_wgs84(srs: "osr.SpatialReference") -> "osr.CoordinateTransformation":
    """A transform from ``srs`` to CRS84, in lon/lat order.

    GDAL 3 follows the authority axis order, which for EPSG:4326 is lat/lon.
    Both sides are pinned to traditional GIS order (x=lon, y=lat) so the result
    is lon/lat whatever the source declares.
    """
    source = srs.Clone()
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.SetFromUserInput("OGC:CRS84")
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(source, target)
