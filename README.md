# Agent Instant Drop

Drop any file, app, or prototype to your human. **Password-protected by default.**

## Install

```bash
git clone https://github.com/superbereza/agent-instant-drop
cd agent-instant-drop
./install.sh
```

Creates isolated venv and symlinks `drop` to `~/.local/bin/`. Installs `cloudflared` to `~/.drop/bin/` (used for HTTPS tunnels).

## Quick Start

```bash
drop start                                              # Start static server
drop add ./report.html                                  # Publish file (auto-password)
drop add ./report.html --public                         # Public link
drop add ./dist/                                        # Publish folder (manifest required)
drop add ./bin --run "myapp --port 7777" --port 7777   # App + auto basic auth
drop list                                               # List pages
drop logs myapp                                         # Tail app log
drop stop myapp                                         # Stop a running app
drop remove abc123                                      # Remove page
drop stop                                               # Stop server
```

## Features

- **Password-protected by default** — `--public` opt-out
- Apps run behind a built-in HTTP basic-auth reverse proxy
- Tunnel required for `--auth` (HTTPS termination via cloudflared); `--auth-insecure` overrides on trusted LAN
- `--rewrite-host` for SPAs that hardcode `http://localhost:<port>` in their JS bundles
- Manifest-based safety for directories (`.drop-publish`)
- Path-traversal + symlink + `.env` blocked at the file-serving layer
- Per-page rate limiting on static-page password attempts (3/min/IP/page)
- Single source of truth for subprocess spawning (no PIPE-buffer hangs)
- Atomic app lifecycle: any phase failure rolls back prior phases
- v1 → v2 schema migration runs automatically on first start

## What's Different in v2

v2 is a full rewrite focused on correctness and maintainability:

- **No more "tunnel dies after 30s"** — cloudflared uses `--logfile`, no PIPE buffer.
- **No more silent dup names** — UNIQUE constraint on registered names.
- **No more side-door** — `--auth` apps that bind `0.0.0.0` on a public IP are refused (override via `--allow-side-door`).
- **Atomic lifecycle** — app+proxy+tunnel start as a unit; any failure rolls back.
- **Tested** — 160+ unit/integration/e2e tests; pytest CI on every push.

See `docs/2026-05-20-v2-greenfield-design.md` for the full architecture.

## For Humans

See [docs/README-human.md](docs/README-human.md) for detailed documentation (v1 reference; v2 keeps the same CLI surface).

## License

MIT
