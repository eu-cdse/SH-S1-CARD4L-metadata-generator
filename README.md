# CARD4L S1 Normalized Radar Backscatter metadata generator

A command-line tool for generating CARD4L normalized radar backscatter metadata for Sentinel-1 datatakes.
It produces CARD4L metadata (JSON and XML files) for each tile of the specified batch task that has been processed by
the Sentinel Hub Batch Processing API.

The batch task itself is created outside this tool by submitting a CARD4L-compatible request to the Sentinel Hub
Batch Processing API.

## Installation

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

`GDAL` (the `osgeo` bindings) must match the system GDAL:

```bash
pip install GDAL==$(gdal-config --version)
```

Install the following libraries if missing. All three must be the same version as the `osgeo` binding.

```bash
apt install gdal-bin libgdal-dev python3-gdal
```

## Configuration

| Environment variable    | Description                                        | Default                                        |
|-------------------------|----------------------------------------------------|------------------------------------------------|
| `BATCH_BASE_URI`        | Batch API base URI                                 | `https://services.sentinel-hub.com/batch/v2`   |
| `SH_TOKEN`              | Sentinel Hub bearer token                          |                                                |
| `AWS_ACCESS_KEY_ID`     | Access key for the Batch output bucket             |                                                |
| `AWS_SECRET_ACCESS_KEY` | Secret key for the Batch output bucket             |                                                |
| `S1_DATA_AWS_PROFILE`   | AWS profile for accessing `sentinel-s1-l1c` bucket |                                                |
| `AWS_REGION`            | AWS region                                         | `eu-central-1`                                 |
| `S3_ENDPOINT`           | S3 endpoint (optional)                             | *(None)*                                       |

`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` specify AWS credentials for accessing output bucket where Batch Processing API
delivers the processing results. The bucket is specified as `{{outputBucket}}` in the request template below.

`S1_DATA_AWS_PROFILE` var specifies an AWS profile from the AWS config/credentials files (`.aws`) to be used for reading Sentinel-1 data
in the `sentinel-s1-l1c` bucket. Note that this bucket allows public read access, but it uses a Requester Pays policy.
To download files, the requester must authenticate with AWS credentials.

# Usage guide

## Submit batch task to the Batch Processing API

Generate CARD4L-compatible Batch request using the following template:

