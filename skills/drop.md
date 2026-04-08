---
name: drop
description: Drop files, apps, or prototypes to your human via drop CLI
---

# Agent Instant Drop

Drop any file, app, or prototype to your human.

## Quick Start

```bash
# Start server (once)
drop start

# Publish a page (public by default)
drop add ./report.html --desc "Weekly report"
# → http://192.168.1.50:8080/p/abc123

# Publish with password
drop add ./secret.html --password --desc "Confidential"
# → http://192.168.1.50:8080/p/def456
# → Password: xK9mP2

# List pages from current directory
drop list

# List all pages
drop list --all

# Remove when done
drop remove abc123

# Stop server
drop stop
```

## Commands

| Command | Description |
|---------|-------------|
| `drop start [--port N]` | Start server (default: 8080) |
| `drop stop` | Stop server |
| `drop status` | Show server URL and all pages |
| `drop add <path>` | Publish file/folder (public by default) |
| `drop add <path> --run "cmd" --port N` | Register an app |
| `drop start <name>` | Start a registered app |
| `drop stop <name>` | Stop a running app |
| `drop cleanup` | Remove crashed/orphaned apps |
| `drop list` | List pages from current directory |
| `drop list --all` | List all pages |
| `drop remove <id>` | Remove published page |

## Flags for `drop add`

- `--name "slug"` / `-n "slug"` — human-readable name in URL
- `--desc "text"` / `-d "text"` — description for listing
- `--password` / `-p` — protect with auto-generated password
- `--password <pass>` — protect with custom password
- `--run "command"` / `-r "command"` — run command for apps
- `--port <N>` — app port (required with --run)
- (no flags) — public access

**URL format:** `http://host:port/p/<secret>/<name>/`

Example:
```bash
drop add ./dist/ --name my-feature --desc "Feature prototype"
# → http://94.131.101.149:8080/p/a8k2m9x4p1n7q3w5/my-feature/
```

## Flags for `drop start`

- `--port <N>` — server port (default: 8080)
- `--host <ip>` — override detected IP
- `--no-tunnel` — disable automatic tunnel when behind NAT

## Examples

```bash
# Public page with description
drop add ./report.html --desc "Q1 Report"

# Auto-generated password
drop add ./secret.html --password --desc "Internal docs"

# Custom password
drop add ./secret.html --password mysecret

# Publish folder (serves index.html)
drop add ./dist/ --desc "Build output"

# Start on different port
drop start --port 9000
```

## Apps (Lifecycle Manager)

Register, start, and stop applications. Drop manages the process — the app runs directly on its own port. Drop does NOT proxy or add auth to apps; the app handles its own security.

```bash
# Register an app with run command and port
drop add ./app.py --run "flask run --port 5000" --port 5000 --name api
# → App registered: http://192.168.1.50:5000/
# → Run 'drop start <id>' to start the app

# Start the app
drop start api
# → App started: http://192.168.1.50:5000/

# Stop the app
drop stop api
# → Stopped api

# List shows app status
drop list
# api [app] [running] http://192.168.1.50:5000/
# report [page] http://192.168.1.50:8080/p/def456/report/

# Clean up crashed apps
drop cleanup
```

**App lifecycle:**
- `drop add --run --port` — registers app (stopped state)
- `drop start <name>` — runs the command on specified port
- `drop stop <name>` — kills the process
- `drop cleanup` — removes crashed/orphaned apps

**Apps vs pages:** Pages are served through the drop server (port 8080) with optional password protection. Apps run independently on their own port — add auth in the app itself if needed.

**Status indicators:**
- `[running]` — app process is active
- `[stopped]` — registered but not running
- `[crashed]` — process exited unexpectedly

## Tunnel (NAT Support)

When running behind NAT, drop automatically creates a public URL via cloudflared tunnel.

```bash
# Behind NAT - tunnel starts automatically
$ drop start
Detected NAT, starting tunnel...
Server started: https://random-words.trycloudflare.com
  (tunneled via cloudflared)

# Apps also get tunnels
$ drop start myapp
Detected NAT, starting tunnel...
App started: https://other-random.trycloudflare.com
  (tunneled via cloudflared)

# Disable tunnel if needed
$ drop start --no-tunnel
Server started: http://192.168.1.50:8080 (local)
```

Tunnel auto-restarts if cloudflared crashes. URLs change on each restart (quick tunnels don't persist).

## Publishing Directories (Manifest Required)

To publish a directory, create `.drop-publish` manifest first:

```bash
# 1. Create manifest with allowed patterns
cat > ./project/.drop-publish << 'EOF'
index.html
assets/**
EOF

# 2. Now publish works
drop add ./project/ --desc "My project"
```

**Why manifest?** Prevents accidental exposure of `.env`, config files, etc. Only files matching manifest patterns are served.

**Manifest syntax:**
- `index.html` — exact file
- `assets/**` — directory and all contents
- `*.html` — glob pattern

**Before creating manifest, check what HTML loads:**
```bash
grep -E "src=|href=" index.html | grep -oE '\./[^"'"'"']*' | sort -u
```
Add ALL referenced directories to manifest (assets/, config/, js/, etc.)

**Security:** `.env` files are always blocked, even if in manifest (except `.env.example`).

**API calls won't work in pages** — drop serves static files only. If HTML uses `fetch('/api/...')`, either embed mock data or register as an app with `--run`.

**Single files** work without manifest:
```bash
drop add ./report.html  # OK, no manifest needed
```

## Tips

- **Always `cd` to project first, then `drop add .`** — don't use absolute paths, so `drop list` works correctly
- `drop list` filters by current directory — use in project folder to see only that project's pages
- Server auto-detects external IP for shareable URLs
- Page IDs support partial matching: `drop remove abc` works for `abc123`
- Files are served from original location — updates appear immediately
- Rate limiting: 3 password attempts per minute per IP
