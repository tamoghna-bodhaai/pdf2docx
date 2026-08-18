# PDF-Extract-Kit sidecar (Python 3.10)

This service keeps PDF-Extract-Kit and its CUDA/Paddle/PyTorch dependency stack
out of the Python 3.12 application environment. It loads DocLayout-YOLO,
PaddleOCR, the formula detector, and UniMERNet once at startup. A semaphore
allows only one inference request at a time by default, which is the safe setting
for the target 8 GB GPU.

## Install

Use a separate Python 3.10 environment. The installed upstream revision is pinned to
`fdb25fd4bd9058ba4e13ac16cb68d4f06b23df56`.

```bash
python3.10 -m venv .venv-extract-kit
.venv-extract-kit/bin/pip install -U pip wheel

git clone https://github.com/opendatalab/PDF-Extract-Kit.git vendor/PDF-Extract-Kit
git -C vendor/PDF-Extract-Kit checkout fdb25fd4bd9058ba4e13ac16cb68d4f06b23df56

# Choose exactly one upstream dependency set. For the GPU build, install the
# PaddlePaddle wheel matching the machine's CUDA version if pip's default does
# not match it.
.venv-extract-kit/bin/pip install -r vendor/PDF-Extract-Kit/requirements.txt
# CPU alternative:
# .venv-extract-kit/bin/pip install -r vendor/PDF-Extract-Kit/requirements-cpu.txt

.venv-extract-kit/bin/pip install -e vendor/PDF-Extract-Kit
.venv-extract-kit/bin/pip install -r sidecar/requirements-service.txt
```

The small `requirements-pdf-extract-kit.txt` file is an alternative direct VCS
install. Cloning explicitly is preferable in deployments because it makes the
corresponding AGPL source easy to retain and publish.

## Download models

Download the upstream snapshot; it contains the paths used by the service's
default configuration.

```bash
.venv-extract-kit/bin/pip install huggingface_hub
.venv-extract-kit/bin/huggingface-cli download \
  opendatalab/pdf-extract-kit-1.0 \
  --local-dir models/pdf-extract-kit-1.0
```

Set the roots and start the service:

```bash
export PDF_EXTRACT_KIT_ROOT="$PWD/vendor/PDF-Extract-Kit"
export PDF_EXTRACT_KIT_MODEL_ROOT="$PWD/models/pdf-extract-kit-1.0"
export PDF_EXTRACT_KIT_GPU_CONCURRENCY=1
.venv-extract-kit/bin/python -m uvicorn service:app \
  --app-dir sidecar --host 127.0.0.1 --port 8010
```

Verify all models loaded before starting conversions:

```bash
curl --fail http://127.0.0.1:8010/health
# {"status":"ready","device":"cuda:0","gpu_concurrency":1}
```

A `503` health response includes the model/import error. Model paths can be
overridden individually with `PDF_EXTRACT_KIT_LAYOUT_MODEL`,
`PDF_EXTRACT_KIT_FORMULA_DETECTION_MODEL`,
`PDF_EXTRACT_KIT_UNIMERNET_MODEL`, `PDF_EXTRACT_KIT_OCR_DET_MODEL`, and
`PDF_EXTRACT_KIT_OCR_REC_MODEL`. Set `PDF_EXTRACT_KIT_DEVICE=cpu` for CPU layout
inference; install the upstream CPU requirements as shown above.

## HTTP contract

- `GET /health`
- `POST /extract-page`, multipart field `image`: returns pixel `width`, `height`,
  and blocks containing `type`, pixel `bbox`, `text`/`latex`, and `confidence`.
- `POST /recognize-formulas`, repeated multipart field `images`: returns
  `{"latex": [...]}` in input order.

The application rejects malformed/out-of-page results. A hybrid conversion falls
back to vision for service errors, low OCR confidence, empty output, ambiguous
reading order, and invalid formula output.

## AGPL-3.0 compliance

PDF-Extract-Kit is licensed under AGPL-3.0. If this sidecar is distributed or
made available over a network, preserve its license notices and provide users
with the complete corresponding source for the exact upstream revision and this
sidecar, including build/install scripts and modifications. Keep the checked-out
upstream tree (including `LICENSE.md`) with release artifacts, and publish the
source at the same location from which the service is offered. Obtain legal
review for the deployment's specific distribution and network-use obligations.
