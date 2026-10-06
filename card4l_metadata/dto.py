"""The API payload objects consumed by the CARD4L metadata flow.

Only the fields actually read by the producer are modelled. Each class is built
from the parsed JSON returned by the BatchV2 service (or from the userdata files
on S3). Attribute names follow the JSON property names of the API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --- ObjectStorageUri

_URL_PATTERN = re.compile(
    r"^(?P<type>[a-z0-9]+)://(?P<bucket>[:A-Za-z0-9\-_.]+)"
    r"(/(?P<key>[<>A-Za-z0-9/\-_.(): ]*)(\?(?P<signature>[a-zA-Z0-9\-_.=&%:+/;\"+]+))?)?$"
)


class ObjectStorageUri:
    """A storage URI: type, bucket, key, signature, and its string form."""

    def __init__(self, url: str):
        m = _URL_PATTERN.match(url)
        if not m:
            raise ValueError("Illegal url: " + url)
        self.type = m.group("type").lower()
        self.bucket = m.group("bucket")
        self.key = m.group("key")
        self.signature = m.group("signature")

    @classmethod
    def of(cls, storage_type: str, bucket: str, key: str) -> "ObjectStorageUri":
        return cls("%s://%s/%s" % (storage_type, bucket, key))

    def is_absolute(self) -> bool:
        return bool(self.key) and not self.key.endswith("/")

    def __str__(self) -> str:
        uri = "%s://%s" % (self.type, self.bucket)
        if self.key is not None:
            uri += "/" + self.key
        if self.signature is not None:
            uri += "?" + self.signature
        return uri


@dataclass
class ObjectStorageInfoDto:
    """Resolves the storage url across the s3 / gs / destinations sections."""

    url: Optional[ObjectStorageUri]

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "ObjectStorageInfoDto":
        url = None
        for key in ("s3", "gs", "pl:destinations"):
            section = data.get(key)
            if section and section.get("url"):
                url = ObjectStorageUri(section["url"])
                break
        return ObjectStorageInfoDto(url=url)


# --- Processing config --


@dataclass
class S1Processing:
    """The S1 processing options (fields used by the metadata flow)."""

    orthorectify: Optional[bool] = None
    dem_instance: Optional[str] = None
    upsampling: Optional[str] = None  # Interpolator name
    back_coeff: Optional[str] = None

    @staticmethod
    def from_json(data: Optional[Dict[str, Any]]) -> "S1Processing":
        data = data or {}
        return S1Processing(
            orthorectify=data.get("orthorectify"),
            dem_instance=data.get("demInstance"),
            upsampling=data.get("upsampling"),
            back_coeff=data.get("backCoeff"),
        )


@dataclass
class S1Collection:
    """An S1 collection; only ``processing`` is used here."""

    processing: S1Processing = field(default_factory=S1Processing)

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "S1Collection":
        return S1Collection(processing=S1Processing.from_json(data.get("processing")))


# --- Tiling grid descriptor ------------------


@dataclass
class TilingGridProperties:
    tile_width: float = 0.0
    tile_height: float = 0.0
    unit: Optional[str] = None  # "METRE" or "DEGREE"


@dataclass
class TilingGridDescriptorDto:
    name: Optional[str] = None
    properties: TilingGridProperties = field(default_factory=TilingGridProperties)

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "TilingGridDescriptorDto":
        props = data.get("properties") or {}
        return TilingGridDescriptorDto(
            name=data.get("name"),
            properties=TilingGridProperties(
                tile_width=props.get("tileWidth", 0.0),
                tile_height=props.get("tileHeight", 0.0),
                unit=props.get("unit"),
            ),
        )


# --- Tiling grid input -------------------------------


@dataclass
class TilingGridInput:
    id: Optional[int] = None
    resolution: Optional[float] = None

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "TilingGridInput":
        return TilingGridInput(id=data.get("id"), resolution=data.get("resolution"))


# --- Batch process task ----------


@dataclass
class BatchOutputResponse:
    identifier: Optional[str] = None


@dataclass
class ProcessRequest:
    """Subset of the inner ``processRequest`` used by the metadata flow."""

    response_identifiers: List[str] = field(default_factory=list)
    s1_collection: Optional[S1Collection] = None

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "ProcessRequest":
        data = data or {}
        output = data.get("output") or {}
        responses = output.get("responses") or []
        response_ids = [r.get("identifier") for r in responses]

        s1_collection = None
        input_data = ((data.get("input") or {}).get("data")) or []
        if input_data:
            s1_collection = S1Collection.from_json(input_data[0])

        return ProcessRequest(
            response_identifiers=response_ids,
            s1_collection=s1_collection,
        )


@dataclass
class BatchProcessRequest:
    """The ``request`` object of a batch process task."""

    delivery: Optional[ObjectStorageInfoDto] = None
    input: TilingGridInput = field(default_factory=TilingGridInput)
    process_request: ProcessRequest = field(default_factory=ProcessRequest)

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "BatchProcessRequest":
        data = data or {}
        output = data.get("output") or {}
        delivery = None
        if output.get("delivery"):
            delivery = ObjectStorageInfoDto.from_json(output["delivery"])
        return BatchProcessRequest(
            delivery=delivery,
            input=TilingGridInput.from_json(data.get("input") or {}),
            process_request=ProcessRequest.from_json(data.get("processRequest") or {}),
        )


@dataclass
class BatchProcessTaskDto:
    """A batch process task (fields used by the metadata flow)."""

    identifier: Optional[str] = None
    status: Optional[str] = None
    request: BatchProcessRequest = field(default_factory=BatchProcessRequest)

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "BatchProcessTaskDto":
        return BatchProcessTaskDto(
            identifier=data.get("id"),
            status=data.get("status"),
            request=BatchProcessRequest.from_json(data.get("request") or {}),
        )
