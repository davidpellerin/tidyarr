#!/usr/bin/env bash
# Create a Python 3.12 environment with the pinned dependencies in requirements.txt.
# Safe to re-run.

set -euo pipefail                       # stop on errors, unset vars, and failed pipes
cd "$(dirname "$0")/.."                 # run from the project root, wherever we're called from

# uv: a fast Python version + virtualenv + package manager. Install it if missing.
if command -v uv >/dev/null; then
  echo "==> uv already installed"
else
  echo "==> Installing uv (Python package manager)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Create .venv with Python 3.12 (the version the project is tested on).
# Skipped if it already exists, so re-runs don't prompt or rebuild.
if [ -d .venv ]; then
  echo "==> Virtual environment .venv already exists, skipping"
else
  echo "==> Creating virtual environment .venv (Python 3.12)"
  uv venv --python 3.12 .venv
fi

# The exact versions the project is tested with (requirements.in says what each package is for). Sync, not install,
# so the venv matches the lockfile exactly, including after `make lock` changes a version.
echo "==> Installing the pinned dependencies from requirements.txt"
uv pip sync --python .venv/bin/python requirements.txt

echo "==> Done. Environment is ready."
