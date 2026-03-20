#!/usr/bin/env bash
uv sync --group dev        # install deps
sudo env QUADLETMAN_DB_PATH=/tmp/qm-dev.db \
  QUADLETMAN_VOLUMES_BASE=/tmp/qm-volumes \
  .venv/bin/quadletman     # run as root with dev-isolated data
uv run pytest              # run tests (not as root)