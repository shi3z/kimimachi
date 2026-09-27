#!/bin/sh

/root/.local/bin/uv venv .venv && /root/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m mapgen.server --port 8891 --bind 0.0.0.0
