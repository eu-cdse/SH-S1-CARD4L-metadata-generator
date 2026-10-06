"""Builds the CARD4L STAC JSON document (+ its assets,
``ProjTransformSerializer``) — produces the STAC item JSON for a tile.

The output dict is assembled in the key order the STAC document uses
(``StacItemDto`` / ``StacItemPropertiesDto`` / ``AssetDto``), non-null values
only, pretty-printed.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import geom as geom_ops
from . import s3 as s3mod
from .model import InputTile, S1Manifest, TileData, TileId

log = logging.getLogger(__name__)

# Note the space between the media type parameters.
_MIME_TYPE_COG = "image/tiff; application=geotiff; profile=cloud-optimized"
_MIME_JSON = "application/json"
_MIME_XML = "application/xml"

_METADATA_FILENAME = "metadata"
_IDENTIFIER_USERDATA = "userdata"

_POLARIZATION_IDS = ("VV", "VH", "HH", "HV")


def produce_json(
    tile_data: TileData,
    output_ids: List[str],
    output_geom,
    input_tiles: List[InputTile],
    manifests: List[S1Manifest],
) -> str:
    """Build the STAC item document."""
    out_geom_json = geom_ops.to_geojson(output_geom)

    item: "OrderedDict[str, Any]" = OrderedDict()
    item["stac_version"] = "1.0.0-beta.2"
    item["stac_extensions"] = ["sat", "sar", "proj"]
    item["id"] = _to_item_id(tile_data.id)
    item["type"] = "Feature"
    item["geometry"] = out_geom_json
    item["bbox"] = geom_ops.envelope_array(output_geom)
    item["properties"] = _fill_properties(tile_data, output_ids, manifests)
    item["assets"] = _fill_assets(tile_data, output_ids)
    item["links"] = _fill_links(tile_data, input_tiles)

    return json.dumps(
        _strip_nulls(item), indent=2, default=_json_default, ensure_ascii=False
    )


def _to_item_id(tile_id: TileId) -> str:
    """The STAC item id of a tile."""
    return "%s_%04d_%02d_%02d_%s" % (
        tile_id.tile_name,
        tile_id.datatake_year,
        tile_id.datatake_month,
        tile_id.datatake_day,
        tile_id.datatake_id,
    )


def _fill_properties(
    tile_data: TileData, output_ids: List[str], manifests: List[S1Manifest]
) -> "OrderedDict[str, Any]":
    """Fill in the item ``properties``."""
    start_times = [m.start_time for m in manifests if m.start_time is not None]
    stop_times = [m.stop_time for m in manifests if m.stop_time is not None]
    min_start = min(start_times) if start_times else None
    max_stop = max(stop_times) if stop_times else None

    product_info = manifests[0].product_info

    props: "OrderedDict[str, Any]" = OrderedDict()
    # Declared DTO fields first (StacItemPropertiesDto order).
    props["datetime"] = min_start
    props["start_datetime"] = min_start
    props["end_datetime"] = max_stop
    props["platform"] = _get_platform(product_info)
    props["instruments"] = ["c-sar"]
    props["constellation"] = "sentinel-1"
    # Remaining values, in insertion order.
    props["proj:epsg"] = tile_data.geometry.epsg
    props["odc:region_code"] = tile_data.id.tile_name
    props["odc:product"] = "s1_rtc"
    props["odc:processing_datetime"] = tile_data.metadata_generation_time
    props["sar:instrument_mode"] = manifests[0].observation_mode
    props["sar:frequency_band"] = "C"
    props["sar:center_frequency"] = 5.405
    props["sar:polarizations"] = _get_polarizations(output_ids)
    props["sar:product_type"] = "RTC"
    props["sar:observation_direction"] = "right"
    props["sat:orbit_state"] = (manifests[0].pass_direction or "").lower()
    props["sat:relative_orbit"] = manifests[0].relative_orbit_number
    props["sat:absolute_orbit"] = manifests[0].absolute_orbit
    return props


def _get_platform(product_info) -> str:
    """The platform name."""
    return product_info.mission_id.lower().replace("s1", "sentinel-1", 1)


def _get_polarizations(output_ids: List[str]) -> List[str]:
    """The polarizations of the product."""
    return [oid for oid in output_ids if oid in _POLARIZATION_IDS]


def _fill_links(tile_data: TileData, input_tiles: List[InputTile]) -> List[Dict[str, Any]]:
    """Fill in the item ``links``."""
    links = [
        _create_link(
            s3mod.create_url(
                tile_data.get_s3_metadata_links_bucket(),
                tile_data.get_s3_key("metadata", "json"),
            ),
            "self",
            _MIME_JSON,
        )
    ]
    for tile in input_tiles:
        links.append(_create_link(tile.data_path, "derived_from", None))
    return links


def _create_link(href: str, rel: str, mime: Optional[str]) -> "OrderedDict[str, Any]":
    link: "OrderedDict[str, Any]" = OrderedDict()
    link["href"] = href
    link["rel"] = rel
    link["type"] = mime
    return link


def _fill_assets(tile_data: TileData, output_ids: List[str]) -> "OrderedDict[str, Any]":
    """Fill in the item ``assets``."""
    assets: "OrderedDict[str, Any]" = OrderedDict()
    for output_id in output_ids:
        if output_id is None or output_id == _IDENTIFIER_USERDATA:
            continue
        assets[output_id.lower()] = _create_asset(tile_data, output_id)
    assets[_METADATA_FILENAME] = _create_metadata_xml_asset(tile_data)
    return assets


def _create_asset(tile_data: TileData, output_id: str) -> "OrderedDict[str, Any]":
    """Build one asset entry."""
    import re as _re

    s3_key = tile_data.get_s3_key(output_id, "tif")
    s3_url = s3mod.create_url(tile_data.get_s3_metadata_links_bucket(), s3_key)
    title = _re.sub(r".*/([^/]+)\.tif+", r"\1", s3_key)

    asset: "OrderedDict[str, Any]" = OrderedDict()
    asset["href"] = s3_url
    asset["title"] = title
    asset["type"] = _MIME_TYPE_COG

    if output_id == "ANGLE":
        asset["description"] = "local incidence angle"
    elif output_id == "MASK":
        asset["description"] = "data mask"
    elif output_id == "AREA":
        asset["description"] = "normalized scattering area"
    else:
        asset["description"] = "polarization " + output_id

    asset["roles"] = ["data"]

    asset["proj:shape"] = [tile_data.geometry.width, tile_data.geometry.height]
    asset["proj:transform"] = _gdal_to_stac(tile_data.geometry.geo_transform)

    if output_id not in ("ANGLE", "MASK", "AREA"):
        asset["sar:polarizations"] = [output_id]

    return asset


def _create_metadata_xml_asset(tile_data: TileData) -> "OrderedDict[str, Any]":
    """Build the asset entry for the CARD4L XML."""
    import re as _re

    s3_key = tile_data.get_s3_key(_METADATA_FILENAME, "xml")
    s3_url = s3mod.create_url(tile_data.get_s3_metadata_links_bucket(), s3_key)
    title = _re.sub(r".*/([^/]+)\.xml", r"\1", s3_key)

    asset: "OrderedDict[str, Any]" = OrderedDict()
    asset["href"] = s3_url
    asset["title"] = title
    asset["type"] = _MIME_XML
    asset["roles"] = ["metadata", "card4l"]
    return asset


def _gdal_to_stac(gdal: List[float]) -> List[float]:
    """Reorder a GDAL geotransform into a STAC ``proj:transform``."""
    return [gdal[1], gdal[2], gdal[0], gdal[4], gdal[5], gdal[3]]


def _strip_nulls(value: Any) -> Any:
    """Recursively drop None values."""
    if isinstance(value, dict):
        result = OrderedDict()
        for k, v in value.items():
            if v is None:
                continue
            result[k] = _strip_nulls(v)
        return result
    if isinstance(value, (list, tuple)):
        return [_strip_nulls(v) for v in value]
    return value


def _json_default(obj: Any):
    if isinstance(obj, datetime):
        return _format_datetime(obj)
    raise TypeError(
        "Object of type %s is not JSON serializable" % type(obj).__name__
    )


def _format_datetime(dt: datetime) -> str:
    """ISO-8601 with an offset and no forced milliseconds."""
    text = dt.isoformat()
    # UTC is rendered as ...Z, so normalize +00:00 to Z.
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text
