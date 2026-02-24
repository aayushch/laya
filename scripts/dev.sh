#!/usr/bin/env bash
# Start all Laya services for development
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    echo ""
    echo "Shutting down..."
    # Kill background processes
    [ -n "${ENGINE_PID:-}" ] && kill "$ENGINE_PID" 2>/dev/null && echo "  Engine stopped"
    # Stop n8n
    docker compose -f "$REPO_ROOT/docker-compose.yml" down --timeout 5 2>/dev/null && echo "  n8n stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "=== Laya Dev ==="

# Start n8n
echo "Starting n8n..."
docker compose -f "$REPO_ROOT/docker-compose.yml" up -d
echo "  n8n running on http://localhost:5678"

# Start Python engine
echo "Starting engine..."
cd "$REPO_ROOT/engine"
source .venv/bin/activate
python -m laya.main &
ENGINE_PID=$!
echo "  Engine running on http://127.0.0.1:8420 (PID: $ENGINE_PID)"

# Wait for engine health
echo "Waiting for engine..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8420/health >/dev/null 2>&1; then
        echo "  Engine ready!"
        break
    fi
    sleep 1
done

# Start Tauri dev
echo "Starting Tauri dev server..."
cd "$REPO_ROOT/ui"
npx tauri dev

# If Tauri exits, clean up
cleanup
