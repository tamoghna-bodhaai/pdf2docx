#!/usr/bin/env bash
# Start the marker-pdf sidecar with the backend settings that actually work on
# this machine. See sidecar/README-marker.md for why each one is here; the short
# version is that vLLM needs GPU-capable Docker (this box has Docker Desktop in a
# VM with no passthrough), and surya's guided decoding sends a grammar the
# prebuilt llama.cpp build rejects, so those calls fail on every page and are
# better not made.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAMA_DIR="${LLAMA_DIR:-$ROOT/vendor/llama/llama-b10456}"

if [[ ! -x "$LLAMA_DIR/llama-server" ]]; then
  echo "llama-server not found at $LLAMA_DIR." >&2
  echo "Fetch a prebuilt Vulkan build as shown in sidecar/README-marker.md." >&2
  exit 1
fi

export SURYA_INFERENCE_BACKEND="${SURYA_INFERENCE_BACKEND:-llamacpp}"
export SURYA_GUIDED_LAYOUT="${SURYA_GUIDED_LAYOUT:-false}"   # grammar unsupported by this build
export SURYA_GUIDED_TABLE_REC="${SURYA_GUIDED_TABLE_REC:-false}"
export LLAMA_CPP_BINARY="$LLAMA_DIR/llama-server"
export LD_LIBRARY_PATH="$LLAMA_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MARKER_GPU_CONCURRENCY="${MARKER_GPU_CONCURRENCY:-1}"

exec "$ROOT/.venv-marker/bin/python" -m uvicorn marker_service:app \
  --app-dir "$ROOT/sidecar" --host 127.0.0.1 --port "${MARKER_PORT:-8011}"
