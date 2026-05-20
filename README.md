# Agent Instant Drop

Drop any file, app, or prototype to your human.

## Install

```bash
git clone https://github.com/superbereza/agent-instant-drop
cd agent-instant-drop
./install.sh
```

Creates isolated venv and symlinks `drop` to `~/.local/bin/`.

## Quick Start

```bash
drop start                                              # Start server
drop add ./report.html                                  # Publish file (auto-password)
drop add ./report.html --public                         # Public link
drop add ./dist/                                        # Publish folder (manifest required)
drop add ./bin --run "myapp --port 7777" --port 7777   # App + auto basic auth
drop list                                               # List pages
drop remove abc123                                      # Remove page
drop stop                                               # Stop server
```

## Features

- **Password-protected by default** — `--public` opt-out
- Apps get built-in HTTP basic auth via a thin reverse proxy
- Tunnel required for `--auth` (HTTPS termination); `--auth-insecure` to override on trusted LAN
- Manifest-based security for directories (`.drop-publish`)
- Human-readable URLs: `/p/<secret>/<name>/`
- External IP detection for shareable URLs
- Rate limiting on static-page passwords (3 attempts/min/IP)
- Auto cloudflared tunnel when behind NAT; per-app watchdog

## For Humans

See [docs/README-human.md](docs/README-human.md) for detailed documentation.

## License

MIT
