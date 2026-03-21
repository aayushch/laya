#!/usr/bin/env bash
# Start all Laya services for development.
#
# n8n lifecycle is managed by the Tauri app (auto-start on launch,
# auto-stop on quit) as a local Node.js process on port 45678.
# This script starts the Python engine and the Tauri dev server.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    echo ""
    echo "Shutting down..."
    # Kill background engine process (if started standalone)
    [ -n "${ENGINE_PID:-}" ] && kill "$ENGINE_PID" 2>/dev/null && echo "  Engine stopped"
    # Kill n8n process on port 45678 (Tauri's RunEvent::Exit may not fire in dev mode)
    N8N_PIDS=$(lsof -ti tcp:45678 2>/dev/null || true)
    if [ -n "$N8N_PIDS" ]; then
        echo "$N8N_PIDS" | xargs kill 2>/dev/null && echo "  n8n stopped"
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "=== Laya Dev ==="
echo "  n8n is managed by the Tauri app (auto-start/stop on port 45678)"
echo ""

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
