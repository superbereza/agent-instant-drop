#!/bin/bash
# Agent Instant Drop - Uninstall Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"
SKILLS_ROOT="${DROP_SKILLS_ROOT:-$HOME}"

echo "Uninstalling Agent Instant Drop..."

# Stop server if running
if [ -f "$HOME/.drop/server.pid" ]; then
    pid=$(cat "$HOME/.drop/server.pid")
    if kill -0 "$pid" 2>/dev/null; then
        echo "Stopping server..."
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$HOME/.drop/server.pid"
fi

# Stop and disable systemd service (Linux only)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if systemctl --user is-active drop.service &>/dev/null; then
        echo "Stopping systemd service..."
        systemctl --user stop drop.service
    fi
    if systemctl --user is-enabled drop.service &>/dev/null; then
        echo "Disabling systemd service..."
        systemctl --user disable drop.service
    fi
    UNIT_FILE="$HOME/.config/systemd/user/drop.service"
    if [[ -f "$UNIT_FILE" ]]; then
        echo "Removing systemd unit..."
        rm "$UNIT_FILE"
        systemctl --user daemon-reload
    fi
fi

# Remove command symlink
if [ -L "$LOCAL_BIN/drop" ]; then
    rm "$LOCAL_BIN/drop"
    echo "  ✓ Removed drop command"
fi

# Remove old pst symlink if exists
if [ -L "$LOCAL_BIN/pst" ]; then
    rm "$LOCAL_BIN/pst"
    echo "  ✓ Removed old pst command"
fi

# Remove skill
SKILL_DIR="$SKILLS_ROOT/.claude/skills/drop"
if [ -d "$SKILL_DIR" ]; then
    rm -rf "$SKILL_DIR"
    echo "  ✓ Removed drop skill"
fi

# Remove old pages-sharing skill if exists
OLD_SKILL_DIR="$SKILLS_ROOT/.claude/skills/pages-sharing"
if [ -d "$OLD_SKILL_DIR" ]; then
    rm -rf "$OLD_SKILL_DIR"
    echo "  ✓ Removed old pages-sharing skill"
fi

# Also remove from old location (~/.claude/commands/) if exists
if [ -L "$HOME/.claude/commands/pages-sharing.md" ]; then
    rm "$HOME/.claude/commands/pages-sharing.md"
    echo "  ✓ Removed old command symlink"
fi

# Remove venv
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "  ✓ Removed venv"
fi

echo ""
echo "Agent Instant Drop uninstalled!"
echo ""
echo "Note: ~/.drop/ directory preserved (contains your pages config)"
echo "To remove completely: rm -rf ~/.drop"
