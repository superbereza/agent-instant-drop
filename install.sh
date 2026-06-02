#!/bin/bash
# Agent Instant Drop - Install Script
# Creates isolated venv and installs skill

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOCAL_BIN="$HOME/.local/bin"

# Default skills location - user home directory
SKILLS_ROOT="${DROP_SKILLS_ROOT:-$HOME}"

echo "Installing Agent Instant Drop..."

# Create venv
echo "Creating venv in $VENV_DIR..."
python3 -m venv "$VENV_DIR"

# Install package
echo "Installing package..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" -q

# Create ~/.local/bin if needed
mkdir -p "$LOCAL_BIN"

# Symlink the self-bootstrapping launcher (not the venv entry point directly),
# so the same `drop` works even if the venv is rebuilt or used as a plugin.
chmod +x "$SCRIPT_DIR/bin/drop"
echo "Creating command symlink in $LOCAL_BIN..."
ln -sf "$SCRIPT_DIR/bin/drop" "$LOCAL_BIN/drop"
echo "  ✓ drop"

# Create skill in .claude/skills/ structure
SKILL_DIR="$SKILLS_ROOT/.claude/skills/drop"
mkdir -p "$SKILL_DIR"

echo "Creating skill in $SKILL_DIR..."
ln -sf "$SCRIPT_DIR/skills/drop/SKILL.md" "$SKILL_DIR/SKILL.md"
echo "  ✓ drop"

# Systemd integration (Linux only)
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"

    cat > "$SYSTEMD_DIR/drop.service" << EOF
[Unit]
Description=Agent Instant Drop
After=network.target

[Service]
Type=simple
EnvironmentFile=-%h/.drop/systemd.env
ExecStart=$VENV_DIR/bin/python -c "from drop.server import run_server; run_server()"
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

    echo "  ✓ systemd unit created"
    systemctl --user daemon-reload
fi

# Cloudflared (tunnel support)
CLOUDFLARED_BIN="$HOME/.drop/bin/cloudflared"
if ! command -v cloudflared &>/dev/null && [[ ! -f "$CLOUDFLARED_BIN" ]]; then
    echo "Installing cloudflared..."
    mkdir -p "$HOME/.drop/bin"
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="amd64" ;;
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm" ;;
        *)       CF_ARCH="amd64" ;;
    esac
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    curl -sL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-${OS}-${CF_ARCH}" \
        -o "$CLOUDFLARED_BIN"
    chmod +x "$CLOUDFLARED_BIN"
    echo "  ✓ cloudflared"
else
    echo "  ✓ cloudflared (already installed)"
fi

echo ""
echo "Agent Instant Drop installed!"
echo ""
echo "Usage:"
echo "  drop start              # Start server"
echo "  drop add ./file.html    # Publish file"
echo "  drop stop               # Stop server"
echo ""
echo "Skill available for all projects under: $SKILLS_ROOT"
echo ""
echo "To change skill location, set DROP_SKILLS_ROOT before install:"
echo "  DROP_SKILLS_ROOT=/path/to/projects ./install.sh"
echo ""
echo "To uninstall: ./scripts/uninstall.sh"
