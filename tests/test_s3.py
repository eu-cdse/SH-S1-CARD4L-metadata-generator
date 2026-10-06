"""Tests for how :class:`card4l_metadata.s3.Card4lS3` builds its boto3 client.

The S1 source data lives in the requester-pays ``sentinel-s1-l1c`` bucket, which
may need different credentials than the delivery bucket. ``aws_profile`` (fed by
``S1_DATA_AWS_PROFILE``) selects an AWS profile for that client; without it the
standard boto3 credential chain applies.

boto3 is stubbed out so the tests never touch the developer's ``~/.aws``.
"""

from __future__ import annotations

import pytest

from card4l_metadata import s3 as s3mod
from card4l_metadata.s3 import Card4lS3, create_url


@pytest.fixture
def boto_calls(monkeypatch):
    """Record how a client was built, without contacting AWS."""
    calls = {}

    class _Session:
        def __init__(self, profile_name=None):
            calls["profile"] = profile_name

        def client(self, service, **kwargs):
            calls["via"] = "session"
            calls["service"] = service
            calls["kwargs"] = kwargs
            return "session-client"

    def _client(service, **kwargs):
        calls["via"] = "default"
        calls["service"] = service
        calls["kwargs"] = kwargs
        return "default-client"

    monkeypatch.setattr(s3mod.boto3, "Session", _Session)
    monkeypatch.setattr(s3mod.boto3, "client", _client)
    return calls


def test_profile_builds_a_session_client(boto_calls):
    Card4lS3(region="eu-central-1", aws_profile="s1-reader")

    assert boto_calls["via"] == "session"
    assert boto_calls["profile"] == "s1-reader"
    assert boto_calls["service"] == "s3"
    assert boto_calls["kwargs"] == {"region_name": "eu-central-1"}


def test_no_profile_uses_the_default_credential_chain(boto_calls):
    Card4lS3(region="eu-central-1")

    assert boto_calls["via"] == "default"
    assert "profile" not in boto_calls
    assert boto_calls["kwargs"] == {"region_name": "eu-central-1"}


def test_endpoint_is_forwarded_with_and_without_a_profile(boto_calls):
    Card4lS3(region="eu-west-1", endpoint="https://s3.example", aws_profile="p")
    assert boto_calls["kwargs"] == {
        "region_name": "eu-west-1",
        "endpoint_url": "https://s3.example",
    }

    Card4lS3(region="eu-west-1", endpoint="https://s3.example")
    assert boto_calls["kwargs"] == {
        "region_name": "eu-west-1",
        "endpoint_url": "https://s3.example",
    }


def test_unset_region_and_endpoint_are_omitted(boto_calls):
    """Empty values must not be passed to boto3 as explicit None/''."""
    Card4lS3()
    assert boto_calls["kwargs"] == {}


def test_explicit_client_is_used_as_is(monkeypatch):
    def _fail(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("boto3 should not be called when a client is given")

    monkeypatch.setattr(s3mod.boto3, "client", _fail)
    monkeypatch.setattr(s3mod.boto3, "Session", _fail)

    s3 = Card4lS3(s3_client="injected")
    assert s3._s3 == "injected"


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #
def test_create_url():
    assert create_url("bucket", "a/b.tif") == "s3://bucket/a/b.tif"


def test_bad_url_is_rejected():
    with pytest.raises(ValueError, match="Invalid S3 URL"):
        s3mod._get_url_part("https://example.com/x", "bucket")
