#!/usr/bin/env bash
# One-shot dev setup for macOS / Linux. See README.md#quickstart.
#
# Pulls the required Ollama models (idempotent - `ollama pull` is a no-op if
# already present), creates/activates a venv, installs the Python package
# and UI deps, then runs the doctor check. Does NOT set your cloud API keys -
# run `python -m yagami.set_key ANTHROPIC_API_KEY` etc. yourself afterward.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "error: $1 not found on PATH. $2" >&2
        exit 1
    fi
}

echo "==> Checking prerequisites"
require_command python3 "Install Python 3.11+ (e.g. via https://www.python.org/downloads/ or your package manager)."
require_command node "Install Node 20+ from https://nodejs.org/."
require_command ollama "Install Ollama from https://ollama.com/download."

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "error: Python 3.11+ required. Found: $(python3 --version)" >&2
    exit 1
fi

echo "==> Pulling Ollama models (skips any already present)"
ollama pull llama3.2:3b-instruct-q4_K_M
ollama pull phi4-mini
ollama pull all-minilm

echo "==> Python env"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -e ".[dev]"

echo "==> UI dependencies"
(cd ui && npm install)

echo "==> Doctor check"
python -m yagami.doctor

cat <<'EOF'

Setup complete. Next steps:
  1. Set API keys:  python -m yagami.set_key ANTHROPIC_API_KEY
  2. Quick try:      (cd ui && npm run build) && yagami
  3. Dev mode:       yagami --reload   (and, in a second terminal) cd ui && npm run dev
EOF