```
{
  "processRequest": {
    "input": {
      "bounds": {
        "geometry": {{ aoiGeometry }},
        "properties": {
          "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"
        }
      },
      "data": [
        {
          "type": "S1GRD",
          "dataFilter": {
            "acquisitionMode": "IW",
            "polarization": "DV",
            "resolution": "HIGH"
          },
          "processing": {
            "backCoeff": "GAMMA0_TERRAIN",
            "orthorectify": true,
            "demInstance": "COPERNICUS",
            "downsampling": "BILINEAR",
            "upsampling": "BILINEAR"
          }
        }
      ]
    },
    "output": {
      "responses": [
        {
          "identifier": "VV",
          "format": {
            "type": "image/tiff"
          }
        },
        {
          "identifier": "VH",
          "format": {
            "type": "image/tiff"
          }
        },
        {
          "identifier": "AREA",
          "format": {
            "type": "image/tiff"
          }
        },
        {
          "identifier": "ANGLE",
          "format": {
            "type": "image/tiff"
          }
        },
        {
          "identifier": "MASK",
          "format": {
            "type": "image/tiff"
          }
        },
        {
          "identifier": "userdata",
          "format": {
            "type": "application/json"
          }
        }
      ]
    },
    "evalscript": "//VERSION=3\nfunction setup() {\n  return {\n    input: [{bands:[\"VV\", \"VH\", \"scatteringArea\", \"localIncidenceAngle\", \"shadowMask\", \"dataMask\"], metadata: [\"bounds\"]}],\n    output: [\n      {\n      id: \"VV\",\n      bands: 1,\n      sampleType: \"FLOAT32\",\n      nodataValue: NaN,\n      },{\n      id: \"VH\",\n      bands: 1,\n      sampleType: \"FLOAT32\",\n      nodataValue: NaN,\n      },{\n      id: \"AREA\",\n      bands: 1,\n      sampleType: \"FLOAT32\",\n      nodataValue: NaN,\n      },{\n      id: \"ANGLE\",\n      bands: 1,\n      sampleType: \"UINT8\",\n      nodataValue: 255\n      },{\n      id: \"MASK\",\n      bands: 1,\n      sampleType: \"UINT8\",\n      nodataValue: 0,\n      }\n    ]\n  };\n}\n\nfunction evaluatePixel(samples) {\n  return {\n    VV: [samples.VV],\n    VH: [samples.VH],\n    AREA: [samples.scatteringArea],\n    ANGLE: [samples.dataMask == 0 ? 255 : samples.localIncidenceAngle],\n    MASK: [samples.shadowMask == 1 ? 2 : samples.dataMask]\n  };\n}\n\nfunction updateOutputMetadata(scenes, inputMetadata, outputMetadata) {\n  outputMetadata.userData = {\"tiles\": scenes.tiles, \"serviceVersion\": inputMetadata.serviceVersion };\n}\n\n"
  },
  "input": {
    "type": "tiling-grid",
    "id": 3,
    "resolution": 0.0002
  },
  "output": {
    "type": "raster",
    "delivery": {
      "s3": {
        "url": "s3://{{ outputBucket }}/{{ outputFolder }}/<tileName>/{{ datatakeYear }}/{{ datatakeMonth }}/{{ datatakeDay }}/{{ datatakeIdHex }}/s1_rtc_{{ datatakeIdHex }}_<tileName>_{{ datatakeYear }}_{{ datatakeMonth }}_{{ datatakeDay }}_<outputId>.<format>",
        "accessKey": "{{ outputAccessKey }}",
        "secretAccessKey": "{{ outputSecretKey }}",
        "region": "{{ outputRegion }}"
      }
    },
    "cogOutput": true,
    "cogParameters": {
      "blockxsize": 1024,
      "blockysize": 1024
    },
    "overwrite": true
  },
  "description": "CARD4L task"
}
```

