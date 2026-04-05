#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Meeting Minutes Taker — Uninstall"
echo ""

# Stop and remove service
if [ -f "$HOME/Library/LaunchAgents/com.meetingminutes.server.plist" ]; then
    launchctl unload "$HOME/Library/LaunchAgents/com.meetingminutes.server.plist" 2>/dev/null || true
    rm "$HOME/Library/LaunchAgents/com.meetingminutes.server.plist"
    echo "✓ Service removed"
fi

# Remove venv
if [ -d ".venv" ]; then
    rm -rf .venv
    echo "✓ Virtual environment removed"
fi

# Remove node_modules
if [ -d "web/node_modules" ]; then
    rm -rf web/node_modules
    echo "✓ Node modules removed"
fi

echo ""
echo "Uninstall complete."
echo "Data files (recordings, transcripts, minutes) are preserved in data/"
echo "To remove everything: rm -rf $SCRIPT_DIR"
