# agent-instant-drop — Claude Code context

This repo ships a Claude Code **skill** at [`skills/drop/SKILL.md`](skills/drop/SKILL.md)
that drives the `drop` CLI — publish any file, folder, or running app to a
password-protected HTTPS link.

- When installed (via `./install.sh` or `/plugin install drop@...`), Claude
  auto-loads the skill — reach for it whenever you want to hand the human a file,
  report, prototype, or running app.
- The CLI is `drop` (on PATH after `install.sh`). If it isn't on PATH, run it from
  this repo as `./bin/drop` (or `${CLAUDE_PLUGIN_ROOT}/bin/drop` when loaded as a
  plugin) — it builds its own venv on first run.
- Tunnel features also need `cloudflared`; `install.sh` fetches it to `~/.drop/bin/`.

The skill file is the single source of truth for usage; this is just the pointer.
