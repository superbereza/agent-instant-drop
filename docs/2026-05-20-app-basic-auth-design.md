# App Basic Auth — Design Document

**Date:** 2026-05-20
**Status:** Approved

## Problem

Сейчас `drop add ... --run "..." --port N` регистрирует app и при старте делает passthrough-tunnel напрямую к app-порту. Auth нет: скилл `skills/drop.md` явно говорит "add auth in the app itself if needed". Это создаёт три проблемы:

1. App-разработчик каждый раз делает свой ad-hoc auth (или забывает)
2. Дефолт = публично, легко accidentally заэкспозить backend с state
3. Несимметрично со static pages, у которых есть `--password` (cookie-form)

## Goal

Одна команда даёт secure HTTPS URL для app'а из коробки:

```
drop add ./bin --run "myserver --port 7777" --port 7777
→ App registered. Auth: drop / 8wK2mP4qLxNR
drop start <appname>
→ App started: https://abc.trycloudflare.com/  (auth: drop / 8wK2mP4qLxNR)
```

Плюс — default-flip: всё (static + apps) защищено паролем по умолчанию. `--public` для явного opt-out.

## Decisions (фиксированные)

### Скоуп фичи

1. **V1 protocol scope** — sync HTTP request/response only. WebSocket / streaming / SSE — НЕ поддерживаем. `Upgrade` header → `501 Not Implemented` с понятным сообщением.
   *Reasoning:* speed-of-light для V1 = 50 LOC stdlib proxy. WS требует bidirectional bridge (другая физика), вернёмся если придёт реальный сигнал "у меня сломался Streamlit". 501 предотвращает silent failure.

2. **Auth = per-app secret.** Никаких глобальных creds. Каждый app имеет свой password_hash в registry.

### UX и дефолты

3. **Default-flip = password-by-default для всего.**
   - Static без флагов → auto-gen password (cookie-form, как сейчас при `--password` без значения)
   - App без флагов → auto-gen basic auth `drop:<12char>`
   - `--public` opt-out для обоих
   - Это breaking change для существующих скриптов; drop pre-1.0, миграция = "переучить пальцы". README/skill обновляются.

