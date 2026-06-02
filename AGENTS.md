# agent-instant-drop — agent guide (cross-agent)

Publish any **file, folder, or running app** to a **password-protected HTTPS link**
(Cloudflare tunnel) so you can hand it to your human. Built for AI agents.

This repo packages the same capability for several coding agents from one source:

- **Claude Code / Cursor / Codex** — load the skill at `skills/drop/SKILL.md`
  (auto-discovered via `.claude-plugin/`, `.cursor-plugin/`, `.codex-plugin/`).
- **Gemini** — see [`GEMINI.md`](GEMINI.md).
- Full, authoritative usage: [`skills/drop/SKILL.md`](skills/drop/SKILL.md).

## Invoking the CLI

`drop` is on PATH after `./install.sh`. Otherwise call `./bin/drop` from this repo
(or `${CLAUDE_PLUGIN_ROOT}/bin/drop` as a plugin). The launcher builds its own venv
on first run — no setup step. Tunnels also need `cloudflared` (install.sh fetches it).

```bash
drop start                              # start the server (once)
drop add ./report.html                  # publish a file (auto-password)
drop add ./report.html --public         # public link
drop add ./dist/                        # publish a folder
drop add ./bin --run "app --port 7777" --port 7777   # publish a running app
drop list                               # list pages
drop stop                               # stop the server
```

Read the skill for the full command set and options.
