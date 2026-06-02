# Installing for OpenCode

No marketplace — add to your `opencode.json` (global `~/.config/opencode/opencode.json` or project):

```json
{ "plugin": ["drop@git+https://github.com/superbereza/agent-instant-drop.git"] }
```

Restart OpenCode. The plugin (`.opencode/plugins/drop.js`) registers this repo's
`skills/` directory — no symlinks. Adapted from [obra/superpowers](https://github.com/obra/superpowers) (MIT); verify against your OpenCode version.
