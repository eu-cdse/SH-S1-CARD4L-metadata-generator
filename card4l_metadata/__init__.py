"""CARD4L S1 NRB metadata producer.

Given a :class:`~card4l_metadata.model.Card4lProcessTask` (built from a
``batchTaskId`` string) it fetches the batch task + tiling grid from the Batch
REST service, reads the per-task execution database and feature manifest from S3,
and produces the CARD4L STAC JSON + XML metadata for each processed tile, writing
the results to a local output directory.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