Submit the batch request to the Batch Processing API as described in the [Batch Processing API docs](https://docs.planet.com/develop/apis/batch-processing)
and save the task ID which is needed for running the metadata generator tool.

```
POST https://services.sentinel-hub.com/batch/v2/process/
<batch request>
```

Start the task:
```
POST https://services.sentinel-hub.com/batch/v2/process/TASK_ID/start
```

Before continuing, make sure that the batch task processing has finished successfully (status of the task is `DONE`).
Status of the batch task can be retrieved using the following request:
```
GET https://services.sentinel-hub.com/batch/v2/process/TASK_ID
```

## Generate CARD4L metadata for the specified batch task

To produce the CARD4L metadata files, run the following command:

```bash
python3 -m card4l_metadata.metadata_producer_main [-h] --batch-task-id BATCH_TASK_ID --output-dir OUTPUT_DIR
                                                  [--log-level LOG_LEVEL]

Produce CARD4L metadata (JSON and XML files) for each tile of the specified batch task that has been
processed by the Sentinel Hub Batch Processing API.

Options:
  -h, --help            Show this help message and exit
  --batch-task-id BATCH_TASK_ID
                        Batch task id to produce CARD4L metadata for.
  --output-dir OUTPUT_DIR
                        Local directory to write the generated per-tile metadata.json and metadata.xml files.
  --log-level LOG_LEVEL
                        Logging level (default INFO).
```

Example:

```bash
python -m card4l_metadata.metadata_producer_main \
  --batch-task-id a8f29943-0de1-480d-9f65-7e418fd9f89f \
  --output-dir out
```

The command produces CARD4L metadata files `metadata.json` and `metadata.xml` for each tile corresponding to the
specified batch task. Individual tile failures do not abort the run: the task finishes with the status
`DONE`, `PARTIAL`, or `FAILED`, and the failures are logged.

The exit code reflects that status: `0` when every tile succeeded (`DONE`), and `1` otherwise (`PARTIAL` or
`FAILED`, plus a summary of the failed tiles on the log). Note that `argparse` exits with `2` on invalid
command-line arguments.

### What it reads

For the given task id the tool fetches the batch task and its tiling grid from the
Batch API, then for every processed tile reads:

- From the Batch output bucket (user provided):
  - the task's execution database and feature manifest (which tiles completed),
  - the `userdata.json` file (input scenes and service version) for each tile,
  - the GeoTIFF header (geometry, CRS, size) of the output tiff files (`VV`, `VH`, `AREA`, `ANGLE`, `MASK`)

- From the Sentinel-1 source bucket (`sentinel-s1-l1c`):
  - the `manifest.safe` and annotation files for each product (tile)

The Copernicus DEM coverage used to describe the elevation source is read
from a file bundled with the package, so no extra download is needed.

### Output

Metadata generator tool generates `metadata.json` and `metadata.xml` files for each tile. The output files structure is as follows:

```
<output-dir>/<tileName>/metadata.json   # STAC item
<output-dir>/<tileName>/metadata.xml    # CARD4L NRB XML
```

## Complete example

The `samples/S1C-72069/` directory contains a batch processing request and corresponding AOI for the Sentinel-1 datatake with an id `72069` and mission `S1C`.
The `out` subdirectory contains the generated metadata files using the following command:

```
python3 -m card4l_metadata.metadata_producer_main --batch-task-id a8f29943-0de1-480d-9f65-7e418fd9f89f --output-dir samples/S1C-72069/out
```

| File                                                      | What it is                                                       |
|-----------------------------------------------------------|------------------------------------------------------------------|
| [batch_request.json](samples/S1C-72069/batch_request.json) | Batch processing request                                         |
| [aoi.geojson](samples/S1C-72069/aoi.geojson)              | AOI                                                              |
| [out](samples/S1C-72069/out)                              | output directory containing the generated metadata for all 30 tiles |

## Tests

```bash
pip install -r requirements.txt -r requirements-test.txt
python -m pytest tests/ -v
```

## Project layout

| Module                                        | Responsibility                                    |
|-----------------------------------------------|---------------------------------------------------|
| `metadata_producer_main.py`                   | the CLI entry point                               |
| `producer.py`                                 | drives all tiles of one task                      |
| `tile_producer.py`                            | generates the metadata of a single tile           |
| `json_producer.py` / `xml_producer.py`        | the STAC JSON and CARD4L XML documents            |
| `batch_client.py`                             | Batch API client                                  |
| `execution_db.py`                             | reads the execution database and feature manifest |
| `s1_parsers.py` / `s1_constants.py`           | S1 manifest and annotation parsing                |
| `gdal_geom.py` / `tiff_header.py` / `geom.py` | raster geometry, TIFF headers, geometry ops       |
| `path_template.py`                            | delivery path templating                          |
| `s3.py` / `config.py`                         | S3 access and runtime configuration               |
| `dto.py` / `model.py`                         | request/response and domain objects               |
| `copernicus_dem.py`                           | Copernicus 10 m DEM coverage lookup               |
| `number_format.py`                            | number formatting                                 |
| `resources/`                                  | bundled data (DEM coverage WKT)                   |

## Notes

- The tool expects a task produced with a CARD4L-compatible request: the outputs
  `VV`, `VH`, `AREA`, `ANGLE`, `MASK` and `userdata`, orthorectified, delivered
  with the `<tileName>/<year>/<month>/<day>/<datatakeId>/s1_rtc_...` path template.
- Generated metadata is written to local files; nothing is uploaded to a bucket.
