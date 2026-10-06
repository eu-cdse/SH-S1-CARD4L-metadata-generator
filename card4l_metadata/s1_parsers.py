"""The S1 XML parsers:
  * ``S1XmlParser``      (base: open XML from S3, XPath helpers)
  * ``S1ManifestParser`` (parse ``manifest.safe``)
  * ``S1AnnotationParser`` (parse the first annotation XML)

Uses ``lxml.etree`` for XPath. The XPath expressions are ported verbatim from the
documents. ``get_time`` appends ``Z`` when the timestamp carries no zone,
yielding a timezone-aware ``datetime``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from lxml import etree
from typing import List, Optional, Tuple

from .model import S1Annotation, S1Manifest
from .s1_constants import create_product_info

log = logging.getLogger(__name__)

_ZONELESS_TIME = re.compile(r".*T[\w.:]+$")


def _ln(name: str) -> str:
    """XPath step matching an element by local name, ignoring its namespace.

    In lxml a bare element name only matches no-namespace elements, which
    silently returns blank for the namespaced SAFE manifest elements (e.g.
    ``safe:orbitNumber``, ``s1:pass``). Matching on ``local-name()`` finds them
    whatever their namespace, without mutating the tree.
    """
    return "*[local-name()='%s']" % name


class S1XmlParser:
    """Loads an S1 XML document from S3 and evaluates XPath against it."""

    def __init__(self, xml_file_url: str, s3):
        # The S1 source bucket is requester-pays.
        data = s3.read_bytes(_bucket(xml_file_url), _key(xml_file_url), requester_pays=True)
        self._xml = etree.fromstring(data)

    # --- XPath helpers -------------------------------------------------------

    def _eval_string(self, node, path: str) -> str:
        """Equivalent of XPath ``string(...)`` evaluation."""
        result = node.xpath("string(%s)" % path)
        return result if isinstance(result, str) else str(result)

    def get_string(self, path: str) -> str:
        return self._eval_string(self._xml, path)

    def get_string_of(self, node, path: str) -> str:
        return self._eval_string(node, path)

    def get_nodes(self, path: str) -> list:
        return self._xml.xpath(path)

    def get_strings_of_subnodes(self, path: str) -> List[str]:
        nodes = self.get_nodes(path)
        return ["".join(n.itertext()) if hasattr(n, "itertext") else str(n) for n in nodes]

    def get_double(self, path: str) -> Optional[float]:
        return self.get_double_of(self._xml, path)

    def get_double_of(self, node, path: str) -> Optional[float]:
        try:
            return float(self._eval_string(node, path))
        except (ValueError, TypeError):
            log.warning("Invalid double value in XML at %s: %s", path, self.get_string(path))
            return None

    def get_integer(self, path: str) -> Optional[int]:
        try:
            return int(self.get_string(path))
        except (ValueError, TypeError):
            log.warning("Invalid integer value in XML at %s: %s", path, self.get_string(path))
            return None

    def get_time(self, path: str) -> Optional[datetime]:
        time_str = self.get_string(path)
        if not time_str:
            return None
        if _ZONELESS_TIME.match(time_str):
            time_str += "Z"
        return datetime.fromisoformat(time_str.replace("Z", "+00:00"))


class S1ManifestParser(S1XmlParser):
    """Parses an S1 ``manifest.safe``."""

    def __init__(self, aws_product_url: str, s3):
        super().__init__(aws_product_url + "/manifest.safe", s3)
        self._raw_product_id = aws_product_url[aws_product_url.rfind("/") + 1:]

    def parse(self) -> S1Manifest:
        polarizations = " ".join(
            self.get_strings_of_subnodes(
                ".//*[@ID='generalProductInformation']/*/*/*/%s"
                % _ln("transmitterReceiverPolarisation")
            )
        )
        return S1Manifest(
            raw_product_id=self._raw_product_id,
            product_info=create_product_info(self._raw_product_id),
            start_time=self.get_time(
                ".//*[@ID='acquisitionPeriod']/*/*/*/%s" % _ln("startTime")
            ),
            stop_time=self.get_time(
                ".//*[@ID='acquisitionPeriod']/*/*/*/%s" % _ln("stopTime")
            ),
            observation_mode=self.get_string(
                ".//*[@ID='platform']/*/*/*/%s/%s/%s/%s"
                % (_ln("instrument"), _ln("extension"), _ln("instrumentMode"), _ln("mode"))
            ),
            polarizations=polarizations,
            pass_direction=self.get_string(
                ".//*[@ID='measurementOrbitReference']/*/*/*/*/*/%s" % _ln("pass")
            ),
            orbit_file=self.get_string(
                ".//%s[contains(@name,'.EOF') and contains(@role,'AUX_')]/@name"
                % _ln("resource")
            ),
            orbit_file_role=self.get_string(
                ".//%s[contains(@name,'.EOF') and contains(@role,'AUX_')]/@role"
                % _ln("resource")
            ),
            facility=self.get_string(
                ".//*[@name='GRD Post Processing']/%s/@name" % _ln("facility")
            ),
            processing_date=self.get_time(".//*[@name='GRD Post Processing']/@start"),
            software_name=self.get_string(
                ".//*[@name='GRD Post Processing']/*/%s/@name" % _ln("software")
            ),
            software_version=self.get_string(
                ".//*[@name='GRD Post Processing']/*/%s/@version" % _ln("software")
            ),
            absolute_orbit=self.get_integer(
                ".//*[@ID='measurementOrbitReference']/*/*/*/%s" % _ln("orbitNumber")
            ),
            relative_orbit_number=self.get_integer(
                ".//*[@ID='measurementOrbitReference']/*/*/*/%s"
                % _ln("relativeOrbitNumber")
            ),
        )


class S1AnnotationParser(S1XmlParser):
    """Parses the first annotation XML of a product."""

    def __init__(self, aws_product_url: str, s3):
        xml_urls = s3.list_xml_files_as_urls(aws_product_url + "/annotation/")
        super().__init__(xml_urls[0], s3)

    def parse(self) -> S1Annotation:
        incidence_angles: List[float] = []
        for angles_str in self.get_strings_of_subnodes(".//incidenceAngle"):
            for token in angles_str.split(" "):
                if token:
                    incidence_angles.append(float(token))

        range_bw, azimuth_bw = self._get_look_bandwidths()

        return S1Annotation(
            platform_heading=self.get_double(".//platformHeading"),
            range_no_of_looks=self.get_integer(".//rangeProcessing/numberOfLooks"),
            azimuth_no_of_looks=self.get_integer(".//azimuthProcessing/numberOfLooks"),
            range_pixel_spacing=self.get_double(".//rangePixelSpacing"),
            azimuth_pixel_spacing=self.get_double(".//azimuthPixelSpacing"),
            range_look_bandwidths=range_bw,
            azimuth_look_bandwidths=azimuth_bw,
            incidence_angle_min=min(incidence_angles) if incidence_angles else None,
            incidence_angle_max=max(incidence_angles) if incidence_angles else None,
        )

    def _get_look_bandwidths(self) -> Tuple[str, str]:
        range_bws: List[str] = []
        azimuth_bws: List[str] = []
        nodes = self.get_nodes(".//swathProcParamsList/swathProcParams")
        for node in nodes:
            swath = self.get_string_of(node, "./swath")
            range_bws.append(
                "beam %s: %s" % (swath, self.get_double_of(node, "./rangeProcessing/lookBandwidth"))
            )
            azimuth_bws.append(
                "beam %s: %s" % (swath, self.get_double_of(node, "./azimuthProcessing/lookBandwidth"))
            )
        return (", ".join(range_bws), ", ".join(azimuth_bws))


def _bucket(s3_url: str) -> str:
    return s3_url[len("s3://"):].split("/", 1)[0]


def _key(s3_url: str) -> str:
    return s3_url[len("s3://"):].split("/", 1)[1]
