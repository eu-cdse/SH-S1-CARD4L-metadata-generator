"""S1 product naming, pixel spacing, ENL and resolution tables (the parts
``Resolutions``) — only the parts used by the CARD4L metadata flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

METERS_TO_DEGREES = 8.983152841195215e-6

_PRODUCT_NAMING_SUBPATTERN = (
    r"(?P<mission>S1A|S1B|S1C|S1D)"
    r"_(?P<mode>[SIWEVMN1-6]{2})"
    r"_(?P<type>SLC|GRD)(?P<resolution>[FHM_])"
    r"_1S(?P<polarisation>SH|SV|DH|DV|HH|HV|VV|VH)"
    r"_(?P<start>[0-9]{8}T[0-9]{6})"
    r"_(?P<stop>[0-9]{8}T[0-9]{6})"
    r"_(?P<orbit>[0-9]{6})"
    r"_(?P<datatake>[0-9A-F]{6})"
    r"_(?P<uniqueid>[0-9A-F]{4})"
    r"(?P<cog>_COG)?"
)

PAT_PRODUCT = re.compile(r"^" + _PRODUCT_NAMING_SUBPATTERN + r".*$")

_PRODUCTID_DATETIME_FORMAT = "%Y%m%dT%H%M%S"

# Polarizations, in the order they appear in the metadata.
POLARIZATIONS = ("SH", "SV", "DH", "DV", "HH", "HV", "VV", "VH")


@dataclass
class S1ProductInfo:
    """The fields decoded from an S1 product id."""

    id: Optional[str] = None
    mission_id: Optional[str] = None
    product_type: Optional[str] = None
    resolution: Optional[str] = None
    mode: Optional[str] = None
    polarisation: Optional[str] = None
    absolute_orbit_number: int = 0
    mission_data_take_id: int = 0
    product_unique_identifier: Optional[str] = None
    start_time: Optional[datetime] = None
    stop_time: Optional[datetime] = None


def create_product_info(product_id: str) -> S1ProductInfo:
    """Decode an S1 product id."""
    m = PAT_PRODUCT.match(product_id)
    if not m:
        raise ValueError("Unsupported product: " + product_id)
    clean_id = product_id.replace(".SAFE", "")
    return S1ProductInfo(
        id=clean_id,
        mission_id=m.group("mission"),
        mode=m.group("mode"),
        product_type=m.group("type"),
        resolution=m.group("resolution"),
        polarisation=m.group("polarisation"),
        absolute_orbit_number=int(m.group("orbit")),
        mission_data_take_id=int(m.group("datatake"), 16),
        product_unique_identifier=m.group("uniqueid"),
        start_time=datetime.strptime(m.group("start"), _PRODUCTID_DATETIME_FORMAT),
        stop_time=datetime.strptime(m.group("stop"), _PRODUCTID_DATETIME_FORMAT),
    )


# --- Pixel spacing / ENL lookup tables -------------------
# Keyed by (resolution, acquisition mode). Resolution letters map to the
# Resolution letter of the product id: H->HIGH, M->MEDIUM, F->FULL.

_RESOLUTION_BY_LETTER = {"H": "HIGH", "M": "MEDIUM", "F": "FULL"}

# Pixel spacing in metres, keyed by (resolution, acquisition mode).
_PIXEL_SPACING = {
    ("FULL", "SM"): 3.5,
    ("HIGH", "SM"): 10.0,
    ("HIGH", "IW"): 10.0,
    ("HIGH", "EW"): 25.0,
    ("MEDIUM", "SM"): 40.0,
    ("MEDIUM", "IW"): 40.0,
    ("MEDIUM", "EW"): 40.0,
    ("MEDIUM", "WV"): 25.0,
}

# Equivalent number of looks, keyed by (resolution, acquisition mode).
_ENL = {
    ("FULL", "SM"): 3.7,
    ("HIGH", "IW"): 4.4,
    ("HIGH", "EW"): 2.7,
    ("HIGH", "SM"): 29.7,
    ("MEDIUM", "IW"): 81.8,
    ("MEDIUM", "EW"): 10.7,
    ("MEDIUM", "SM"): 398.4,
    ("MEDIUM", "WV"): 123.7,
}


def _resolution_enum(product_info: S1ProductInfo) -> str:
    letter = product_info.resolution
    if letter not in _RESOLUTION_BY_LETTER:
        raise ValueError("Unknown S1 resolution " + str(letter))
    return _RESOLUTION_BY_LETTER[letter]


def get_pixel_spacing(product_info: S1ProductInfo) -> float:
    """Pixel spacing in metres."""
    key = (_resolution_enum(product_info), product_info.mode)
    if key not in _PIXEL_SPACING:
        raise ValueError(
            "Cannot determine pixel spacing for resolution: %s and acquisition mode: %s"
            % key
        )
    return _PIXEL_SPACING[key]


def get_enl(product_info: S1ProductInfo) -> float:
    """Equivalent number of looks."""
    key = (_resolution_enum(product_info), product_info.mode)
    if key not in _ENL:
        raise ValueError(
            "Cannot determine ENL for resolution: %s and acquisition mode: %s" % key
        )
    return _ENL[key]


# --- Azimuth/range resolution lookup ---------------------

# (azimuth resolution, range resolution), keyed by (mode, resolution).
_RESOLUTIONS = {
    "IWH": (
        "beam IW1: 22.5, beam IW2: 22.6, beam IW3: 22.6",
        "beam IW1: 20.4, beam IW2: 20.3, beam IW3: 20.5",
    ),
    "IWM": (
        "beam IW1: 90.2, beam IW2: 90.6, beam IW3: 90.3",
        "beam IW1: 87.9, beam IW2: 87.8, beam IW3: 88.7",
    ),
    "EWH": (
        "beam EW1: 51.5, beam EW2: 51.1, beam EW3: 51.3, beam EW4: 51.1, beam EW5: 51.5",
        "beam EW1: 49.1, beam EW2: 50.3, beam EW3: 50.4, beam EW4: 50.7, beam EW5: 51.4",
    ),
    "EWM": (
        "beam EW1: 90.1, beam EW2: 89.4, beam EW3: 86.9, beam EW4: 86.5, beam EW5: 90.1",
        "beam EW1: 90.9, beam EW2: 93.1, beam EW3: 93.3, beam EW4: 93.8, beam EW5: 95.1",
    ),
}


@dataclass
class Resolutions:
    """Azimuth and range resolution for a mode/resolution pair."""

    azimuth_resolution: Optional[str]
    range_resolution: Optional[str]

    @staticmethod
    def get(mode: str, resolution: str) -> Optional["Resolutions"]:
        entry = _RESOLUTIONS.get(str(mode) + str(resolution))
        if entry is None:
            return None
        return Resolutions(azimuth_resolution=entry[0], range_resolution=entry[1])
