#!/usr/bin/env bash
# Start all Laya services for development.
#
# Both the engine and n8n are managed by the Tauri app (auto-start on
# launch, auto-stop on quit). This script just launches the Tauri dev server.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    echo ""
    echo "Shutting down..."
    # Kill n8n process on port 45678 (Tauri's RunEvent::Exit may not fire in dev mode)
    N8N_PIDS=$(lsof -ti tcp:45678 2>/dev/null || true)
    if [ -n "$N8N_PIDS" ]; then
        echo "$N8N_PIDS" | xargs kill 2>/dev/null && echo "  n8n stopped"
    fi
    # Kill engine on port 8420 (Tauri's RunEvent::Exit may not fire in dev mode)
    ENGINE_PIDS=$(lsof -ti tcp:8420 2>/dev/null || true)
    if [ -n "$ENGINE_PIDS" ]; then
        echo "$ENGINE_PIDS" | xargs kill 2>/dev/null && echo "  Engine stopped"
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "=== Laya Dev ==="
echo "  Engine and n8n are managed by the Tauri app"
echo ""

# Start Tauri dev (engine is spawned by the Tauri app itself)
echo "Starting Tauri dev server..."
cd "$REPO_ROOT/ui"
npx @tauri-apps/cli dev

# If Tauri exits, clean up
cleanup
