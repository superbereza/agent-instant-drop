---
name: drop
description: Drop files, apps, or prototypes to your human via drop CLI
---

# Agent Instant Drop

Drop any file, app, or prototype to your human. **Password-protected by default.** Use `--public` to opt out.

> **Invoking `drop`:** call `drop` directly. It builds its own venv on first run, so there's no separate setup step.

## Quick Start

```bash
# Start the static server (once)
drop start

# Publish a static page — auto-generated password
drop add ./report.html --desc "Weekly report"
# → http://192.168.1.50:8080/p/abc.../
# → Password: xK9mP2qLx

# Explicit public page
drop add ./public.html --public

# Custom password
drop add ./secret.html --password mysecret

# Register an app — basic auth is added automatically
drop add ./app --run "flask run --host 127.0.0.1 --port 5000" --port 5000 --name api
# → App registered
# → Auth: basic (drop / 8wK2mP4qLxNR)

# Public app (no auth)
drop add ./demo --run "python -m http.server 7777 --bind 127.0.0.1" --port 7777 --public

# Custom basic auth credentials
drop add ./app --run "..." --port 5000 --auth basic:admin:s3cret

# Run the app (spawns app + proxy + cloudflared tunnel atomically)
drop start api
# → App started: https://abc.trycloudflare.com/
# →   Auth: basic (drop / <see add output>)

# Tail its logs
drop logs api          # app log
drop logs api --proxy  # proxy log
drop logs api --tunnel # cloudflared log
drop logs api -f       # follow mode

# Stop
drop stop api
```

## Commands

| Command | What |
|---------|------|
| `drop start [--port N]` | Start static server (default: 8080) |
| `drop start <name>` | Start a registered app |
| `drop start <name> --auth-insecure` | Allow cleartext basic auth (skip tunnel requirement) |
| `drop stop` / `drop stop <name>` | Stop server / app |
| `drop status` | Show registered pages count |
| `drop add <path>` | Publish (password-protected by default) |
| `drop add <path> --public` | Publish without password |
| `drop add <path> --run "cmd" --port N` | Register an app |
| `drop list` / `drop list -a` | List pages from cwd / all pages |
| `drop logs <name> [--proxy|--tunnel] [-f]` | Tail a per-page log |
| `drop remove <id|name>` | Remove a registered page |
| `drop cleanup` | Remove entries whose source file no longer exists |

## Flags for `drop add`

- `--name <slug>` / `-n <slug>` — short slug used in URLs and as a stable handle
- `--desc <text>` / `-d <text>` — description for `drop list`
- `--password [PASS]` / `-p [PASS]` — static-page password. Default: auto-generated.
- `--public` — opt out of default auth. Apps and pages.
- `--run <cmd>` / `-r <cmd>` — command to spawn (makes the entry an app)
- `--port <N>` — app port (required with `--run`)
- `--auth basic[:user:pass]` — explicit basic auth for apps (default: auto-gen `drop:<12char>`)
- `--rewrite-host` — proxy rewrites `http://localhost:<port>` in text bodies (for SPAs that hardcode it)

## Flags for `drop start`

- `--port <N>` — server port (only when starting the static server, default 8080)
- `--host <ip>` — override detected IP for URL output
- `--no-tunnel` — skip cloudflared
- `--auth-insecure` — allow cleartext basic auth without tunnel (override)

## Auth model

**Static pages** use a cookie-form login. Submit password → cookie set (15 min, httponly) → content served. Rate limit: 3 attempts/min/IP/page.

**Apps** sit behind a thin reverse proxy that does HTTP basic auth. Proxy:
- Validates path starts with `/` (SSRF guard)
- Rejects `Upgrade` (WebSocket) requests with 501 (V1 limitation)
- Passes through HTTP/1.1 GET/POST/etc and 30x redirects
- With `--rewrite-host`: replaces `http://localhost:<app_port>` in text response bodies with the tunnel origin (for SPAs)

### Important: side door

The proxy guards itself. Your app's own port still listens wherever you bound it. If you bind `0.0.0.0` on a public IP, **the app port is reachable bypassing the proxy** — drop refuses to start in this case (probe after spawn). Override with `--allow-side-door` in your `Page` registration (advanced; CLI flag pending).

**Best practice:** always bind apps to `127.0.0.1` when using `--auth`:

```bash
drop add ./api --run "flask run --host 127.0.0.1 --port 5000" --port 5000
```

### `--auth` requires a tunnel

basic-auth credentials transmitted over plain HTTP travel in base64 (cleartext). v2 refuses to start an auth app without a tunnel unless you pass `--auth-insecure`.

## Tunnel (NAT + HTTPS)

drop uses cloudflared quick-tunnels to give your app an HTTPS URL.

- Behind NAT: tunnel auto-starts.
- Apps with `--auth`: tunnel always attempted (HTTPS termination for basic auth).
- cloudflared logs land in `~/.drop/logs/<page_id>.tunnel.log` — no PIPE-buffer hangs (v1 bug fixed).
- No automatic restart: if cloudflared crashes after `drop start`, `drop stop <app> && drop start <app>` to recover (V3 backlog: named tunnels + watchdog process).

## Publishing Directories (Manifest Required)

```bash
cat > ./project/.drop-publish << 'EOF'
index.html
assets/**
EOF

drop add ./project/
```

Manifest patterns: exact files, `*.html` globs, `dir/**` recursive directories.

`.env` files are always blocked, even if in the manifest (except `.env.example`).

## Tips

- **`cd` to project first, then `drop add .`** — paths are stored absolute; `drop list` filters by cwd.
- **Always bind apps to `127.0.0.1` with `--auth`** — side door is real.
- **Save the auto-generated credentials** — printed once at `drop add`; only the hash is stored.
- **Use `drop logs <name> -f`** to follow a misbehaving app's stdout/stderr.
- **`drop add ... --name X`** is unique; second call with same name fails fast.
