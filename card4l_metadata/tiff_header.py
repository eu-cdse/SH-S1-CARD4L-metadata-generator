"""Minimal TIFF header reader.

Reads the values the CARD4L XML needs from the first band of a (possibly
truncated, header-only) TIFF: byte order, sample format, bits per sample and
the nodata value. Band information comes from ``gdal.Info`` on an in-memory
copy of the first 30000 bytes fetched via ``Card4lS3.open_tiff_header``, so no
full raster is required. GDAL does not report the TIFF byte order, so that is
taken from the byte order marker.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from osgeo import gdal
from typing import Optional

gdal.UseExceptions()

# TIFF tag values.
SAMPLE_FORMAT_UINT = 1
SAMPLE_FORMAT_INT = 2
SAMPLE_FORMAT_IEEEFP = 3


@dataclass
class TiffHeader:
    big_endian: bool
    bits_per_sample: int = 1
    sample_format: int = SAMPLE_FORMAT_UINT
    no_data_value: Optional[str] = None

    @property
    def byte_order_label(self) -> str:
        """Byte order as written into the XML."""
        return "Big Endian" if self.big_endian else "Little Endian"

    @property
    def sample_format_label(self) -> Optional[str]:
        """Sample format as written into the XML."""
        return {
            SAMPLE_FORMAT_IEEEFP: "FLOAT",
            SAMPLE_FORMAT_UINT: "UINT",
            SAMPLE_FORMAT_INT: "INT",
        }.get(self.sample_format)


def read_tiff_header(data: bytes) -> TiffHeader:
    """Read the first band's properties from TIFF header bytes via ``gdal.Info``."""
    if len(data) < 8:
        raise ValueError("Not enough bytes for a TIFF header")

    byte_order = data[:2]
    if byte_order == b"II":
        big_endian = False
    elif byte_order == b"MM":
        big_endian = True
    else:
        raise ValueError("Invalid TIFF byte order marker")

    info = _gdal_info(data)
    bands = info.get("bands") or []
    if not bands:
        raise ValueError("TIFF has no bands")
    band = bands[0]

    data_type = band["type"]
    image_structure = band.get("metadata", {}).get("IMAGE_STRUCTURE", {})

    nbits = image_structure.get("NBITS")
    if nbits is not None:
        bits_per_sample = int(nbits)
    else:
        bits_per_sample = gdal.GetDataTypeSize(gdal.GetDataTypeByName(data_type))

    return TiffHeader(
        big_endian=big_endian,
        bits_per_sample=bits_per_sample,
        sample_format=_sample_format(data_type, image_structure),
        no_data_value=_format_no_data(band.get("noDataValue")),
    )


def _gdal_info(data: bytes) -> dict:
    """Run ``gdal.Info`` (JSON) on TIFF bytes through a /vsimem file."""
    vsipath = "/vsimem/tiffheader_%s.tif" % uuid.uuid4().hex
    gdal.FileFromMemBuffer(vsipath, data)
    try:
        # A truncated header usually points to further IFDs (overviews) past
        # the end of the data; libtiff reports that as an error, so run without
        # exceptions and only fail if GDAL produced no info at all.
        with gdal.ExceptionMgr(useExceptions=False), gdal.quiet_errors():
            ds = gdal.Open(vsipath)
            if ds is None:
                raise ValueError("GDAL could not open the TIFF header")
            # -mdd all: include band IMAGE_STRUCTURE metadata (NBITS, PIXELTYPE).
            info = gdal.Info(ds, format="json", options=["-mdd", "all"])
            ds = None
    finally:
        gdal.Unlink(vsipath)
    if not info:
        raise ValueError("gdal.Info could not read the TIFF header")
    return info


def _sample_format(data_type: str, image_structure: dict) -> int:
    if "Float" in data_type:
        return SAMPLE_FORMAT_IEEEFP
    if data_type.startswith(("Int", "CInt")):
        return SAMPLE_FORMAT_INT
    # GDAL < 3.7 reports signed 8-bit rasters as Byte with PIXELTYPE=SIGNEDBYTE.
    if image_structure.get("PIXELTYPE") == "SIGNEDBYTE":
        return SAMPLE_FORMAT_INT
    return SAMPLE_FORMAT_UINT


def _format_no_data(value) -> Optional[str]:
    """Format the nodata value like the GDAL_NODATA tag text (e.g. "0", "nan")."""
    if value is None:
        return None
    # gdal.Info encodes non-finite values as the strings "NaN" / "Infinity".
    value = float(value)
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if value.is_integer():
        return str(int(value))
    return repr(value)
