"""Produces the metadata of every processed tile of one task.

The task is held in memory: its status is set to DONE, PARTIAL or FAILED
according to how many tiles succeeded, and nothing is written back to a
datasource.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .execution_db import ExecutionDbReader
from .model import Card4lProcessTask, MetadataStatus
from .tile_producer import TileMetadataProducer

log = logging.getLogger(__name__)


class MetadataProducer:
    """Runs the metadata production for a single task."""

    def __init__(self, batch_client, s3, output_dir: Path, s1data_s3=None):
        self._batch_client = batch_client
        self._s3 = s3
        self._execution_db_reader = ExecutionDbReader(s3)
        self._output_dir = output_dir
        self._s1data_s3 = s1data_s3 or s3

    def process(self, task: Card4lProcessTask) -> None:
        """Generate the metadata of every processed tile of ``task``."""
        log.info("Processing card4l task %s", task.batch_task_id)
        failed_count = 0
        success_count = 0
        errors = []

        batch_task = self._batch_client.get_task(task.batch_task_id)
        tiling_grid_input = batch_task.request.input
        grid = self._batch_client.get_grid_descriptor(tiling_grid_input.id)
        processed_tiles = self._execution_db_reader.get_processed_tiles(batch_task)

        for tile in processed_tiles:
            log.info("Processing tile %s", tile.name)
            if tile.name is None or tile.geometry is None:
                failed_count += 1
                continue
            try:
                TileMetadataProducer(
                    task,
                    batch_task,
                    tile,
                    grid,
                    self._s3,
                    self._output_dir,
                    s1data_s3=self._s1data_s3,
                ).generate_metadata()
                success_count += 1
            except Exception as ex:
                log.error(
                    "Error generating metadata for tile %s of task %s",
                    tile.name,
                    task.batch_task_id,
                    exc_info=True,
                )
                errors.append("Tile %s: %s" % (tile.name, ex))
                failed_count += 1

        log.info(
            "Done card4l task %s, succeeded %d tiles, failed %d",
            task.batch_task_id,
            success_count,
            failed_count,
        )
        if failed_count == 0:
            task.status = MetadataStatus.DONE
        else:
            task.error = "\n".join(errors) + "\n" if errors else task.error
            task.status = (
                MetadataStatus.FAILED if success_count == 0 else MetadataStatus.PARTIAL
            )
