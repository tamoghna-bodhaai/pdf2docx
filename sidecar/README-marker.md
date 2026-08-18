# marker-pdf sidecar

This service keeps marker-pdf, torch, and the Surya VLM stack out of the
application's Python 3.12 environment — and out of the PDF-Extract-Kit sidecar's
environment, whose pinned Paddle and torch versions would not survive marker
being installed beside them. Model artifacts are built once at startup and a
semaphore allows one inference at a time, which is the safe setting for the
8 GB GPU this was built against.

It backs the `marker` output mode. The other modes do not use it.

## What this service does *not* do

It does not interpret marker's output. It runs marker with whatever
configuration it is handed and returns the result; the application writes that
result to disk verbatim. That is the point of the mode — the document you open
should show marker's quality, not this codebase's reading of it.

## Install

Use a separate environment. marker needs Python 3.10 or newer.

```bash
python3 -m venv .venv-marker
.venv-marker/bin/pip install -U pip wheel
.venv-marker/bin/pip install -r sidecar/requirements-marker.txt
```

## The inference server

marker 2.x runs OCR and layout through a Surya VLM served by a separate
inference process, which it auto-spawns on first use:

- **NVIDIA GPU** — vLLM, which needs Docker and the NVIDIA Container Toolkit.
  Docker is the delivery mechanism; `SURYA_INFERENCE_BACKEND=vllm`.
- **CPU / Apple Silicon** — the `llama-server` binary from llama.cpp
  (`brew install llama.cpp` on macOS); `SURYA_INFERENCE_BACKEND=llamacpp`.
- **An already-running server** — point at it with `SURYA_INFERENCE_URL` and
  nothing is spawned.

The server is **not optional for anything but pure text extraction**, and this
was verified rather than assumed: with no Docker daemon running, marker 2.0.0
fails with `SpawnError: docker run failed` on a digital, text-layer PDF, and
`{"mode": "fast"}` fails the same way. Only `{"disable_ocr": true}` avoids it,
which does text-layer extraction on CPU — that measures marker's *parsing*, not
its OCR, and drops scanned pages and formula recognition.

Verified on this machine (marker-pdf 2.0.0, surya-ocr 0.22.1, torch 2.13.0+cu130,
RTX 5060): a 4-page digital PDF converts in about 5 seconds cold and under a
second warm with `disable_ocr`, producing headings, lists, emphasis, an extracted
figure, and the `{N}` + 48-dash page separator this application splits on.

### What works on this machine

Docker Desktop is the only Docker here, and it runs in a VM with no GPU
passthrough — `docker run --gpus all` fails with *"failed to discover GPU vendor
from CDI"*, and there is no `nvidia-container-toolkit` or native Docker Engine
installed. So the **vLLM backend cannot reach the 5060 as configured**; enabling
it would mean installing Docker Engine and the NVIDIA Container Toolkit, both of
which need root.

The working configuration is llama.cpp over Vulkan, which needs neither root nor
CUDA containers — the NVIDIA Vulkan ICD is already present:

```bash
# A prebuilt Vulkan build; llama-server then sees the 5060 as Vulkan0.
curl -sL -o llama.tar.gz \
  https://github.com/ggml-org/llama.cpp/releases/download/b10456/llama-b10456-bin-ubuntu-vulkan-x64.tar.gz
mkdir -p vendor/llama && tar xzf llama.tar.gz -C vendor/llama
```

Two settings matter here. `LD_LIBRARY_PATH` is required — `llama-server` will not
find its own shared libraries without it.

`SURYA_GUIDED_LAYOUT=false` is strongly advised. With guided layout on, surya
sends a GBNF grammar that this llama.cpp build rejects
(`Failed to initialize samplers: failed to parse grammar`), and every page logs
*"Layout inference failed … leaving page empty"*. Measured rather than assumed:
that does **not** empty the document — marker carries on without the layout
guidance and text recognition still runs, so a 3-page scan produced the same
3996 characters either way, differing only in figure-block numbering. What you
lose is a dozen wasted inference calls per document, a noisy log, and the
structural read the guided pass was there to provide. Turning it off skips the
calls that were never going to succeed.

## Run

```bash
export MARKER_GPU_CONCURRENCY=1
export SURYA_INFERENCE_BACKEND=llamacpp        # vllm needs GPU-capable Docker
export SURYA_GUIDED_LAYOUT=false               # see above — otherwise pages come back empty
export SURYA_GUIDED_TABLE_REC=false
export LLAMA_CPP_BINARY="$PWD/vendor/llama/llama-b10456/llama-server"
export LD_LIBRARY_PATH="$PWD/vendor/llama/llama-b10456"
.venv-marker/bin/python -m uvicorn marker_service:app \
  --app-dir sidecar --host 127.0.0.1 --port 8011
```

The surya GGUF weights download from Hugging Face on the first OCR request, not
at startup, so the first scanned page is much slower than the rest. Set
`HF_TOKEN` to avoid the unauthenticated rate limit.

**Keep it bound to `127.0.0.1`.** The config is a passthrough (see below), so
this service will run whatever marker configuration it is given. It is not
built to face a network.

Verify before converting anything — the first start also downloads model
weights, so it is not instant:

```bash
curl --fail http://127.0.0.1:8011/health
# {"status":"ready","device":"cuda:0","gpu_concurrency":1,"marker_version":"2.0.0"}
```

A `503` carries the import or model error instead.

### One GPU, two sidecars

The PDF-Extract-Kit sidecar and marker's Surya server will not both sit
comfortably on an 8 GB card. Run one at a time, or give marker
`TORCH_DEVICE=cpu` and accept that it will be much slower.

## HTTP contract

- `GET /health`
- `POST /convert-document` — multipart `file` (the PDF) and `config` (a JSON
  object). Returns:

  ```json
  {
    "format": "markdown",
    "content": "…",
    "images": {"_page_0_Figure_1.jpeg": "<base64>"},
    "metadata": {…},
    "config": {…}
  }
  ```

  `config` in the response is the effective merged configuration, so a finished
  job can state exactly what marker was asked to do.

### The config passthrough

The `config` field is handed to marker's own `ConfigParser` — the same object
the `marker` CLI builds its flags into — merged over two service defaults:
`output_format: "markdown"` and `paginate_output: true`. A request may override
either.

Nothing is filtered. Every CLI option is therefore reachable from the
application through `PDF2DOCX_MARKER_OPTIONS`, including ones added upstream
after this was written:

```bash
PDF2DOCX_MARKER_OPTIONS='{"mode":"fast","force_ocr":true,"format_lines":true}'
```

`paginate_output` is defaulted on because the application gives each source page
its own Word page, and marker's separator is the only record of where pages end.
Turning it off is allowed; the document then arrives as a single page.

`use_llm` is reachable the same way, but marker's LLM calls are billed by
whichever provider **this service** is configured against, are made outside the
application's accounting, and will not appear in the job's cost. Configure its
credentials in this service's environment, and treat the reported cost of a
`use_llm` conversion as zero-because-unknown rather than as free.

## Licensing

marker-pdf's code is Apache-2.0. Its **model weights are not**: they carry a
modified AI Pubs Open Rail-M licence with a revenue-based restriction on
commercial use. That is a different obligation from the AGPL one attaching to
the PDF-Extract-Kit sidecar, and it attaches to the weights this service
downloads on first run. Obtain legal review before relying on this mode
commercially.
