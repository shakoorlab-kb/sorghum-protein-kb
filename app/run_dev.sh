#!/usr/bin/env bash
# Local development server on :8000 (Neo4j from the sintel-neo4j-dev container).
cd "$(dirname "$0")"
source dev.env
: "${OPENROUTER_API_KEY:?export OPENROUTER_API_KEY first (only needed for chat and automatic extraction)}"
export SI_PORT=8770
exec .venv/bin/python -m sorghum_intel serve
