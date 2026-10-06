"""The token-based key templating used to locate per-tile output files on
object storage.

A delivery template must carry the tokens that distinguish one output from
another; there is no default layout to fall back on. See ``get_path_uri``.
"""

from __future__ import annotations

import re

from .dto import ObjectStorageInfoDto, ObjectStorageUri

TOKEN_TASK_ID = "<requestId>"
TOKEN_GRID_TILE_NAME = "<tileName>"
TOKEN_OUTPUT_ID = "<outputId>"
TOKEN_FORMAT = "<format>"

# Tokens a delivery template must carry for per-tile output paths to be unique:
# without <tileName> every tile resolves to the same key, without <outputId>
# every band does. <format> is not discriminating -- a template may spell the
# extension out literally.
_REQUIRED_OUTPUT_TOKENS = (TOKEN_GRID_TILE_NAME, TOKEN_OUTPUT_ID)

# Splits a delivery path into its bucket and key parts.
_PATH_PATTERN = re.compile(
    "^(s3|gs)://(?P<bucket>[\\w\\-.]*:?[\\w\\-.]{3,63})(/(?P<key>[\\u0001-\\u00ff]+))?$"
)


def _has_no_tokens(path: str) -> bool:
    return "<" not in path


def _without_trailing_slash(s: str) -> str:
    return s[:-1] if s.endswith("/") else s


def extract_bucket(tile_path: str) -> str:
    m = _PATH_PATTERN.match(tile_path)
    if not m:
        raise ValueError("Invalid tilePath")
    return m.group("bucket")


def extract_key_or_empty(tile_path: str) -> str:
    m = _PATH_PATTERN.match(tile_path)
    if not m:
        raise ValueError("Invalid tilePath")
    return m.group("key") or ""


def _replace_key_template_with_values(
    key: str, grid_tile_name: str, output_id: str, fmt: str
) -> str:
    return (
        key.replace(TOKEN_GRID_TILE_NAME, grid_tile_name)
        .replace(TOKEN_OUTPUT_ID, output_id)
        .replace(TOKEN_FORMAT, fmt)
    )


def get_path_uri(
    delivery: ObjectStorageInfoDto,
    output_id: str,
    grid_tile_name: str,
    fmt: str,
) -> ObjectStorageUri:
    """Resolve a delivery path template to a concrete URI.

    Raises ``ValueError`` if the template cannot address individual outputs.
    """
    tile_path = str(delivery.url)
    bucket = extract_bucket(tile_path)
    key = extract_key_or_empty(tile_path)

    missing = [token for token in _REQUIRED_OUTPUT_TOKENS if token not in key]
    if missing:
        raise ValueError(
            "Delivery template '%s' is missing the %s token(s). "
            "A CARD4L delivery path must carry %s."
            % (tile_path, ", ".join(missing), " and ".join(_REQUIRED_OUTPUT_TOKENS))
        )

    tile_key = _replace_key_template_with_values(key, grid_tile_name, output_id, fmt)
    return ObjectStorageUri.of(delivery.url.type, bucket, tile_key)


def get_base_path_uri(
    default_path_url: ObjectStorageUri, batch_task_id: str
) -> ObjectStorageUri:
    """The base path of a delivery, with the task-id token substituted."""
    default_path = _without_trailing_slash(str(default_path_url))
    if _has_no_tokens(default_path):
        default_path += "/" + TOKEN_TASK_ID
    default_path = default_path.replace(TOKEN_TASK_ID, batch_task_id)
    # Strip any remaining token segment.
    default_path = re.sub(r"(^|/)[^/]*<.*$", "", default_path)
    return ObjectStorageUri(default_path)


def create_file_uri(
    default_path_url: ObjectStorageUri,
    batch_task_id: str,
    file_name_prefix: str,
    extension: str,
) -> ObjectStorageUri:
    """Build the URI of a single file under a delivery base path."""
    default_path = get_base_path_uri(default_path_url, batch_task_id)
    return ObjectStorageUri(
        "%s/%s-%s.%s" % (default_path, file_name_prefix, batch_task_id, extension)
    )
