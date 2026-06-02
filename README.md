# Agent Instant Drop

Drop any file, app, or prototype to your human. **Password-protected by default.**

Ships an agent **skill** at [`skills/drop/SKILL.md`](skills/drop/SKILL.md), wired up for **Claude Code, Cursor, Codex and Gemini** from one source (see below).

## Install

Three ways — pick one. All end with the same `drop` CLI and `drop` skill; the `.venv/` builds on first run.

### 1. Standalone (clone + symlinks)

```bash
git clone https://github.com/superbereza/agent-instant-drop
cd agent-instant-drop
./install.sh
```

Symlinks `bin/drop` → `~/.local/bin/drop` and the skill → `~/.claude/skills/drop`, and installs `cloudflared` to `~/.drop/bin/` (used for HTTPS tunnels).

### 2. As a Claude Code plugin (this repo is its own marketplace)

```text
/plugin marketplace add superbereza/agent-instant-drop
/plugin install drop@agent-instant-drop
```

Claude pulls the repo and loads `skills/drop/SKILL.md`; the first `drop …` call builds the venv (the skill calls the bundled `${CLAUDE_PLUGIN_ROOT}/bin/drop`). Tunnel features still need `cloudflared` — run `./install.sh` once or place it on PATH.

### 3. From an aggregate marketplace

```text
/plugin marketplace add superbereza/superbereza-skills
/plugin install drop@superbereza-skills
```

### Other agents

The same `skills/` directory is exposed to **Cursor** (`.cursor-plugin/`), **Codex** (`.codex-plugin/`) and **Gemini** (`gemini-extension.json` → [`GEMINI.md`](GEMINI.md)). One skill, one source — see [`AGENTS.md`](AGENTS.md).

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
- Automatic migration from the legacy storage format on first start

## For Humans

See [docs/README-human.md](docs/README-human.md) for detailed documentation.

## License

MIT

## OpenCode

This skill also supports [OpenCode](https://opencode.ai) — see [`.opencode/INSTALL.md`](.opencode/INSTALL.md).
