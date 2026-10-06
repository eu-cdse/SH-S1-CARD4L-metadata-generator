"""Builds the CARD4L XML document with ``lxml.etree``.

Each helper below emits one element of the ``<Product>`` tree, preserving the
element names, ordering, attributes and constant strings the CARD4L format
requires. Where an element carries attributes as well as text (``Value``,
``Url``, ``BeamValues``) a small helper appends the corresponding child.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from lxml import etree
from shapely import wkt as shapely_wkt
from typing import List, Optional

from . import geom as geom_ops
from . import s3 as s3mod
from .dto import S1Collection
from .model import InputTile, S1Annotation, S1Manifest, TileData
from .number_format import format_double
from .s1_constants import (
    METERS_TO_DEGREES,
    POLARIZATIONS,
    Resolutions,
    get_enl,
    get_pixel_spacing,
)
from .s3 import MAX_OUTPUT_TIFF_HEADER_LENGTH
from .tiff_header import read_tiff_header

log = logging.getLogger(__name__)

_COPERNICUS_DEM_YEAR = 2021
_IDENTIFIER_USERDATA = "userdata"
_NUM_OVERVIEW_LEVELS = 7

# Output ids of the per-pixel metadata layers.
_OUTPUT_ID_DATA_MASK = "MASK"
_OUTPUT_ID_LOCAL_CONTRIBUTING_AREA = "AREA"
_OUTPUT_ID_LOCAL_INC_ANGLE = "ANGLE"

_R_RMSE_METERS = 4.4

_SATELLITE_REFERENCES = {
    "S1A": "http://database.eohandbook.com/database/missionsummary.aspx?missionID=575",
    "S1B": "http://database.eohandbook.com/database/missionsummary.aspx?missionID=576",
    "S1C": "http://database.eohandbook.com/database/missionsummary.aspx?missionID=577",
    "S1D": "http://database.eohandbook.com/database/missionsummary.aspx?missionID=814",
}

_ANNOTATION_BEAM_VALUES = re.compile(
    r"beam IW1: (?P<IW1>.*), beam IW2: (?P<IW2>.*), beam IW3: (?P<IW3>.*)"
)

# DEM instance names.
_MAPZEN = "MAPZEN"
_COPERNICUS = "COPERNICUS"
_COPERNICUS_30 = "COPERNICUS_30"
_COPERNICUS_90 = "COPERNICUS_90"
# The default DEM instance.
_DEFAULT_DEM = _COPERNICUS


# --- small element helpers ---------------------------------------------------


def _sub(parent, name: str, text: Optional[str] = None, **attrs) -> "etree._Element":
    el = etree.SubElement(parent, name)
    for key, val in attrs.items():
        if val is not None:
            el.set(key, val)
    if text is not None:
        el.text = text
    return el


def _opt(parent, name: str, text: Optional[str], **attrs) -> None:
    """Emit an element only when ``text`` is not None.

    A ``None`` value omits the element entirely:
    a null field is omitted from the output entirely (unlike primitive fields,
    which always serialize). Use this for nullable String / datetime / Integer
    fields; use :func:`_sub` for primitives, constants and container elements.
    """
    if text is not None:
        _sub(parent, name, text, **attrs)


def _d(value) -> Optional[str]:
    """Format a numeric value for the XML."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_double(value)
    return str(value)


def _url(parent, name: str, url: Optional[str]) -> None:
    """An element with a type="URL" attribute and the URL as text.

    A ``None`` url omits the element entirely, matching e.g.
    ``SatelliteReference`` when the mission is not in the reference map.
    """
    if url is None:
        return
    _sub(parent, name, url, type="URL")


def _value(parent, name: str, units: Optional[str], value) -> None:
    """An element with a units attribute and the value as text."""
    _sub(parent, name, _d(value), units=units)


def _beam_values(parent, name: str, units: str, s1_annotation_values: str) -> None:
    """The three IW beam values."""
    m = _ANNOTATION_BEAM_VALUES.match(s1_annotation_values)
    if not m:
        raise ValueError(
            "Invalid format of beam values in S1 annotations: " + s1_annotation_values
        )
    el = _sub(parent, name, None, units=units)
    for beam_id in ("IW1", "IW2", "IW3"):
        _sub(el, "Beam", _d(float(m.group(beam_id))), ID=beam_id)


