# agent-instant-drop — Gemini context

Publish any **file, folder, or running app** to a **password-protected HTTPS link**
(Cloudflare tunnel) and hand it to your human.

Gemini doesn't auto-load `skills/`, so the essentials are inlined here; the full
reference is in [`skills/drop/SKILL.md`](skills/drop/SKILL.md).

## Invoking

`drop` is on PATH after `./install.sh`; otherwise run `./bin/drop` from this repo
(it builds its own venv on first run). Tunnels also need `cloudflared`
(install.sh fetches it to `~/.drop/bin/`).

## Cheat sheet

```bash
drop start                              # start the static server (once)
drop add ./report.html                  # publish a file — password auto-generated
drop add ./report.html --public         # public link, no password
drop add ./dist/                        # publish a folder
drop add ./bin --run "app --port 7777" --port 7777   # publish a running app + basic auth
drop list                               # list published pages
drop logs <name>                        # tail an app's log
drop stop <name>                        # stop one running app
drop remove <id>                        # remove a page
drop stop                               # stop the server
```

Default is **password-protected**; pass `--public` to opt out. The full options
and output details are in the skill file.
