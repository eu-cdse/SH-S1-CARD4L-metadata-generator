"""Tests for the Copernicus 10m DEM resolver (coverage read from a local file)."""

from __future__ import annotations

import pytest
from shapely.geometry import box

from card4l_metadata.copernicus_dem import (
    DEFAULT_COVERAGE_FILE,
    EEA10,
    GLO30,
    MIXED,
    Copernicus10DemResolver,
)


@pytest.fixture(autouse=True)
def _reset_resolver():
    """The resolver is process-global; keep tests independent."""
    yield
    Copernicus10DemResolver._coverage = None
    Copernicus10DemResolver._coverage_geom = None


def test_bundled_coverage_file_exists():
    assert DEFAULT_COVERAGE_FILE.is_file()
    assert DEFAULT_COVERAGE_FILE.name == "copernicus-dem-10m-geometry.wkt"


def test_initialize_reads_bundled_file_without_s3():
    Copernicus10DemResolver.initialize()
    geometry = Copernicus10DemResolver._coverage_geom
    assert geometry is not None
    assert geometry.geom_type == "MultiPolygon"
    assert geometry.is_valid


def test_resolve_fully_covered_area_is_eea10():
    Copernicus10DemResolver.initialize()
    # Slovenia — inside the EEA-10 coverage.
    assert Copernicus10DemResolver.resolve(box(14.5, 46.0, 14.6, 46.1)) == EEA10


def test_resolve_uncovered_area_is_glo30():
    Copernicus10DemResolver.initialize()
    # Tanzania — outside the EEA-10 coverage.
    assert Copernicus10DemResolver.resolve(box(36.0, -7.0, 36.1, -6.9)) == GLO30


def test_resolve_partially_covered_area_is_mixed():
    Copernicus10DemResolver.initialize()
    # Adriatic coast — straddles the coverage boundary.
    assert Copernicus10DemResolver.resolve(box(12.0, 44.0, 14.0, 45.5)) == MIXED


def test_resolve_before_initialize_raises():
    with pytest.raises(RuntimeError, match="not initialized"):
        Copernicus10DemResolver.resolve(box(0, 0, 1, 1))


def test_initialize_accepts_an_explicit_file(tmp_path):
    path = tmp_path / "coverage.wkt"
    path.write_text("POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))", encoding="ascii")

    Copernicus10DemResolver.initialize(path)

    assert Copernicus10DemResolver.resolve(box(1, 1, 2, 2)) == EEA10
    assert Copernicus10DemResolver.resolve(box(20, 20, 21, 21)) == GLO30


def test_initialize_rejects_non_polygonal_wkt(tmp_path):
    path = tmp_path / "coverage.wkt"
    path.write_text("LINESTRING (0 0, 1 1)", encoding="ascii")

    with pytest.raises(ValueError, match="Expected polygonal geometry"):
        Copernicus10DemResolver.initialize(path)


def test_initialize_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        Copernicus10DemResolver.initialize(tmp_path / "nope.wkt")
