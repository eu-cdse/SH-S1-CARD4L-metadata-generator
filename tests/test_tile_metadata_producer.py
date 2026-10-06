"""End-to-end test of :class:`TileMetadataProducer`.

Parameterized over the ``grid2`` / ``grid3`` fixture sets: runs the producer
against mocked S3 and compares the generated STAC JSON and CARD4L XML with the
expected documents, ignoring differences that carry no meaning:

* ``<ProcessingTime>`` / ``odc:processing_datetime`` are ignored in the diff —
  only their *consistency* between XML and JSON is asserted;
* the CRS WKT body is blanked and only its ``ID["EPSG",n]`` compared (the exact
  WKT text depends on the GDAL version);
* JSON lists are compared order-insensitively, except under ``proj:``/``bbox``
  which are compared positionally;
* polygon rings are normalized and floats compared with a tolerance.

S1 source data
--------------
The manifests and annotations otherwise come from the requester-pays
``sentinel-s1-l1c`` bucket:

* Put fixtures under ``tests/resources/<grid>/s1/<productId>/`` —
  ``manifest.safe`` plus ``annotation/*.xml`` — and the test runs offline.
* Otherwise set ``CARD4L_S1_LIVE=1`` (needs AWS credentials + region) to read
  from the real bucket.
* Otherwise the test skips.

Set ``REGEN_GOLDENS=1`` to overwrite the expected documents with the current
output, after reviewing a diff.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path

import pytest
from lxml import etree
from shapely import normalize, to_wkt
from shapely import wkt as shapely_wkt
from shapely.geometry import shape

from card4l_metadata.copernicus_dem import Copernicus10DemResolver
from card4l_metadata.dto import BatchProcessTaskDto, TilingGridDescriptorDto
from card4l_metadata.model import Card4lProcessTask, GridTile
from card4l_metadata.tile_producer import TileMetadataProducer

from .local_s3 import LocalOutputS3, LocalS1S3

RESOURCES = Path(__file__).parent / "resources"

GRIDS = ["grid2", "grid3"]

XML_PROCESSING_TIME_RE = re.compile(r"<ProcessingTime>([^<]+)</ProcessingTime>")
XML_CRS_RE = re.compile(
    r'<CoordinateReferenceSystem type="WKT">(.*)</CoordinateReferenceSystem>', re.S
)
XML_CRS_EPSG_ID_RE = re.compile(r'\n {4}ID\["EPSG",(\d+)]')
JSON_PROCESSING_TIME_FIELD = "odc:processing_datetime"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _mock_dem_resolver():
    """The producer needs an initialized resolver; use the bundled coverage."""
    Copernicus10DemResolver.initialize()


def _s1_source(grid_dir: Path):
    """Local S1 fixtures when complete, else the live bucket, else skip."""
    userdata = _load(grid_dir / "userdata.json")
    product_ids = [
        t["dataPath"].rstrip("/").split("/")[-1] for t in userdata.get("tiles", [])
    ]

    local = LocalS1S3(grid_dir)
    if local.has_fixtures_for(product_ids):
        return local

    if os.environ.get("CARD4L_S1_LIVE") == "1":
        from card4l_metadata.s3 import Card4lS3

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        return Card4lS3(region=region)

    pytest.skip(
        "S1 source data unavailable: add fixtures under %s/s1/<productId>/ "
        "(manifest.safe + annotation/*.xml) or set CARD4L_S1_LIVE=1 with AWS "
        "credentials." % grid_dir
    )


@pytest.mark.parametrize("grid", GRIDS)
def test_create_tile_metadata(grid, tmp_path):
    grid_dir = RESOURCES / grid
    if not grid_dir.is_dir():
        pytest.skip("missing fixtures: %s" % grid_dir)

    s1_s3 = _s1_source(grid_dir)
    s3 = LocalOutputS3(grid_dir)

    batch_task = BatchProcessTaskDto.from_json(_load(grid_dir / "test_task.json"))
    card4l_task = Card4lProcessTask.from_batch_task_id(batch_task.identifier)

    tile_json = _load(grid_dir / "test_tile.json")
    tile = GridTile(name=tile_json["name"], geometry=tile_json.get("geometry"))
    grid_descriptor = TilingGridDescriptorDto.from_json(_load(grid_dir / "tiling_grid.json"))

    output_dir = tmp_path / "out"
    TileMetadataProducer(
        card4l_task, batch_task, tile, grid_descriptor, s3, output_dir, s1data_s3=s1_s3
    ).generate_metadata()

    tile_out = output_dir / tile.name
    xml_actual = (tile_out / "metadata.xml").read_text(encoding="utf-8")
    json_actual = (tile_out / "metadata.json").read_text(encoding="utf-8")

    # Sanity thresholds (also enforced inside the producer).
    assert len(json_actual) >= 1000, "suspiciously short JSON"
    assert len(xml_actual) >= 4000, "suspiciously short XML"
    _assert_processing_times_consistent(xml_actual, json_actual)

    golden_xml = grid_dir / "metadata.xml"
    golden_json = grid_dir / "metadata.json"
    if os.environ.get("REGEN_GOLDENS") == "1":
        golden_xml.write_text(xml_actual, encoding="utf-8")
        golden_json.write_text(json_actual, encoding="utf-8")
        pytest.skip("regenerated goldens for %s; re-run to assert against them" % grid)
    if not (golden_xml.is_file() and golden_json.is_file()):
        pytest.skip("missing expected metadata for %s" % grid)

    _assert_xml_equals(golden_xml.read_text(encoding="utf-8"), xml_actual)
    _assert_json_equals(golden_json.read_text(encoding="utf-8"), json_actual)


# --------------------------------------------------------------------------- #
# XML comparison
# --------------------------------------------------------------------------- #
def _assert_xml_equals(expected: str, actual: str):
    assert _canonical_xml(expected) == _canonical_xml(actual)
    assert _crs_id(expected) == _crs_id(actual)


def _canonical_xml(xml: str) -> str:
    """Structural form of the document, ignoring serializer-only differences.

    The XML declaration, indentation and attribute order can differ without any
    change in content, so compare the parsed tree rather than raw strings.
    Geometry text is normalized (see :func:`_normalize_wkt`).
    """
    xml = _remove_non_constant(xml)
    root = etree.fromstring(
        xml.encode("utf-8"), etree.XMLParser(remove_blank_text=True)
    )
    for el in root.iter():
        if len(el.attrib) > 1:  # attribute order is not significant in XML
            items = sorted(el.attrib.items())
            for key, _ in items:
                del el.attrib[key]
            for key, value in items:
                el.set(key, value)
        if el.text:
            el.text = _normalize_wkt(el.text) if "POLYGON" in el.text else el.text.strip()
        if el.tail:
            el.tail = el.tail.strip() or None
    return etree.tostring(root, pretty_print=True).decode()


def _normalize_wkt(text: str) -> str:
    """Canonicalize a WKT polygon: ring start/orientation and coordinate precision.

    The same ring can be emitted starting at a different vertex and differ in
    the last few floating-point digits. Both are normalized away; a genuinely
    different geometry still fails.
    """
    try:
        return to_wkt(normalize(shapely_wkt.loads(text.strip())), rounding_precision=6)
    except Exception:
        return text.strip()


def _remove_non_constant(xml: str) -> str:
    xml = XML_PROCESSING_TIME_RE.sub("", xml, count=1)
    return XML_CRS_RE.sub(
        '<CoordinateReferenceSystem type="WKT"></CoordinateReferenceSystem>', xml, count=1
    )


def _crs_id(xml: str):
    m = XML_CRS_RE.search(xml)
    if m:
        id_m = XML_CRS_EPSG_ID_RE.search(m.group(1))
        if id_m:
            return id_m.group().strip()
    return None


# --------------------------------------------------------------------------- #
# JSON comparison
# --------------------------------------------------------------------------- #
def _assert_json_equals(expected_str: str, actual_str: str):
    diff = _first_difference(json.loads(expected_str), json.loads(actual_str), "")
    assert diff is None, "JSON differs at %s" % diff


def _first_difference(expected, actual, path):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return path + ".type"
        if set(expected.keys()) != set(actual.keys()):
            return path + ".keys"
        # GeoJSON geometry: compare shapes, not coordinate lists (a ring may
        # start at a different vertex and differ in the last digits).
        if "coordinates" in expected and "type" in expected:
            return None if _geometries_equal(expected, actual) else path
        for key in expected:
            if key == JSON_PROCESSING_TIME_FIELD:
                continue
            diff = _first_difference(expected[key], actual[key], path + "/" + key)
            if diff is not None:
                return diff
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return path + ".type"
        if len(expected) != len(actual):
            return path + ".length"
        if "proj:" in path or "bbox" in path:
            # Compared positionally, but with a tolerance: the last few float
            # digits can differ (see _floats_equal).
            for i, (e, a) in enumerate(zip(expected, actual)):
                if _first_difference(e, a, "%s[%d]" % (path, i)) is not None:
                    return "%s[%d]" % (path, i)
            return None
        for i, expected_el in enumerate(expected):
            el_path = "%s[%d]" % (path, i)
            if not any(_first_difference(expected_el, a, el_path) is None for a in actual):
                return el_path
        return None
    if expected is None:
        return None if actual is None else path
    if isinstance(expected, float) or isinstance(actual, float):
        return None if _floats_equal(expected, actual) else path
    return None if expected == actual else path


def _floats_equal(expected, actual) -> bool:
    """Compare floats with a tolerance far below any meaningful difference.

    1e-6 degrees is ~0.1 m on the ground; the observed spread is ~3e-9. A
    genuine value error is orders of magnitude larger.
    """
    if not isinstance(expected, (int, float)) or not isinstance(actual, (int, float)):
        return False
    return math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-6)


def _geometries_equal(expected, actual, tolerance: float = 1e-6) -> bool:
    """Topological comparison of two GeoJSON geometries."""
    try:
        return normalize(shape(expected)).equals_exact(
            normalize(shape(actual)), tolerance
        )
    except Exception:
        return expected == actual


# --------------------------------------------------------------------------- #
# processing-time consistency
# --------------------------------------------------------------------------- #
def _assert_processing_times_consistent(xml_metadata: str, json_metadata: str):
    m = XML_PROCESSING_TIME_RE.search(xml_metadata)
    xml_time = m.group(1) if m else ""
    json_time = json.loads(json_metadata)["properties"][JSON_PROCESSING_TIME_FIELD]
    assert _parse_instant(xml_time) == _parse_instant(json_time)


def _parse_instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