# --- entry point -------------------------------------------------------------


def produce_xml(
    tile_data: TileData,
    output_ids: List[str],
    output_geom,
    input_tiles: List[InputTile],
    manifests: List[S1Manifest],
    annotations: List[S1Annotation],
    s1_collection: S1Collection,
    service_version: Optional[str],
    s3,
) -> str:
    """Build and serialize the ``<Product>`` document."""
    product = etree.Element("Product")
    product.set("type", "Normalized Radar Backscatter")
    product.set("version", "5.5")
    _url(
        product,
        "DocumentIdentifier",
        "https://ceos.org/ard/files/PFS/NRB/v5.5/CARD4L-PFS_NRB_v5.5.pdf",
    )

    _data_collection_time(product, input_tiles, manifests)
    _source_attributes(product, input_tiles, manifests, annotations, output_ids)
    _product_attributes(
        product, tile_data, manifests, output_ids, output_geom, s1_collection,
        service_version, s3,
    )

    return etree.tostring(
        product, pretty_print=True, xml_declaration=True, encoding="UTF-8"
    ).decode("utf-8")


# --- DataCollectionTime ------------------------------------------------------


def _data_collection_time(product, input_tiles, manifests) -> None:
    """Build the ``DataCollectionTime`` element."""
    starts = [m.start_time for m in manifests if m.start_time is not None]
    stops = [m.stop_time for m in manifests if m.stop_time is not None]
    el = _sub(product, "DataCollectionTime")
    _sub(el, "NumberOfAcquisitions", str(len(input_tiles)))
    _opt(el, "FirstAcquisitionDate", _time(min(starts)) if starts else None)
    _opt(el, "LastAcquisitionDate", _time(max(stops)) if stops else None)


