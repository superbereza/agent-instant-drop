# agent-instant-drop — agent guide

Publish any **file, folder, or running app** to a **password-protected HTTPS link**
(Cloudflare tunnel) so you can hand it to your human. Built for AI agents.

The same capability is wired up for several coding agents from one source:

- **Claude Code / Cursor / Codex** — load the skill at [`skills/drop/SKILL.md`](skills/drop/SKILL.md)
  (auto-discovered via `.claude-plugin/`, `.cursor-plugin/`, `.codex-plugin/`).
- **Gemini** — reads this file (`gemini-extension.json` → `contextFileName: AGENTS.md`).
- Full, authoritative usage: [`skills/drop/SKILL.md`](skills/drop/SKILL.md).

## Invoking the CLI

`drop` is on PATH after `./install.sh`. Otherwise call `./bin/drop` from this repo
(or `${CLAUDE_PLUGIN_ROOT}/bin/drop` when loaded as a plugin). The launcher builds
its own venv on first run — no setup step. Tunnels also need `cloudflared`
(install.sh fetches it to `~/.drop/bin/`).

## Cheat sheet

```bash
drop start                              # start the server (once)
drop add ./report.html                  # publish a file (auto-password)
drop add ./report.html --public         # public link, no password
drop add ./dist/                        # publish a folder
drop add ./bin --run "app --port 7777" --port 7777   # publish a running app + basic auth
drop list                               # list published pages
drop logs <name>                        # tail an app's log
drop stop <name>                        # stop one running app
drop remove <id>                        # remove a page
drop stop                               # stop the server
```

Default is **password-protected**; pass `--public` to opt out. Read the skill for
the full command set, options, and output details.
## Maintainer note

Changing a skill or its payload? It reaches installed plugins **only after a release** —
`scripts/bump.sh <v>` → commit → tag `vX.Y.Z` → GitHub release → `/plugin update`. A commit on
`main` alone propagates nothing (Claude/Codex cache plugins by version string). Full rule and the
MAJOR/MINOR/PATCH guidance: the `skill-builder` skill, §7.
