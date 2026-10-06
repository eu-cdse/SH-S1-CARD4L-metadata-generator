"""Geometry operations backed by shapely, used to compute the output geometry
and to write the WKT strings that go into the CARD4L XML.

The output geometry is built with a mitre-join buffer (limit 5.0), a cascaded
polygon union, an intersection, and a Douglas-Peucker simplification.

The WKT writer emits the exact layout the CARD4L XML expects: a space between
the two coordinates of a point and a bare comma between points, with no
trailing space.
"""

from __future__ import annotations

from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from typing import Any, List

# shapely/GEOS join_style: round=1, mitre=2, bevel=3.
_JOIN_MITRE = 2
_MITRE_LIMIT = 5.0


def to_shape(geojson_obj: Any) -> BaseGeometry:
    """Convert a GeoJSON mapping to a shapely geometry.

    Accepts a GeoJSON mapping (dict). If a shapely geometry is passed it is
    returned unchanged.
    """
    if isinstance(geojson_obj, BaseGeometry):
        return geojson_obj
    return shape(geojson_obj)


def to_geojson(geom: BaseGeometry) -> dict:
    """Convert a shapely geometry to a GeoJSON dict."""
    return mapping(geom)


def buffer_mitre(geom: BaseGeometry, distance: float) -> BaseGeometry:
    """Buffer a geometry with a mitre join, limit 5.0."""
    return geom.buffer(distance, join_style=_JOIN_MITRE, mitre_limit=_MITRE_LIMIT)


def cascaded_union(geoms: List[BaseGeometry]) -> BaseGeometry:
    """Union a list of polygons."""
    return unary_union(geoms)


def simplify(geom: BaseGeometry, tolerance: float) -> BaseGeometry:
    """Douglas-Peucker simplification."""
    return geom.simplify(tolerance, preserve_topology=False)


def envelope_array(geom: BaseGeometry) -> List[float]:
    """The bounding box as [minX, minY, maxX, maxY]."""
    minx, miny, maxx, maxy = geom.bounds
    return [minx, miny, maxx, maxy]
