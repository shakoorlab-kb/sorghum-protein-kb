#!/usr/bin/env bash
# Deploy Sorghum Protein Intelligence from a workstation to a Linux server with Docker.
#   LAB_HOST=user@server [REMOTE_DIR=apps/sorghum_intel] [REMOTE_KB_DIR=../sorghum_protein] \
#     OPENROUTER_API_KEY=... deploy/deploy.sh
#   REMOTE_KB_DIR is the KB checkout on the server, relative to REMOTE_DIR or absolute.
#
#   deploy/deploy.sh            sync code, (first time) create secrets, build, start, health check
#   deploy/deploy.sh code       sync code only
#   deploy/deploy.sh secrets    create server secrets if missing (first deploy)
#   deploy/deploy.sh restart    rebuild + restart on the server
#   deploy/deploy.sh sync-kb    index the KB library on the server and build the graph (LLM)
#   deploy/deploy.sh graph      ship claude_graph/extractions and import them into Neo4j
#   deploy/deploy.sh logs       tail the app log
#
# Data ownership: the SERVER owns ./data (uploads, chats, jobs) and the Neo4j volume.
# Code flows Mac -> server; data never does. Secrets are created on the server once
# (~/.config/sorghum-intel/, mode 600) and never copied back.
set -euo pipefail

HOST="${LAB_HOST:?set LAB_HOST=user@server}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
R_DIR="${REMOTE_DIR:-apps/sorghum_intel}"
SSH=(ssh -i "$HOME/.ssh/id_ed25519" -o BatchMode=yes -o ConnectTimeout=8)
remote() { "${SSH[@]}" -n "$HOST" "$@" </dev/null; }

sync_code() {
  echo "==> code -> $HOST:$R_DIR"
  remote "mkdir -p $R_DIR/data"
  rsync -a --compress --delete -e "${SSH[*]}" \
    --exclude .venv --exclude .devdata --exclude /data --exclude dev.env --exclude run_dev.sh \
    --exclude node_modules --exclude __pycache__ --exclude .DS_Store \
    --exclude backend/sorghum_intel/static --exclude frontend/dist --exclude /claude_graph \
    "$HERE/" "$HOST:$R_DIR/"
}

ensure_secrets() {
  if remote "test -f ~/.config/sorghum-intel/secrets.env"; then
    echo "==> secrets already on the server; leaving them untouched"
    return
  fi
  echo "==> creating server secrets (OpenRouter key from \$OPENROUTER_API_KEY)"
  local key
  key="${OPENROUTER_API_KEY:-}"
  [ -n "$key" ] || { echo "no OPENROUTER_API_KEY found" >&2; exit 1; }
  # The key travels on stdin, never on a command line.
  printf '%s' "$key" | "${SSH[@]}" "$HOST" 'set -e; umask 077; mkdir -p ~/.config/sorghum-intel; cd ~/.config/sorghum-intel
    key=$(cat); npw=$(openssl rand -hex 18); apw=$(openssl rand -base64 18 | tr -d "/+=" | cut -c1-16)
    printf "OPENROUTER_API_KEY=%s\nNEO4J_PASSWORD=%s\nSI_ADMIN_USER=admin\nSI_ADMIN_PASSWORD=%s\nSI_SESSION_SECRET=%s\n" \
      "$key" "$npw" "$apw" "$(openssl rand -hex 32)" > secrets.env
    printf "NEO4J_AUTH=neo4j/%s\n" "$npw" > neo4j.env
    chmod 600 secrets.env neo4j.env
    echo "admin password: $apw   (stored in ~/.config/sorghum-intel/secrets.env)"'
}

restart() {
  echo "==> build + start"
  remote "cd $R_DIR && SI_SECRETS_DIR=\$HOME/.config/sorghum-intel KB_DIR=${REMOTE_KB_DIR:-../sorghum_protein} docker compose up -d --build && docker image prune -f >/dev/null"
  echo "==> waiting for health"
  for _ in $(seq 1 60); do
    if remote "curl -fsS http://127.0.0.1:8770/api/health" 2>/dev/null; then echo; return; fi
    sleep 5
  done
  echo "app did not become healthy; see: deploy/deploy.sh logs" >&2
  exit 1
}

case "${1:-all}" in
  all)     sync_code; ensure_secrets; restart ;;
  code)    sync_code ;;
  secrets) ensure_secrets ;;
  restart) restart ;;
  sync-kb) remote "docker exec -d sorghum-intel python -m sorghum_intel sync"; echo "started; follow it on the Ingest page" ;;
  graph)
    remote "mkdir -p $R_DIR/data/claude_graph/extractions"
    rsync -a --compress -e "${SSH[*]}" "$HERE/claude_graph/extractions/" "$HOST:$R_DIR/data/claude_graph/extractions/"
    "${SSH[@]}" -n "$HOST" "docker exec sorghum-intel python -m sorghum_intel claude-import /data/claude_graph" </dev/null ;;
  logs)    remote "docker logs --tail 200 -f sorghum-intel" ;;
  *)       sed -n '2,12p' "$0"; exit 2 ;;
esac
