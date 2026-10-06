"""Unit tests for the ``batchTaskId``-string input path (pytest).

The standalone tool now takes a ``batchTaskId`` string (``--batch-task-id``)
instead of a JSON task file. These tests cover:

* :meth:`Card4lProcessTask.from_batch_task_id` — the model builder.
* ``main._parse_args`` — the CLI accepts ``--batch-task-id``.
* ``main.run`` — the id flows through to ``BatchV2Client.get_task`` and to the
  task handed to ``MetadataProducer.process`` (collaborators mocked).

Run with the project venv::

    ./venv/bin/python -m pytest tests/test_batch_task_id_input.py -v
"""

from unittest import mock

import pytest

from card4l_metadata import metadata_producer_main as main
from card4l_metadata.model import Card4lProcessTask, MetadataStatus

# The concrete batchTaskId under test.
BATCH_TASK_ID = "5228284c-aa24-434d-980f-edbc3d879589"


def test_builds_task_from_id():
    task = Card4lProcessTask.from_batch_task_id(BATCH_TASK_ID)

    assert task.batch_task_id == BATCH_TASK_ID
    assert task.status == MetadataStatus.WAITING
    assert task.created is not None
    assert task.error is None


def test_parse_args_accepts_batch_task_id():
    args = main._parse_args(["--batch-task-id", BATCH_TASK_ID, "--output-dir", "out"])

    assert args.batch_task_id == BATCH_TASK_ID
    assert args.output_dir == "out"


def test_parse_args_batch_task_id_is_required():
    with pytest.raises(SystemExit):
        main._parse_args(["--output-dir", "out"])


def test_run_id_flows_to_get_task_and_producer():
    """``run`` wires the id through the (mocked) collaborators."""
    captured = {}

    batch_client = mock.Mock()

    def fake_process(task):
        captured["task"] = task
        # get_task is what actually consumes the batch_task_id.
        batch_client.get_task(task.batch_task_id)
        # A real producer always leaves a terminal status behind; run() maps it
        # to the exit code, so the stand-in has to set one too.
        task.status = MetadataStatus.DONE

    producer = mock.Mock()
    producer.process.side_effect = fake_process

    with mock.patch.object(main, "BatchV2Client", return_value=batch_client), \
            mock.patch.object(main, "Card4lS3"), \
            mock.patch.object(main, "Copernicus10DemResolver"), \
            mock.patch.object(main, "MetadataProducer", return_value=producer), \
            mock.patch.object(main.Path, "mkdir"):
        rc = main.run(["--batch-task-id", BATCH_TASK_ID, "--output-dir", "out"])

    assert rc == 0
    # The task handed to the producer carries the id from the CLI.
    assert captured["task"].batch_task_id == BATCH_TASK_ID
    # And that id reaches the BatchV2 client.
    batch_client.get_task.assert_called_once_with(BATCH_TASK_ID)
    batch_client.close.assert_called_once()


def test_run_empty_id_is_rejected():
    with mock.patch.object(main, "BatchV2Client") as batch_client_cls, \
            mock.patch.object(main, "Card4lS3"), \
            mock.patch.object(main, "Copernicus10DemResolver"), \
            mock.patch.object(main, "MetadataProducer"), \
            mock.patch.object(main.Path, "mkdir"):
        rc = main.run(["--batch-task-id", "", "--output-dir", "out"])

    assert rc == 1
    batch_client_cls.assert_not_called()
