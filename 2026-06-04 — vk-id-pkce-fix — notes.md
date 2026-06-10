# 2026-06-04 — VK ID SDK: починили PKCE-флоу — notes

## TL;DR

`scripts/vk/auth.py` сломан был не из-за «бага VK с unpkg.com» — а потому что
новая версия VK ID SDK (1.1.1348, грузится с `static.vk.com/vkid/...`) **требует
PKCE** (code_verifier + code_challenge=S256) обязательно. Без них страница
авторизации падает в "Ошибка загрузки" с консольной ошибкой
`code_challenge or code_challenge_method is invalid`. Плюс `state` был 22
символа, нужно ≥ 32. Плюс UX: юзер копировал только `code`, путался с
`state` и `device_id`.

Починили: переписали `auth.py` под OAuth 2.1 с PKCE, попросили вставлять
**полный URL** после редиректа (скрипт сам парсит). 5 unit-тестов прошли.

---

## Что было

### Симптом

```
GET https://id.vk.com/authorize?client_id=54622669&response_type=code&...
→ HTTP 200, JS грузится с static.vk.com/vkid/1.1.1348/authorize.js
→ Консоль: {code: invalid_request, message: "code_challenge or code_challenge_method is invalid"}
→ UI: "Ошибка загрузки. Попробуйте ещё раз. Если не получится сейчас, то чуть позже."
```

### Корень

В новой версии SDK (с июня 2025+) VK форсирует PKCE для всех public-клиентов.
Старая версия (3.0.0 с unpkg.com) у них сломалась давно, но **страница
авторизации не падала** — просто UI глючил. Теперь они переехали на свой CDN
(`static.vk.com/vkid/1.1.1348/`) и параллельно включили обязательную
проверку PKCE в authorize.js.

### Проверка

- `curl https://unpkg.com/@vkid/sdk@3.0.0/dist-sdk/umd/index.js` → **HTTP 404** (как раньше)
- `curl https://unpkg.com/@vkid/sdk/dist-sdk/umd/index.js` → **HTTP 302** (правильный путь живой)
- `curl https://id.vk.com/authorize?client_id=54622669&...` → **HTTP 200, 186 KB** (страница грузится, JS с static.vk.com, но без PKCE падает)
- В Playwright (без туннеля) — то же самое: "Ошибка загрузки" + invalid_request в консоли
- `https://id.vk.com/about/business/go/.../apps/54622669/edit (link omitted — содержит ID аккаунта владельца)` — приложение живо, статус «включено и видно всем», Redirect URL `https://oauth.vk.com/blank.html` стоит

### App ID

- `client_id=54622669` — живо, name `findppl`, platform `Web`
- Владелец: `accounts/<id> (без раскрытия)` ([владелец])
- HTML-страница авторизации содержит `"name":"DELETED"` в JSON-конфиге — это **визуальный глюк VK в новом SDK**, не реальное удаление. Подтверждено в кабинете.

### Дополнительно

- `data/vk_user_token.txt` — протух (`access_token has expired`, error 5)
- `data/vk_app_secret.txt` — `lLs6C3kQ…b5Cu` (20 символов, валидный)
- `data/vk_session.json` — отсутствует (ещё не создавали)

---

## Что починили

### `scripts/vk/auth.py`

1. **Добавили PKCE**:
   - `_pkce_pair()` → `(code_verifier, code_challenge)` через SHA256
   - `_build_auth_url()` теперь принимает `code_challenge` и добавляет
     `code_challenge_method=S256`
   - `_exchange_code_for_tokens()` шлёт `code_verifier` в payload

2. **Длина state**: `secrets.token_urlsafe(32)` (43 символа, было 22)

3. **UX**: `main()` теперь просит вставлять **полный URL**
   `https://oauth.vk.com/blank.html?code=...&state=...&device_id=...`,
   парсит сам через `_parse_callback()`, сверяет `state`, возвращает
   `code` и `device_id` от VK (не тот, что мы генерили — VK может
   его поменять при redirect chain, и тогда refresh не сработает).

