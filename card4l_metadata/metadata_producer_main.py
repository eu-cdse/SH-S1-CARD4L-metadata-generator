"""Standalone CLI entry point: produce CARD4L metadata for one batch task.

The task is identified by a ``batchTaskId`` supplied on the command line. The
Copernicus DEM resolver is initialized, the collaborators are built, and the
JSON + XML metadata of every processed tile is written to a local directory.

Usage:
    python -m card4l_metadata.metadata_producer_main --batch-task-id ID --output-dir ./out

Endpoints and credentials come from the environment: BATCH_BASE_URI, SH_TOKEN,
AWS_REGION / AWS_DEFAULT_REGION, S3_ENDPOINT (see ``config.py``), plus the
optional S1_DATA_AWS_PROFILE for the Sentinel-1 source data bucket.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .batch_client import BatchV2Client
from .config import Config
from .copernicus_dem import Copernicus10DemResolver
from .model import Card4lProcessTask, MetadataStatus
from .producer import MetadataProducer
from .s3 import Card4lS3

log = logging.getLogger("card4l_metadata")


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="card4l_metadata",
        description="Produce CARD4L metadata (JSON and XML files) for each tile of the specified batch task that has been processed by the Sentinel Hub Batch Processing API.",
    )
    parser.add_argument(
        "--batch-task-id", required=True,
        help="Batch task id to produce CARD4L metadata for.",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Local directory to write the generated per-tile metadata.json and metadata.xml files.",
    )
    parser.add_argument("--log-level", default="INFO",
                        help="Logging level (default INFO).")
    return parser.parse_args(argv)


def run(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    task = Card4lProcessTask.from_batch_task_id(args.batch_task_id)
    if not task.batch_task_id:
        log.error("A non-empty --batch-task-id is required.")
        return 1

    log.info("Created card4l task for the batch task %s", task.batch_task_id)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = Config.from_env()

    s3 = Card4lS3(region=config.aws_region, endpoint=config.s3_endpoint)
    s1data_s3 = Card4lS3(
        region=config.aws_region,
        endpoint=config.s3_endpoint,
        aws_profile=os.environ.get("S1_DATA_AWS_PROFILE"),
    )
    # The Copernicus DEM coverage comes from the bundled WKT file.
    Copernicus10DemResolver.initialize()

    batch_client = BatchV2Client(config.batch_base_uri, config.sh_token)
    try:
        producer = MetadataProducer(batch_client, s3, output_dir, s1data_s3=s1data_s3)
        producer.process(task)
    except Exception:
        log.error("Error producing metadata", exc_info=True)
        return 1
    finally:
        batch_client.close()

    log.info("Task %s finished with status %s", task.batch_task_id, task.status.value)
    if task.status is not MetadataStatus.DONE:
        # Per-tile failures are swallowed by the producer so one bad tile does
        # not abort the run; they must still make the process exit non-zero, or
        # a caller cannot tell a complete run from a failed one.
        if task.error:
            log.error("Tile failures:\n%s", task.error.rstrip("\n"))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
