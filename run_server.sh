#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "No .venv found. Run ./setup_venv.sh first." >&2
  exit 1
fi

source .venv/bin/activate
python -m app.server