def _time(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if dt is not None else None


# --- SourceAttributes --------------------------------------------------------


def _source_attributes(product, input_tiles, manifests, annotations, output_ids) -> None:
    """Fill in the ``SourceAttributes`` elements."""
    for i, input_tile in enumerate(input_tiles):
        manifest = manifests[i]
        annotation = annotations[i]
        resolutions = Resolutions.get(
            manifest.product_info.mode, manifest.product_info.resolution
        )
        mission_id = manifest.product_info.mission_id

        src = _sub(product, "SourceAttributes")
        src.set("acqID", str(i + 1))

        _url(src, "SourceDataRepository", input_tile.data_path)
        _sub(src, "Satellite", mission_id.replace("S1", "Sentinel-1", 1))
        _url(src, "SatelliteReference", _SATELLITE_REFERENCES.get(mission_id))
        # Fixed fields of SourceAttributes (declared after the above).
        _url(
            src,
            "ProductDefinitionReference",
            "https://sentinel.esa.int/documents/247904/1877131/Sentinel-1-Product-Definition",
        )
        _sub(src, "Instrument", "Synthetic Aperture Radar")
        _url(
            src,
            "SensorCalibration",
            "https://sentinel.esa.int/web/sentinel/technical-guides/sentinel-1-sar/"
            "sar-instrument/calibration",
        )

        # SourceDataAcquisitionTime
        acq = _sub(src, "SourceDataAcquisitionTime")
        _opt(acq, "StartTime", _time(manifest.start_time))
        _opt(acq, "EndTime", _time(manifest.stop_time))

        # SourceDataAcquisitionParameters
        params = _sub(src, "SourceDataAcquisitionParameters")
        _sub(params, "RadarBand", "C")
        _value(params, "RadarCenterFrequency", "Hz", 5.40500045433435e09)
        _opt(params, "ObservationMode", manifest.observation_mode)
        _opt(params, "Polarizations", manifest.polarizations)
        _sub(params, "AntennaPointing", "Right")
        _sub(params, "BeamID", "TOPS")

        # OrbitInformation
        orbit = _sub(src, "OrbitInformation")
        _opt(orbit, "PassDirection", manifest.pass_direction)
        _opt(orbit, "OrbitDataSource", _get_orbit_data_source(manifest))
        _opt(orbit, "OrbitDataFileName", _get_orbit_data_file(manifest))
        _value(orbit, "PlatformHeading", "deg", _normalize_angle(annotation.platform_heading))
        _value(orbit, "OrbitMeanAltitude", "m", 693000)

        # SourceProcParam
        proc = _sub(src, "SourceProcParam")
        _opt(proc, "ProcessingFacility", manifest.facility)
        _opt(proc, "ProcessingDate", _time(manifest.processing_date))
        _opt(
            proc,
            "SoftwareVersion",
            "%s, %s" % (manifest.software_name, manifest.software_version),
        )
        _opt(proc, "ProductID", manifest.raw_product_id)
        _sub(proc, "ProductLevel", "GRD")
        _opt(proc, "AzimuthNumberOfLooks", _d(annotation.azimuth_no_of_looks))
        _opt(proc, "RangeNumberOfLooks", _d(annotation.range_no_of_looks))
        _beam_values(proc, "AzimuthLookBandwidth", "Hz", annotation.azimuth_look_bandwidths)
        _beam_values(proc, "RangeLookBandwidth", "Hz", annotation.range_look_bandwidths)

        # SourceDataImageAttributes
        img = _sub(src, "SourceDataImageAttributes")
        _geometry_wkt(img, "SourceGeographicalExtent",
                      geom_ops.to_shape(input_tile.data_geometry), 12)
        _sub(img, "SourceDataGeometry", "Ground range")
        _value(img, "AzimuthPixelSpacing", "m", annotation.azimuth_pixel_spacing)
        _value(img, "RangePixelSpacing", "m", annotation.range_pixel_spacing)
        _beam_values(img, "AzimuthResolution", "m", resolutions.azimuth_resolution)
        _beam_values(img, "RangeResolution", "m", resolutions.range_resolution)
        _value(img, "IncAngleNearRange", "deg", annotation.incidence_angle_min)
        _value(img, "IncAngleFarRange", "deg", annotation.incidence_angle_max)

        # PerformanceIndicators (per polarization present in outputs).
        for pol in POLARIZATIONS:
            if pol in output_ids:
                _performance_indicators(src, pol, manifest)


def _performance_indicators(src, polarization: str, manifest: S1Manifest) -> None:
    """Build the ``PerformanceIndicators`` element."""
    is_ew = manifest.observation_mode == "EW"
    el = _sub(src, "PerformanceIndicators")
    el.set("pol", polarization)
    _url(
        el,
        "NoiseReference",
        "https://sentinel.esa.int/documents/247904/2142675/"
        "Thermal-Denoising-of-Products-Generated-by-Sentinel-1-IPF",
    )
    # NoiseEquivalentIntensity (fixed).
    nei = _sub(el, "NoiseEquivalentIntensity", None, type="Sigma0", units="dB")
    _sub(nei, "Estimates", "-30", type="min")
    _sub(nei, "Estimates", "-22", type="max")

    _sub(el, "EquivalentNumberOfLooks", _d(get_enl(manifest.product_info)))
    # PeakSideLobeRatio: range (fixed) then azimuth.
    _sub(el, "PeakSideLobeRatio", _d(-21.2), units="dB", direction="range")
    _sub(el, "PeakSideLobeRatio", _d(-28.3 if is_ew else -21.2),
         units="dB", direction="azimuth")
    # IntegratedSideLobeRatio: range (fixed) then azimuth.
    _sub(el, "IntegratedSideLobeRatio", _d(-16.1), units="dB", direction="range")
    _sub(el, "IntegratedSideLobeRatio", _d(-19.4 if is_ew else -16.1),
         units="dB", direction="azimuth")


def _get_orbit_data_file(manifest: S1Manifest) -> Optional[str]:
    """The orbit data file name."""
    if not manifest.orbit_file:
        return None
    return re.sub(r".*/", "", manifest.orbit_file)


def _get_orbit_data_source(manifest: S1Manifest) -> str:
    """The orbit data source."""
    role = manifest.orbit_file_role
    if not role:
        return "DOWNLINK"
    mapping = {"AUX_POE": "POEORB", "AUX_RES": "RESORB", "AUX_PRE": "PREORB"}
    if role in mapping:
        return mapping[role]
    log.error(
        "Invalid orbitFileRole %s, will be used verbatim instead of converting to orbitType",
        role,
    )
    return role


def _normalize_angle(angle: Optional[float]) -> Optional[float]:
    """Normalize an angle."""
    if angle is not None and angle < 0.0:
        return angle + 360.0
    return angle


# --- CARD4LProductAttributes -------------------------------------------------


def _product_attributes(
    product, tile_data, manifests, output_ids, output_geom, s1_collection,
    service_version, s3,
) -> None:
    """Build the ``CARD4LProductAttributes`` element."""
    attrs = _sub(product, "CARD4LProductAttributes")
    processing = s1_collection.processing

    # DataAccess
    data_access = _sub(attrs, "DataAccess")
    _sub(data_access, "ProcessingFacility", "Sentinel Hub, Sinergise")
    _sub(data_access, "ProcessingTime", _time(tile_data.metadata_generation_time))
    _sub(data_access, "SoftwareVersion", _get_software_version(service_version))
    _url(data_access, "Repository", _get_output_s3_dir_url(tile_data))

    # AncillaryData
    _ancillary_data(attrs, processing, output_geom)

    # ProductSampleSpacing
    spacing = _sub(attrs, "ProductSampleSpacing")
    _value(spacing, "ProductColumnSpacing", _spacing_units(tile_data), tile_data.resolution)
    _value(spacing, "ProductRowSpacing", _spacing_units(tile_data), tile_data.resolution)

    # Filtering
    _filtering(attrs, tile_data, manifests)

    # ProductBoundingBox (CornerCoords UL + LR)
    _corner_coords(attrs, tile_data)

    # ProductGeographicalExtent
    _geometry_wkt(attrs, "ProductGeographicalExtent", output_geom, 8)

    # ProductImageSize
    size = _sub(attrs, "ProductImageSize")
    _sub(size, "NumberLines", str(tile_data.geometry.height))
    _sub(size, "NumPixelsPerLine", str(tile_data.geometry.width))

    # PixelCoordinateConvention (fixed)
    _sub(attrs, "PixelCoordinateConvention", "pixel centre")

    # CoordinateReferenceSystem (EPSG + WKT)
    _sub(attrs, "CoordinateReferenceSystem", str(tile_data.geometry.epsg), type="EPSG")
    _sub(
        attrs,
        "CoordinateReferenceSystem",
        _get_map_projection(tile_data, output_ids, s3),
        type="WKT",
    )

    # PerPixelMetadata
    _per_pixel_metadata(attrs, tile_data, output_ids, s3)

    # BackscatterMeasurementData (per polarization)
    for pol in POLARIZATIONS:
        if pol in output_ids:
            _backscatter_element(attrs, tile_data, pol, s3)

    # NoiseRemoval (fixed)
    noise = _sub(attrs, "NoiseRemoval")
    _sub(noise, "NoiseRemovalApplied", "true")
    _url(
        noise,
        "NRAlgorithm",
        "https://sentinel.esa.int/web/sentinel/radiometric-calibration-of-level-1-products",
    )

    # RadiometricTerrainCorrections (fixed)
    rtc = _sub(attrs, "RadiometricTerrainCorrections")
    _url(rtc, "RTCAlgorithm", "https://doi.org/10.1109/TGRS.2011.2120616")
    _sub(rtc, "DEMOversamplingFactor", _d(2.0))

    # RadiometricAccuracy (fixed)
    rad = _sub(attrs, "RadiometricAccuracy")
    _url(
        rad,
        "RadAccuracyReference",
        "https://sentinel.esa.int/documents/247904/1877131/Sentinel-1-Product-Definition",
    )
    _sub(rad, "Absolute", "1", units="dB", confidence="3σ")
    _sub(rad, "Relative", "0.1", units="dB", confidence="3σ")

    # GeometricCorrection
    _geometric_correction(attrs, processing, output_geom, tile_data)

    # GriddingConvention (fixed)
    _url(
        attrs,
        "GriddingConvention",
            "https://docs.planet.com/develop/apis/batch-processing/#1-tiling-grid",
    )

    # GridName
    _sub(attrs, "GridName", tile_data.batch_grid.name)


def _spacing_units(tile_data: TileData) -> str:
    """Sample spacing units: ``m`` for metre grids, ``deg`` otherwise."""
    return "m" if tile_data.batch_grid.properties.unit == "METRE" else "deg"


def _get_software_version(service_version: Optional[str]) -> str:
    """The software version string."""
    if not service_version:
        return "Batch API, unknown version"
    return "Batch API, v" + service_version


def _get_output_s3_dir_url(tile_data: TileData) -> str:
    """The S3 directory URL of the delivered outputs."""
    directory = tile_data.get_s3_key(_IDENTIFIER_USERDATA, "json")
    directory = directory[: directory.rfind("/")]
    return s3mod.create_url(tile_data.get_s3_metadata_links_bucket(), directory)


def _ancillary_data(attrs, processing, output_geom) -> None:
    """Build the ``AncillaryData`` element."""
    from .copernicus_dem import Copernicus10DemResolver

    dem_instance = processing.dem_instance or _MAPZEN
    if dem_instance == _MAPZEN:
        dem = "Mapzen DEM"
    elif dem_instance == _COPERNICUS:
        dem = "Copernicus DEM, %s, release %d" % (
            Copernicus10DemResolver.resolve(output_geom),
            _COPERNICUS_DEM_YEAR,
        )
    elif dem_instance == _COPERNICUS_30:
        dem = "Copernicus DEM, GLO-30, release %d" % _COPERNICUS_DEM_YEAR
    elif dem_instance == _COPERNICUS_90:
        dem = "Copernicus DEM, GLO-90, release %d" % _COPERNICUS_DEM_YEAR
    else:
        raise ValueError("Unknown DEM instance " + dem_instance)
    _sub(attrs, "AncillaryData", None, DEM=dem, EGM="EGM2008, 1x1 minute grid in WGS84")


def _filtering(attrs, tile_data: TileData, manifests) -> None:
    """Build the ``Filtering`` element."""
    is_metre = tile_data.batch_grid.properties.unit == "METRE"
    target_pixel_spacing = (
        tile_data.resolution if is_metre else tile_data.resolution / METERS_TO_DEGREES
    )
    src_pixel_spacing = get_pixel_spacing(manifests[0].product_info)
    stride = max(int(target_pixel_spacing // src_pixel_spacing), 1)

    el = _sub(attrs, "Filtering")
    if stride == 1:
        _sub(el, "FilterApplied", "false")
        return
    import math

    overview_level = min(int(math.log(stride) / math.log(2)), _NUM_OVERVIEW_LEVELS - 1)
    window = 2 ** overview_level
    _sub(el, "FilterApplied", "true")
    _sub(el, "FilterType", "block average")
    _sub(el, "WindowSizeCol", str(window))
    _sub(el, "WindowSizeLine", str(window))


def _corner_coords(attrs, tile_data: TileData) -> None:
    """The upper-left and lower-right corners of an envelope.

    For geographic CRS the corners carry Latitude/Longitude (deg); for projected
    CRS they carry Easting/Northing (m).
    """
    env = tile_data.geometry.native_envelope
    geographic = tile_data.geometry.is_geographic
    ul_x, ul_y = env.top_left()
    lr_x, lr_y = env.bottom_right()

    for corner, (x, y) in (("UL", (ul_x, ul_y)), ("LR", (lr_x, lr_y))):
        el = _sub(attrs, "ProductBoundingBox", None, corner=corner)
        if geographic:
            _value(el, "Latitude", "deg", y)
            _value(el, "Longitude", "deg", x)
        else:
            _value(el, "Easting", "m", x)
            _value(el, "Northing", "m", y)


def _geometry_wkt(parent, name: str, geom, indent_level: int) -> None:
    """A geometry element: order/type attributes plus the padded WKT text."""
    wkt = "\n" + " " * (indent_level + 4) + shapely_wkt.dumps(geom, rounding_precision=15) + "\n" + " " * indent_level
    _sub(parent, name, wkt, order="longitude latitude", type="WKT")


def _per_pixel_metadata(attrs, tile_data, output_ids, s3) -> None:
    """Build the ``PerPixelMetadata`` element."""
    el = _sub(attrs, "PerPixelMetadata")
    if _OUTPUT_ID_DATA_MASK in output_ids:
        _tiff_metadata(el, "DataMask", tile_data, _OUTPUT_ID_DATA_MASK, s3,
                       sample_units=None, sample_type="Mask", bit_values=True)
    if _OUTPUT_ID_LOCAL_CONTRIBUTING_AREA in output_ids:
        _tiff_metadata(el, "LocalContributingArea", tile_data,
                       _OUTPUT_ID_LOCAL_CONTRIBUTING_AREA, s3,
                       sample_units="square_meters/square_meters",
                       sample_type="Normalized Scattering Area")
    if _OUTPUT_ID_LOCAL_INC_ANGLE in output_ids:
        _tiff_metadata(el, "LocalIncAngle", tile_data, _OUTPUT_ID_LOCAL_INC_ANGLE, s3,
                       sample_units="deg", sample_type="Angle")


def _tiff_metadata(parent, name, tile_data, output_id, s3,
                   sample_units, sample_type, bit_values=False) -> None:
    """Build a ``TiffMetadata`` element from an output TIFF header.

    Field order mirrors ``TiffMetadata``: FileName, SampleType, DataFormat,
    NoDataValue, DataType, BitsPerSample, ByteOrder, BitValues.
    """
    el = _sub(parent, name)
    _sub(el, "FileName", _get_filename(tile_data, output_id))
    _value(el, "SampleType", sample_units, sample_type)
    _sub(el, "DataFormat", "geotiff")

    header = _read_output_tiff_header(tile_data, output_id, s3)
    no_data = header.no_data_value if header else None
    _opt(el, "NoDataValue", no_data)
    if header is not None:
        _opt(el, "DataType", header.sample_format_label)
        _sub(el, "BitsPerSample", str(header.bits_per_sample))
        _sub(el, "ByteOrder", header.byte_order_label)
    else:
        _sub(el, "BitsPerSample", "0")

    if bit_values:
        bv = _sub(el, "BitValues")
        _sub(bv, "NoData", "0")
        _sub(bv, "ValidData", "1")
        _sub(bv, "InvalidData", "2")


def _backscatter_element(attrs, tile_data, polarization, s3) -> None:
    """Build the ``BackscatterMeasurementData`` element."""
    el = _sub(attrs, "BackscatterMeasurementData")
    _sub(el, "BackscatterMeasurement", "Gamma-0")
    _sub(el, "BackscatterConvention", "linear power")
    _value(el, "BackscatterConversionEq", "dB", "10*log10(DN)")
    _sub(el, "Polarization", polarization)
    _sub(el, "FileName", _get_filename(tile_data, polarization))
    _sub(el, "DataFormat", "geotiff")

    header = _read_output_tiff_header(tile_data, polarization, s3)
    if header is not None:
        _opt(el, "DataType", header.sample_format_label)
        _sub(el, "BitsPerSample", str(header.bits_per_sample))
        _sub(el, "ByteOrder", header.byte_order_label)
    else:
        _sub(el, "BitsPerSample", "0")


def _read_output_tiff_header(tile_data, output_id, s3):
    """Read the output TIFF header from S3; log and return None on failure."""
    key = tile_data.get_s3_key(output_id, "tif")
    try:
        data = s3.read_bytes(tile_data.get_s3_bucket(), key, num_bytes=MAX_OUTPUT_TIFF_HEADER_LENGTH)
        return read_tiff_header(data)
    except Exception:
        log.warning(
            "Cannot read or parse TIFF header from s3://%s/%s. Values will be missing.",
            tile_data.get_s3_bucket(),
            key,
        )
        return None


def _get_filename(tile_data: TileData, output_id: str) -> str:
    """The file name: the key with its directory stripped."""
    key = tile_data.get_s3_key(output_id, "tif")
    return re.sub(r".*/", "", key)


def _get_map_projection(tile_data, output_ids, s3) -> Optional[str]:
    """WKT of the output CRS, plus the trailing padding the format carries.

    We reuse the native CRS pretty-WKT already parsed from the tile geometry and
    append the trailing padding the element carries.
    """
    tiff_output_id = next(
        (oid for oid in output_ids if oid != _IDENTIFIER_USERDATA), None
    )
    if tiff_output_id is None:
        return None
    wkt = tile_data.geometry.native_pretty_wkt
    if not wkt:
        return None
    return wkt + "        "


def _geometric_correction(attrs, processing, output_geom, tile_data) -> None:
    """Build the ``GeometricCorrection`` element."""
    el = _sub(attrs, "GeometricCorrection")

    # DigitalElevationModel
    _digital_elevation_model(el, processing, output_geom)

    _sub(el, "GeoCorrMethod", "Range Doppler Terrain Correction")
    _url(
        el,
        "GeoCorrAlgorithm",
        "https://sentinel.esa.int/documents/247904/1653442/Guide-to-Sentinel-1-Geocoding.pdf",
    )

    acc = _sub(el, "GeoCorrAccuracy", None, type="GTC")
    for value in _create_rrmse(tile_data):
        _value(acc, "rRMSE", value[0], value[1])
    _value(acc, "NorthernBias", "m", -0.2)
    _value(acc, "NorthernSTDev", "m", 0.5)
    _value(acc, "EasternBias", "m", -4.4)
    _value(acc, "EasternSTDev", "m", 0.5)
    _url(acc, "GeoAccuracyReference", "https://ieeexplore.ieee.org/document/9554039")

    # ResamplingMethod (upsampling interpolator, default NEAREST).
    upsampling = (processing.upsampling if processing and processing.upsampling else "NEAREST")
    _sub(el, "ResamplingMethod", upsampling)


def _digital_elevation_model(parent, processing, output_geom) -> None:
    """Build the ``DigitalElevationModel`` element."""
    from .copernicus_dem import Copernicus10DemResolver

    dem_instance = _get_dem_filtering_instance(processing.dem_instance)
    url_mapzen = "https://github.com/tilezen/joerd/tree/master/docs"
    url_copernicus = (
        "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model"
    )
    if dem_instance == "MAPZEN":
        dem_ref, dem_type = url_mapzen, "MAPZEN"
    elif dem_instance == "COPERNICUS":
        dem_ref = url_copernicus
        dem_type = "%s, release %d" % (
            Copernicus10DemResolver.resolve(output_geom),
            _COPERNICUS_DEM_YEAR,
        )
    elif dem_instance == "COPERNICUS_30":
        dem_ref = url_copernicus
        dem_type = "GLO-30, release %d" % _COPERNICUS_DEM_YEAR
    elif dem_instance == "COPERNICUS_90":
        dem_ref = url_copernicus
        dem_type = "GLO-90, release %d" % _COPERNICUS_DEM_YEAR
    else:
        raise ValueError("Unknown DEM instance " + dem_instance)

    el = _sub(parent, "DigitalElevationModel", None, dem="Surface")
    _url(el, "DEMReference", dem_ref)
    _sub(el, "DEMType", dem_type)
    _sub(el, "DEMResamplingMethod", "BILINEAR")
    _url(
        el,
        "EGMReference",
        "https://earth-info.nga.mil/index.php?dir=wgs84&action=wgs84#egm2008",
    )
    _sub(el, "EGMType", "EGM2008, 1x1 minute grid in WGS84")
    _sub(el, "EGMResamplingMethod", "BILINEAR")


def _get_dem_filtering_instance(s1_dem_instance: Optional[str]) -> str:
    """Resolve the DEM instance used for filtering.

    Maps the S1DatasourceAttributes DEM-instance name to the DemCollection
    constant used by DigitalElevationModel. The private DemCollection UUID-based
    constants map back to the plain names used in the switch below.
    """
    if s1_dem_instance is None:
        return _DEFAULT_DEM
    if s1_dem_instance == _MAPZEN:
        return "MAPZEN"
    if s1_dem_instance == _COPERNICUS:
        return "COPERNICUS"
    if s1_dem_instance == _COPERNICUS_30:
        return "COPERNICUS_30"
    if s1_dem_instance == _COPERNICUS_90:
        return "COPERNICUS_90"
    raise ValueError("Invalid DEM instance: " + s1_dem_instance)


def _create_rrmse(tile_data: TileData):
    """Two rRMSE values (metres and samples), 2-decimal format."""
    is_metre = tile_data.batch_grid.properties.unit == "METRE"
    resolution_m = (
        tile_data.resolution if is_metre else tile_data.resolution / METERS_TO_DEGREES
    )
    return [
        ("m", _two_decimals(_R_RMSE_METERS)),
        ("samples", _two_decimals(_R_RMSE_METERS / resolution_m)),
    ]


def _two_decimals(value: float) -> str:
    """Format with up to two decimals, trailing zeros removed."""
    text = "%.2f" % value
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
