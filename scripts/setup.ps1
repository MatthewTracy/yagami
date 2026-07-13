# One-shot dev setup for Windows. See README.md#quickstart.
#
# Pulls the required Ollama models (idempotent - `ollama pull` is a no-op if
# already present), creates/activates a venv, installs the Python package
# and UI deps, then runs the doctor check. Does NOT set your cloud API keys -
# run `python -m yagami.set_key ANTHROPIC_API_KEY` etc. yourself afterward.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Require-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "$name not found on PATH. $hint"
        exit 1
    }
}

Write-Host "==> Checking prerequisites"
Require-Command "python" "Install Python 3.11+ from https://www.python.org/downloads/"
Require-Command "node" "Install Node 20+ from https://nodejs.org/"
Require-Command "ollama" "Install Ollama from https://ollama.com/download/windows"

$pyVersion = (python -c "import sys; print(sys.version_info >= (3, 11))")
if ($pyVersion -ne "True") {
    Write-Error "Python 3.11+ required. Found: $(python --version)"
    exit 1
}

Write-Host "==> Pulling Ollama models (skips any already present)"
ollama pull llama3.2:3b-instruct-q4_K_M
ollama pull phi4-mini
ollama pull all-minilm

Write-Host "==> Python env"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& ".venv\Scripts\Activate.ps1"
pip install -e ".[dev]"

Write-Host "==> UI dependencies"
Push-Location ui
npm install
Pop-Location

Write-Host "==> Doctor check"
python -m yagami.doctor

Write-Host ""
Write-Host "Setup complete. Next steps:"
Write-Host "  1. Set API keys:  python -m yagami.set_key ANTHROPIC_API_KEY"
Write-Host "  2. Quick try:     cd ui; npm run build; cd ..; yagami"
Write-Host "  3. Dev mode:      yagami --reload   (and, in a second terminal) cd ui; npm run dev"
