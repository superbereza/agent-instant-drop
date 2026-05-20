---
name: drop
description: Drop files, apps, or prototypes to your human via drop CLI
---

# Agent Instant Drop

Drop any file, app, or prototype to your human. **Password-protected by default.** Use `--public` to opt out.

## Quick Start

```bash
# Start server (once)
drop start

# Publish a page (password auto-generated)
drop add ./report.html --desc "Weekly report"
# → http://192.168.1.50:8080/p/abc123
# → Password: xK9mP2qLx

# Explicit public page
drop add ./public.html --public

# Custom password
drop add ./secret.html --password mysecret

# Register an app — basic auth is added automatically
drop add ./app --run "flask run --port 5000" --port 5000 --name api
# → App registered: http://...:5000/
# → Auth: basic (drop / 8wK2mP4qLxNR)

# Public app (no auth)
drop add ./demo --run "python -m http.server 7777" --port 7777 --public

# Custom basic auth credentials
drop add ./app --run "flask run --port 5000" --port 5000 --auth basic:admin:s3cret

# List pages from current directory
drop list                  # [auth] tag marks protected apps
drop list --all            # all pages

# Remove when done
drop remove abc123

# Stop server
drop stop
```

## Commands

| Command | Description |
|---------|-------------|
| `drop start [--port N]` | Start server (default: 8080) |
| `drop start <name>` | Start a registered app |
| `drop start <name> --auth-insecure` | Allow cleartext basic auth without tunnel |
| `drop stop` / `drop stop <name>` | Stop server or app |
| `drop status` | Show server URL and all pages |
| `drop add <path>` | Publish file/folder (auto-password) |
| `drop add <path> --public` | Publish without password |
| `drop add <path> --run "cmd" --port N` | Register an app (auto basic auth) |
| `drop list` | List pages from current directory |
| `drop list --all` | List all pages |
| `drop remove <id>` | Remove published page |
| `drop cleanup` | Remove crashed/orphaned apps |

## Flags for `drop add`

- `--name "slug"` / `-n "slug"` — human-readable name in URL
- `--desc "text"` / `-d "text"` — description for listing
- `--password [PASS]` / `-p [PASS]` — explicit password for static pages (auto-gen if no value). Default: auto-password.
- `--auth basic[:user:pass]` — basic auth for apps. Default: auto-gen `drop:<12char>`. Apps only.
- `--public` — opt out of default auth. For pages/apps that are intentionally public.
- `--run "command"` / `-r "command"` — run command for apps
- `--port <N>` — app port (required with `--run`)

**URL format:** `http://host:port/p/<secret>/<name>/`

## Flags for `drop start`

- `--port <N>` — server port (default: 8080)
- `--host <ip>` — override detected IP
- `--no-tunnel` — disable automatic tunnel when behind NAT
- `--auth-insecure` — for apps with auth: allow cleartext (basic auth over plain HTTP) without a tunnel. Use only on trusted LAN/dev.

## Auth model

**Static pages** use cookie-form auth with rate limiting. A password form appears; correct password sets a cookie.

**Apps** use HTTP basic auth via a thin reverse proxy spawned by drop:
- Proxy lives in front of your app on an auto-allocated port
- Tunnel (cloudflared) points to the proxy, not directly to your app
- Default: proxy binds 127.0.0.1, only the tunnel can reach it → secure
- With `--auth-insecure`: proxy binds 0.0.0.0 for LAN access, basic auth travels in cleartext (warning shown)

### Important: side door

Drop's auth protects the proxy port. Your app's own port (the one in `--port`) is not protected by drop. If your app binds `0.0.0.0` on a public IP, anyone can reach it directly bypassing the proxy. Always bind your app to `127.0.0.1` when using `--auth`:

```bash
drop add ./api --run "flask run --host 127.0.0.1 --port 5000" --port 5000
```

Drop prints a warning to this effect on every `drop start <app>`.

### `--auth` requires a tunnel

When an app has `--auth`, `drop start` will refuse if no tunnel can be started (cloudflared missing, `--no-tunnel` given, etc.). This is to keep basic auth credentials off the wire in cleartext. Override with `--auth-insecure` if you know what you're doing.