4. **Fallback**: если юзер вставит только code (без URL) — работает, но
   device_id берём тот, что отправляли.

5. **Обработка ошибок**:
   - `invalid_grant` → «code протух (10 минут) или уже использован»
   - `invalid_request` → «проверьте PKCE»
   - `code_challenge` обязателен

6. **`_save_session()`**: добавили поле `obtained_via: "vk_id_sdk_pkce"` —
   чтобы в будущем отличать от возможных других флоу.

### Что НЕ меняли

- `scripts/vk/config.py` — `_refresh_access_token()` уже соответствует
  докам VK OAuth 2.1 (POST `grant_type=refresh_token` + `client_secret` +
  `device_id` + `state`). Не трогаем.
- `scripts/vk/harvest.py` — `get_token()` уже вызывается правильно.
- `scripts/vk/filter.py`, `scripts/vk/run_harvest.py` — без правок.
- `.env` — `VK_APP_ID=54622669` корректен.
- `data/vk_app_secret.txt` — валидный, не трогаем.
- `data/vk_user_token.txt` — пусть лежит как fallback (но не удалять —
  `config.py:get_token()` имеет приоритет session > user_token > service).

---

## Верификация

### Unit-тесты (5/5 passed)

```
PKCE: verifier=86ch, challenge=43ch, OK
URL build: PKCE params present, OK
Callback parse: OK
State mismatch protection: OK
Missing code protection: OK
```

### Что нужно проверить руками (Роман)

**С ОТКЛЮЧЁННЫМ ТУННЕЛЕМ:**

1. `py -m scripts.vk.auth` → откроется URL в браузере → залогиниться →
   скопировать ПОЛНЫЙ URL из адресной строки после редиректа на
   `https://oauth.vk.com/blank.html?code=...&state=...&device_id=...` →
   вставить → `[OK] Сессия сохранена: data/vk_session.json`

2. `py -c "from scripts.vk.config import has_id_session, get_token; print(has_id_session(), get_token()[:30])"`
   → `True vk1.a.…` (или похоже)

3. `py -m scripts.vk.run_harvest both --strategy individual_rel`
   → `[TOKEN] source: VK ID (refresh_token)` + новые файлы в
   `data/vk_harvest/raw/`

4. Тест автообновления (форсировать истечение):
   ```powershell
   copy data\vk_session.json data\vk_session.bak.json
   py -c "import json,pathlib; p=pathlib.Path(r'D:\СС\IdeaProjects\findppl\data\vk_session.json'); s=json.loads(p.read_text(encoding='utf-8')); s['expires_at']='2026-06-01T00:00:00+00:00'; p.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')"
   py -m scripts.vk.run_harvest both --strategy individual_rel
   # Проверить: expires_at снова в будущем
   ```

---

## Полезные ссылки

- [Реализация OAuth 2.1 в VK ID (PKCE обязателен)](https://id.vk.com/about/business/go/docs/ru/vkid/latest/vk-id/connection/realization)
- [Авторизация без SDK для Web](https://id.vk.com/about/business/go/docs/ru/vkid/latest/vk-id/connection/start-integration/auth-without-sdk/auth-without-sdk-web)
- [Как работает авторизация VK ID на Web](https://id.vk.com/about/business/go/docs/ru/vkid/latest/vk-id/connection/start-integration/how-auth-works/auth-flow-web)
- [Справочник методов API VK ID](https://id.vk.com/about/business/go/docs/ru/vkid/latest/vk-id/connection/api-integration/api-description)
- [RFC 7636 — Proof Key for Code Exchange (PKCE)](https://datatracker.ietf.org/doc/html/rfc7636)

---

## Timeline

- **2026-05-31 (предыдущие дни)**: проблема — «auth.py не работает», ошибочно
  списывали на `unpkg.com` → 404.
- **2026-06-04**: диагностика через curl + Playwright → выяснили, что
  `unpkg.com`-баг побочно ушёл (VK переехал на static.vk.com), но появилась
  **новая обязательная проверка PKCE**. Починили за один проход.
