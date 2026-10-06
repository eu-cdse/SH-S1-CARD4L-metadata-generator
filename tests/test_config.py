"""Tests for :class:`card4l_metadata.config.Config` env resolution."""

from __future__ import annotations

import pytest

from card4l_metadata.config import (
    DEFAULT_BATCH_BASE_URI,
    Config,
)

_ENV = (
    "BATCH_BASE_URI", "SH_TOKEN",
    "AWS_REGION", "AWS_DEFAULT_REGION", "S3_ENDPOINT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_sh_token_from_env(monkeypatch):
    """The Sentinel Hub token authenticates the Batch API."""
    monkeypatch.setenv("SH_TOKEN", "shared-token")
    assert Config.from_env().sh_token == "shared-token"


def test_explicit_token_overrides_env(monkeypatch):
    monkeypatch.setenv("SH_TOKEN", "from-env")
    assert Config.from_env(sh_token="explicit").sh_token == "explicit"


def test_no_token_configured():
    assert Config.from_env().sh_token is None


def test_base_uri_defaults():
    assert Config.from_env().batch_base_uri == DEFAULT_BATCH_BASE_URI


def test_base_uris_from_env(monkeypatch):
    monkeypatch.setenv("BATCH_BASE_URI", "https://batch.example/batch/v2/")
    assert Config.from_env().batch_base_uri == "https://batch.example/batch/v2/"


def test_region_falls_back_to_aws_default_region(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    assert Config.from_env().aws_region == "eu-west-1"
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    assert Config.from_env().aws_region == "eu-central-1"
