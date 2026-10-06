"""Domain models carrying the state passed between the producer stages.

These are plain dataclasses carrying the state passed between the producer
stages. ``Card4lProcessTask`` is the program's input (loaded from JSON); the
rest are built during processing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .dto import ObjectStorageInfoDto
from .s1_constants import S1ProductInfo


class MetadataStatus(str, Enum):
    """The outcome of producing a task's metadata."""

    WAITING = "WAITING"
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


@dataclass
class Card4lProcessTask:
    """One metadata-production task.

    This is the standalone program's input, built from a ``batchTaskId`` string
    instead of being read from the ``card4l_metadata.process_task`` DB table.
    """

    batch_task_id: Optional[str] = None
    status: MetadataStatus = MetadataStatus.WAITING
    created: Optional[datetime] = None
    error: Optional[str] = None

    @staticmethod
    def from_batch_task_id(batch_task_id: str) -> "Card4lProcessTask":
        """Build a task from a ``batchTaskId`` string."""
        return Card4lProcessTask(
            batch_task_id=batch_task_id,
            created=datetime.now(),
        )


@dataclass
class GridTile:
    """One tile of the batch tiling grid.

    ``geometry`` is a shapely geometry (the tile polygon read from the feature
    manifest GeoPackage).
    """

    name: Optional[str] = None
    geometry: Any = None


@dataclass
class InputTile:
    """One input scene of a processed tile, from its userdata."""

    data_path: Optional[str] = None
    data_geometry: Any = None  # GeoJSON dict

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "InputTile":
        return InputTile(
            data_path=data.get("dataPath"),
            data_geometry=data.get("dataGeometry"),
        )


@dataclass
class Userdata:
    """The userdata document written alongside a processed tile."""

    tiles: List[InputTile] = field(default_factory=list)
    service_version: Optional[str] = None

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "Userdata":
        return Userdata(
            tiles=[InputTile.from_json(t) for t in (data.get("tiles") or [])],
            service_version=data.get("serviceVersion"),
        )


_TILE_ID_PATTERN = re.compile(
    r"(.*/)?(?P<tileName>[^/]+)/(?P<datatakeYear>\d+)/(?P<datatakeMonth>\d+)"
    r"/(?P<datatakeDay>\d+)/(?P<datatakeId>\w+)/s1_rtc_[^/]+"
)


@dataclass
class TileId:
    """The tile name and datatake a delivered object belongs to."""

    tile_name: str
    datatake_year: int
    datatake_month: int
    datatake_day: int
    datatake_id: str

    @staticmethod
    def from_s3_key(s3_key: str) -> "TileId":
        m = _TILE_ID_PATTERN.match(s3_key)
        if not m:
            raise ValueError("S3 path %s does not match Card4l pattern" % s3_key)
        return TileId(
            tile_name=m.group("tileName"),
            datatake_year=int(m.group("datatakeYear")),
            datatake_month=int(m.group("datatakeMonth")),
            datatake_day=int(m.group("datatakeDay")),
            datatake_id=m.group("datatakeId"),
        )


@dataclass
class TileData:
    """Everything the producers need about one processed tile."""

    batch_grid: Any  # TilingGridDescriptorDto
    id: TileId
    geometry: Any  # TiffGeometry
    resolution: float
    metadata_generation_time: datetime
    delivery: ObjectStorageInfoDto

    def get_s3_bucket(self) -> str:
        return self.delivery.url.bucket

    def get_s3_metadata_links_bucket(self) -> str:
        bucket = self.get_s3_bucket()
        return bucket

    def get_s3_key(self, output_id: str, fmt_extension: str) -> str:
        """The S3 key of one output."""
        from . import path_template

        return path_template.get_path_uri(
            self.delivery,
            output_id,
            self.id.tile_name,
            fmt_extension,
        ).key


@dataclass
class S1Manifest:
    """The fields read from an S1 ``manifest.safe``."""

    raw_product_id: Optional[str] = None
    product_info: Optional[S1ProductInfo] = None
    start_time: Optional[datetime] = None
    stop_time: Optional[datetime] = None
    observation_mode: Optional[str] = None
    polarizations: Optional[str] = None
    pass_direction: Optional[str] = None
    orbit_file: Optional[str] = None
    orbit_file_role: Optional[str] = None
    facility: Optional[str] = None
    processing_date: Optional[datetime] = None
    software_name: Optional[str] = None
    software_version: Optional[str] = None
    absolute_orbit: Optional[int] = None
    relative_orbit_number: Optional[int] = None


@dataclass
class S1Annotation:
    """The fields read from an S1 annotation XML."""

    platform_heading: Optional[float] = None
    range_no_of_looks: Optional[int] = None
    azimuth_no_of_looks: Optional[int] = None
    range_pixel_spacing: Optional[float] = None
    azimuth_pixel_spacing: Optional[float] = None
    range_look_bandwidths: Optional[str] = None
    azimuth_look_bandwidths: Optional[str] = None
    incidence_angle_min: Optional[float] = None
    incidence_angle_max: Optional[float] = None


def utc_now() -> datetime:
    """The current time, as an aware UTC datetime."""
    return datetime.now(timezone.utc)
