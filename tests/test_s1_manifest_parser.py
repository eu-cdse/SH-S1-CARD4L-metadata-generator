"""Unit tests for :class:`S1ManifestParser` against a real ``manifest.safe``.

Regression test: the SAFE manifest elements are namespaced (``safe:``, ``s1:``,
``s1sarl1:``). The XPaths referred to them by bare name, which in lxml
matches only no-namespace elements, so ``orbitNumber`` / ``relativeOrbitNumber``
(and other namespaced fields) came back blank. The parser now matches terminal
named steps by ``local-name()``.
"""

from pathlib import Path

import pytest

from card4l_metadata.s1_parsers import S1ManifestParser

_RESOURCE_DIR = Path(__file__).parent / "resources"
_MANIFEST = _RESOURCE_DIR / "manifest.safe"


class _LocalFileS3:
    """Minimal ``Card4lS3`` stand-in that serves the manifest from disk."""

    def __init__(self, manifest_path: Path):
        self._data = manifest_path.read_bytes()

    def read_bytes(self, bucket, key, requester_pays=False, num_bytes=None):
        # The parser only reads the single manifest.safe for these tests.
        return self._data


@pytest.fixture
def manifest():
    if not _MANIFEST.is_file():
        pytest.skip("manifest resource not available: %s" % _MANIFEST)
    # The product url is only used to derive the raw product id (which must match
    # the S1 naming pattern) and to build the ``/manifest.safe`` key; the fake S3
    # ignores the key and returns the file.
    product = "S1C_IW_GRDH_1SDV_20260806T052025_20260806T052057_008870_011985_6338"
    parser = S1ManifestParser(
        "s3://bucket/path/" + product, _LocalFileS3(_MANIFEST)
    )
    return parser.parse()


def test_absolute_orbit_number(manifest):
    assert manifest.absolute_orbit == 8870


def test_relative_orbit_number(manifest):
    assert manifest.relative_orbit_number == 22


def test_pass_direction(manifest):
    assert manifest.pass_direction == "DESCENDING"


def test_observation_mode(manifest):
    assert manifest.observation_mode == "IW"


def test_polarizations(manifest):
    assert manifest.polarizations == "VV VH"


def test_start_and_stop_time(manifest):
    assert manifest.start_time is not None
    assert manifest.stop_time is not None
    assert manifest.start_time < manifest.stop_time


def test_orbit_file(manifest):
    assert manifest.orbit_file.endswith(".EOF")
    assert manifest.orbit_file_role.startswith("AUX_")


def test_software(manifest):
    assert manifest.software_name == "Sentinel-1 IPF"
    assert manifest.software_version == "004.03"
    assert manifest.facility == "S1C-PS"