4. **Flag UX = mirror `--password`.**
   - `--auth basic` → auto-gen `drop:<12char>`, печатается раз
   - `--auth basic:user:pass` → explicit (для кастомного user'а)
   - `--password` для static продолжает работать как сейчас
   - `--public` универсальный opt-out

5. **Help text для новых флагов** обязателен. Per-subcommand epilog с примерами для `drop add` и `drop start`. Top-level `__doc__` обновляется под новые дефолты.

### Безопасность

6. **HTTPS coupling = REFUSE if no tunnel.** При `drop start <app>` где зарегистрирован `--auth`:
   - Tunnel запустился (любая причина: NAT-detect, или force для VPS) → ok
   - Tunnel НЕ запустился → exit с ошибкой, объяснением и подсказкой про `--auth-insecure`
   - **Изменение поведения tunnel'я:** для apps с `--auth` пробуем запустить tunnel даже на VPS public IP (сейчас NAT-detect его блокирует). HTTPS termination нужен для basic auth.

7. **Override = `--auth-insecure` flag** на `drop start`. Разрешает cleartext basic auth без tunnel'я. Печатает большой warning. Явный opt-in, не silent fallback.

8. **Conditional proxy bind:**
   - Tunnel running → proxy binds `127.0.0.1:<auto>` (tunnel единственный entry, ноль extra public ports)
   - `--auth-insecure` без tunnel → proxy binds `0.0.0.0:<auto>` (иначе фича физически не работает, нечего шарить)

9. **Side-door runtime warning.** При каждом `drop start <app>` с auth печатаем:
   ```
   ⚠ --auth protects tunnel only. If your app binds 0.0.0.0 on a public IP,
     app port is still reachable bypassing auth. Use --host 127.0.0.1 in --run.
   ```
   ~5 LOC, без detection (false positives), без env injection (magic).

### Имплементация

10. **Proxy = stdlib `http.server` + `urllib.request`** в новом модуле `src/drop/proxy.py`. ~50 LOC. Запускается как `python -m drop.proxy --page-id <id> --proxy-port <N> --app-port <M> --bind <127.0.0.1|0.0.0.0>`. Subprocess управляется `subprocess.Popen` по аналогии с `tunnel.py`.

11. **App tunnel watchdog — deferred to V2.** Изначально планировался parity с server watchdog'ом, но при имплементации обнаружено что *оба* watchdog'а (старый server + новый app) запускаются как `daemon=True` потоки в short-lived CLI-процессе, который завершается сразу после `drop start` — daemon-потоки умирают вместе с процессом. Pre-existing баг в server watchdog не трогаем; новый app watchdog не добавляем чтобы не плодить dead-code. Реальный фикс требует отдельного long-lived `drop-watchdog` subprocess'а — отдельная задача в V2.

12. **`drop list` indicator** — тэг `[auth]` рядом с `[running]`/`[stopped]` для apps с auth. Creds в list НЕ показываем (только на `add` и `start`).

## Architecture

### Процессы при `drop start <app>` с `--auth`

```
                          ┌─────────────────┐
public internet ─────────►│  cloudflared    │  127.0.0.1:proxy_port
                          │  (tunnel)       │◄───┐
                          └─────────────────┘    │
                                                 │
                          ┌─────────────────┐    │
                          │  drop.proxy     │────┘  accepts only loopback
                          │  basic-auth     │
                          │  passthrough    │  127.0.0.1:app_port (recommended)
                          └─────────────────┘◄───┐
                                                 │
                          ┌─────────────────┐    │
                          │  user's app     │────┘  bind on user
                          │  (--run cmd)    │
                          └─────────────────┘
```

### Start order (`drop start <app>` с auth)

1. Spawn app (`subprocess.Popen` shell `--run`), сохранить `pid`
2. Allocate free port (`socket.bind(('', 0))`), сохранить `proxy_port`
3. Решить bind:
   - Если tunnel будет запущен (всегда пытаемся при `--auth`) → bind `127.0.0.1`
   - Если `--auth-insecure` → bind `0.0.0.0`
4. Spawn `python -m drop.proxy --page-id <id> --proxy-port <N> --app-port <M> --bind <addr>`
5. Wait readiness (poll proxy_port for connect, timeout 5s)
6. Если не `--auth-insecure`:
   - `tunnel.start_tunnel(proxy_port)`
   - **Если не запустился → kill proxy → kill app → exit с ошибкой** (с подсказкой про `--auth-insecure`)
7. Если tunnel запустился → start watchdog
8. Print:
   - Side-door warning
   - (если `--auth-insecure`) cleartext warning
   - URL + creds

### Stop order (`drop stop <app>`)

1. Kill tunnel watchdog (если есть)
2. Kill tunnel (если есть)
3. Kill proxy (если есть)
4. Kill app
5. Очистить в registry: `pid`, `proxy_pid`, `proxy_port`, `tunnel_pid`, `tunnel_url`

### Без `--auth` — текущее поведение

App + опц. tunnel. Никаких изменений в коде кроме default-flip. Если юзер написал `--public` → ничего нового, app старуется как сейчас (кроме того что `--public` явно записан в registry, см. ниже).

## Data Model

### `PageInfo` дополнения

```python
class PageInfo(TypedDict):
    # existing fields...
    source: str
    is_dir: bool
    password_hash: str  # для static — cookie-form pass; для app без auth — ""
    created_at: str
    description: str
    name: str
    type: str  # "static" | "app"
    run_cmd: str
    port: int  # user's app_port
    pid: int
    tunnel_url: str
    tunnel_pid: int
    # NEW
    auth: dict | None  # {"scheme": "basic", "user": "drop", "password_hash": "..."} or None
    public: bool       # True если юзер явно сказал --public (для UX в list)
    proxy_pid: int     # 0 если нет proxy
    proxy_port: int    # 0 если нет proxy
```

`password_hash` оставляем как был (для static cookie-form). `auth` — новый блок именно для apps.

## New Code

### `src/drop/proxy.py` (~50 LOC, stdlib only)

```python
"""Basic-auth reverse proxy for drop apps."""
import argparse, base64, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import storage
from .utils import verify_password

# Read on startup: page_id, app_port from args; auth dict from registry

class ProxyHandler(BaseHTTPRequestHandler):
    APP_PORT: int
    AUTH: dict  # {"scheme": "basic", "user": ..., "password_hash": ...}

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, pw = decoded.partition(":")
        except Exception:
            return False
        return user == self.AUTH["user"] and verify_password(pw, self.AUTH["password_hash"])

    def _reject_upgrade(self) -> bool:
        if "upgrade" in (self.headers.get("Connection", "") or "").lower():
            self.send_response(501)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"WebSocket/Upgrade not supported by drop V1 proxy.\n")
            return True
        return False

    def _proxy(self, method: str):
        if self._reject_upgrade():
            return
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", f'Basic realm="drop"')
            self.end_headers()
            return
        # Forward to app
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        url = f"http://127.0.0.1:{self.APP_PORT}{self.path}"
        req = urllib.request.Request(url, data=body, method=method)
        for h, v in self.headers.items():
            if h.lower() not in ("host", "authorization", "content-length"):
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for h, v in resp.headers.items():
                    if h.lower() not in ("transfer-encoding",):
                        self.send_header(h, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"proxy error: {e}".encode())

    def do_GET(self): self._proxy("GET")
    def do_POST(self): self._proxy("POST")
    def do_PUT(self): self._proxy("PUT")
    def do_DELETE(self): self._proxy("DELETE")
    def do_PATCH(self): self._proxy("PATCH")
    def do_HEAD(self): self._proxy("HEAD")
    def do_OPTIONS(self): self._proxy("OPTIONS")

    def log_message(self, format, *args):
        pass  # silence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page-id", required=True)
    ap.add_argument("--proxy-port", type=int, required=True)
    ap.add_argument("--app-port", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    args = ap.parse_args()

    page = storage.get_page(args.page_id)
    if not page or not page.get("auth"):
        print(f"error: no auth config for {args.page_id}", file=sys.stderr)
        sys.exit(1)

    ProxyHandler.APP_PORT = args.app_port
    ProxyHandler.AUTH = page["auth"]
    ThreadingHTTPServer((args.bind, args.proxy_port), ProxyHandler).serve_forever()


if __name__ == "__main__":
    main()
```

Streaming bodies / chunked encoding — V2. Сейчас читаем body полностью (`urlopen(req).read()`) — для типичных API/HTML страниц ок.

### Изменения в `src/drop/cli.py`

- `cmd_add`:
  - Парсинг `--auth`, `--public` (новые)
  - Валидация: `--auth` требует `--run`; `--auth` несовместим с `--public`
  - Default-flip: если нет `--password`/`--auth` и нет `--public` → auto-gen (static cookie-form pass / app basic auth `drop:<gen>`)
  - Сохранение `auth` блока в storage (через storage.add_page или новый параметр)
  - Output: печатать creds один раз
- `cmd_start_app`:
  - Новый флаг `--auth-insecure`
  - Если у app'а есть `auth`:
    - Side-door warning
    - Allocate proxy_port (socket trick)
    - Решить bind (127.0.0.1 vs 0.0.0.0)
    - Spawn proxy subprocess
    - Wait readiness
    - Tunnel attempt + refuse-or-`--auth-insecure` logic
  - Если у app'а нет auth (`--public`) — старая логика (без proxy)
- `cmd_stop_app`:
  - Kill proxy если есть
  - Очистить новые поля
- `cmd_list`:
  - Тэг `[auth]` рядом со status-индикатором
- Subcommand epilog'и + обновлённый top-level `__doc__`

### Изменения в `src/drop/storage.py`

- Дополнить `PageInfo` (новые поля)
- В `add_page`: принять `auth: dict | None`, `public: bool`
- Новые хелперы: `update_page_proxy(page_id, proxy_pid, proxy_port)`, `clear_page_runtime(page_id)` (обнулить pid/proxy_pid/proxy_port/tunnel_pid/tunnel_url разом)

### Изменения в `src/drop/utils.py`

- `verify_password` уже есть. Может потребоваться `generate_auth_creds()` helper (user="drop", random 12-char).

### Изменения в `tunnel.py`

- `start_watchdog`/`stop_watchdog` сейчас single-instance global (модуль-level state). Для apps нужен per-app watchdog (или отдельный subprocess подобный proxy.py).
- **Решение:** добавить `start_watchdog_for_app(page_id, proxy_port)` который держит per-app state. Альтернатива — отдельный watchdog subprocess. Решим в плане; ключевое — в спеке зафиксировано что watchdog для apps добавляется.

## CLI Changes (полный список)

### `drop add <path>`

Новые флаги:
- `--auth [SPEC]` — `basic` или `basic:user:pass`. Только с `--run`. Несовместим с `--public`.
- `--public` — явный opt-out из auth. Несовместим с `--password` и `--auth`.

Изменённое поведение:
- Без `--password`/`--auth`/`--public`:
  - Static → auto-gen cookie-form password (как `--password` без значения сейчас)
  - App → auto-gen `--auth basic`

Epilog с примерами (см. секцию "Architecture" выше).

### `drop start [name]`

Новый флаг:
- `--auth-insecure` — разрешить app с auth стартовать без tunnel'я (cleartext). Только для apps с auth.

Epilog с примером `drop start myapp --auth-insecure`.

### `drop list`

Изменение output: `[auth]` indicator для app'ов с auth.

### Без изменений: `drop stop`, `drop status`, `drop remove`, `drop cleanup`.

### Top-level `drop --help` (`__doc__`)

Обновлённые примеры:
```
drop start
drop add ./report.html              # auto-password
drop add ./report.html --public     # explicit public
drop add ./bin --run "..." --port N # app + auto basic auth
drop list
drop remove abc123
drop stop
```

## Failure Modes / Errors

| Сценарий | Поведение |
|---|---|
| `--auth` без `--run` | Error: "auth only applies to apps (use with --run)" |
| `--auth` + `--public` | Error: "cannot combine --auth with --public" |
| `--password` + `--public` | Error: "cannot combine --password with --public" |
| `--auth basic:malformed` | Error: "invalid --auth format, expected basic[:user:pass]" |
| `drop start <app>` где app has auth, cloudflared missing | Error + hint: "tunnel required for --auth. Install (./install.sh) or --auth-insecure" |
| Tunnel startup failed | Error + hint to retry или `--auth-insecure` |
| `--auth-insecure` без auth в registry | Игнорируется (no-op) с note |
| Proxy не стартанул в 5s | Kill app, error |
| App не стартанул | Текущий путь, proxy/tunnel не пробуем |
| Tunnel умер во время работы | Watchdog рестартит, обновляет `tunnel_url` в registry |

## Side Effects (изменения существующего поведения)

**Breaking changes:**

1. `drop add ./file.html` теперь auto-protects (раньше = public). Юзер должен явно сказать `--public` для текущего поведения.
2. `drop add ./bin --run --port` теперь auto-protects (раньше = публичный app). `--public` для opt-out.

**Migration:** drop pre-1.0, без миграционных шимов. README и `skills/drop.md` чётко описывают новый дефолт + флаг `--public`.

Существующие записи в `pages.json` остаются как есть:
- Static с непустым `password_hash` — продолжает работать (cookie-form)
- Static с пустым `password_hash` — остаётся public (без auth)
- App без поля `auth` — трактуется как `public: true` (без proxy, как сегодня); в `drop list` отображается без `[auth]` тэга
- App без поля `proxy_pid`/`proxy_port` — отсутствие = 0/нет proxy (новые поля опциональны на чтении)

Только новые записи (созданные после релиза этой фичи) получают auth по умолчанию.

**Не breaking:**
- `--password mypass` работает как раньше
- Apps без auth (`--public`) работают как раньше (никаких proxy/новых процессов)
- Static с password работает как раньше
- Tunnel auto-start для apps без auth — без изменений (NAT-detect)

## Docs Updates

### `skills/drop.md`

- Обновить Quick Start: убрать "public by default", показать `--public` opt-out
- Раздел "Apps" — переписать: больше не "add auth in the app yourself", теперь `--auth` встроен
- Добавить раздел "Auth model": cookie-form для static, basic auth proxy для apps
- Примеры:
  - `drop add ./report.html` → auto password
  - `drop add ./report.html --public` → public
  - `drop add ./bin --run "..." --port N` → app + auto basic auth
  - `drop add ./bin --run "..." --port N --auth basic:admin:s3cret` → custom user
  - `drop start <app> --auth-insecure` → cleartext override
- Раздел "Tunnel": обновить — `--auth` теперь форсит tunnel attempt; cleartext возможен только под `--auth-insecure`

### `README.md`

- Quick Start: обновить пример `drop add ./report.html` (показать что теперь print'ит password)
- Features: добавить "Apps with built-in basic auth", "Secure by default — `--public` для opt-out"
- Один пример с app + auth

## Out of Scope (V2 candidates)

- WebSocket / streaming / SSE — на сигнал реального юзкейса
- Multiple users per app — один пользователь достаточно
- `--auth-file path/to/creds` — чтобы избежать shell history
- Rate limiting на proxy — auto-gen 12-char (~70 bits) делает brute force нерелевантным; добавим если будут weak custom passwords
- Token / bearer auth — не запрашивалось
- Auto-install cloudflared — install.sh уже это делает, требуется только запустить
- Streaming/chunked request+response bodies (`urlopen.read()` буферизует всё в память) — нормально для типичных API/HTML, не для больших файлов или long polling

## Implementation Order (high-level checklist)

(Детальный пошаговый план — отдельный документ через writing-plans skill.)

1. `proxy.py` standalone (можно тестировать без интеграции)
2. `storage.py` data model: новые поля + хелперы
3. `cli.py cmd_add`: `--auth`, `--public`, default-flip, валидация, креды печатать
4. `cli.py cmd_start_app`: proxy spawn, tunnel-required logic, `--auth-insecure`
5. `cli.py cmd_stop_app`: proxy kill, очистка полей
6. `cli.py cmd_list`: `[auth]` indicator
7. Tunnel watchdog для apps
8. Help text / epilogs / `__doc__`
9. `skills/drop.md` обновить
10. `README.md` обновить
11. Smoke test полный сценарий (NAT + auth, --auth-insecure + LAN, --public, conflicts)
12. Push один commit (или серия — на усмотрение плана)

## Testing Notes

Manual smoke tests (нет тестовой инфраструктуры в репо):

- `drop add ./test.html` → видим password в output
- `drop add ./test.html --public` → нет password
- `drop add ./test.html --password mypass --public` → error
- Mini test app (`python -m http.server`), `drop add . --run "python -m http.server 7777" --port 7777`
- `drop start <name>` → видим creds, side-door warning, tunnel URL
- `curl https://<tunnel>/` → 401
- `curl -u drop:<pass> https://<tunnel>/` → 200
- `drop stop <name>` → app + proxy + tunnel оба умерли (проверить через `ps`)
- `--no-tunnel`: `drop start <name>` → refuse error
- `drop start <name> --auth-insecure` → proxy на 0.0.0.0, cleartext warning, basic auth работает по `http://localhost:<proxy_port>/`
- Upgrade test: WebSocket клиент → 501

После: `/review` (упрощение) + `/security-review` (sec).