## Apps (Lifecycle Manager)

Register, start, and stop applications. Drop manages the process and (if auth is enabled) a reverse proxy.

```bash
# Register an app (auto basic auth)
drop add ./app.py --run "flask run --port 5000" --port 5000 --name api
# → App registered: http://192.168.1.50:5000/
# → Auth: basic (drop / 8wK2mP4qLxNR)

# Register a public app
drop add ./demo --run "python -m http.server 7777" --port 7777 --public --name demo

# Start the app
drop start api
# → ⚠ --auth protects tunnel only. ...
# → App started: https://abc.trycloudflare.com/  (auth: drop / <see add output>)

# Stop the app (tunnel, proxy, app all killed)
drop stop api

# List with status
drop list
# api    [app] [running] [auth]  https://abc.trycloudflare.com/
# demo   [app] [running]         http://192.168.1.50:7777/ (public)

# Clean up crashed apps
drop cleanup
```

**App lifecycle:**
- `drop add --run --port` — registers app (stopped state). Auth is added by default.
- `drop start <name>` — runs the command on specified port. Spawns proxy + tunnel if auth.
- `drop stop <name>` — kills tunnel, proxy, app
- `drop cleanup` — removes crashed/orphaned apps

**Apps vs pages:** Pages are served through the drop server (port 8080) with optional cookie-form auth. Apps run on their own port behind a basic-auth proxy (when auth is enabled).

**Status indicators:**
- `[running]` — app process is active
- `[stopped]` — registered but not running
- `[crashed]` — process exited unexpectedly
- `[auth]` — app has basic auth configured

## Tunnel (NAT Support and HTTPS for auth)

Drop uses cloudflared to create a public HTTPS URL.

- **Behind NAT** (laptop on a home router): tunnel auto-starts so the URL is reachable from outside.
- **Apps with `--auth`**: tunnel is always required (HTTPS termination for basic auth). If cloudflared is missing or `--no-tunnel` is set, drop refuses to start with a hint about `--auth-insecure`.
- **No automatic tunnel restart**: if cloudflared crashes after `drop start`, the tunnel URL stops working — `drop stop <app>` then `drop start <app>` to recover.

```bash
# Behind NAT - tunnel starts automatically
$ drop start
Starting tunnel...
Server started: https://random-words.trycloudflare.com

# App with auth
$ drop start myapp
⚠ --auth protects tunnel only. ...
Starting tunnel...
App started: https://other-random.trycloudflare.com/
  Auth: basic (drop / <hidden>)
  (tunneled via cloudflared)

# App with auth, no tunnel available — refuse
$ drop start myapp --no-tunnel
Error: --no-tunnel conflicts with --auth (cleartext over HTTP)
  Hint: Drop --no-tunnel, or pass --auth-insecure to confirm.

# Override
$ drop start myapp --no-tunnel --auth-insecure
App started: http://192.168.1.50:41234/
  Auth: basic (drop / <hidden>)
⚠ CLEARTEXT: basic auth credentials transmitted in base64 over plain HTTP.
  Anyone on the network path can read them. Use only on trusted LAN.

# Disable tunnel (for non-auth apps)
$ drop start mydemo --no-tunnel
App started: http://192.168.1.50:7777/
```

## Publishing Directories (Manifest Required)

To publish a directory as static content, create `.drop-publish` manifest first:

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

**API calls won't work in static pages** — drop serves static files only. If HTML uses `fetch('/api/...')`, either embed mock data or register as an app with `--run`.

**Single files** work without manifest:
```bash
drop add ./report.html  # OK, no manifest needed
```

## Tips

- **Always `cd` to project first, then `drop add .`** — don't use absolute paths, so `drop list` works correctly.
- **Bind your app to 127.0.0.1 when using `--auth`** — otherwise the app port is reachable bypassing the proxy.
- **Save the auto-generated credentials** — drop prints them once at `drop add`. After that, they're stored as a hash; you can't recover the plaintext.
