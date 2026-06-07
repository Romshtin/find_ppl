# find_ppl

Разведывательный скилл для Claude Code: собирает посты и комменты из Telegram и VK, фильтрует по взвешенным маркерам и складывает в JSON-архив, пригодный для ручного разбора в Obsidian.

Идея — поиск 1–2 человек для редких встреч «без ролей, без масок» (концепция из «Индивидуальных отношений» А. Курпатова). Реализация — пайплайн `harvest → filter → flat archive`.

---

## Что внутри

```
find_ppl/
├── scripts/
│   ├── common/                    # общая логика маркеров и дедупликации
│   ├── telegram/                  # Telethon-клиент, harvest, filter, resolve
│   └── vk/                        # VK API-клиент, harvest, filter, auth, resolve
├── skills/
│   └── find_ppl/                  # PowerShell-скилл для Claude Code
├── data/
│   ├── strategies/                # JSON-стратегии (каналы, группы, маркеры)
│   ├── telegram_harvest/{raw,filtered}/
│   └── vk_harvest/{raw,filtered}/
├── .env                           # секреты (НЕ в репе)
├── .gitignore
├── CLAUDE.md                      # заметки для Claude Code
└── requirements-telegram.txt
```

PowerShell-скилл `find_ppl` (для Claude Code) лежит в этом репо в
`skills/find_ppl/`. Чтобы активировать его в Claude Code, скопируйте
папку в `~/.claude/skills/find_ppl/` (для Windows —
`C:\Users\<user>\.claude\skills\find_ppl\`). Скилл оборачивает
Python-скрипты в единый orchestrator: подъём SOCKS-моста → harvest →
filter → гашение моста. Подробности — в `skills/find_ppl/SKILL.md`.

---

## ⚠️ Что НЕ будет работать из коробки

В `.gitignore` сознательно вынесены **секреты, сессии и сырые архивы** —
без них пайплайн не запустится. Вот что нужно подготовить, чтобы
получить рабочую копию.

### Telegram: нужна инфраструктура

| Компонент | Зачем | Где взять |
|---|---|---|
| **VPS с Shadowsocks-сервером** | Мост `ss-local.exe → VPS:8388` для обхода блокировки Telegram | Свой VPS (Hetzner/OVH/…). Поднять `shadowsocks-rust` или `shadowsocks-libev` на UDP-порту |
| **`ss-local.exe`** | Локальный SOCKS5-мост на `127.0.0.1:1080` | Бинарь Shadowsocks-клиента под Windows: [shadowsocks/shadowsocks-windows](https://github.com/shadowsocks/shadowsocks-windows) (архивные релизы) или `shadowsocks-rust` |
| **`ss-config.json`** | Конфиг моста с адресом VPS и паролем | Сгенерировать по документации Shadowsocks, положить рядом с `sslocal.exe` |
| **`TG_API_ID` / `TG_API_HASH`** | Идентификатор приложения Telegram | [my.telegram.org](https://my.telegram.org) → «API development tools» → создать приложение |
| **`TG_SESSION_NAME`** (опц.) | Имя `.session`-файла Telethon. По умолчанию `findppl_session` | — |
| **`SSLOCAL_EXE` / `SSLOCAL_CONFIG`** (опц.) | Пути к мосту в `.env`. По умолчанию `C:\Tools\ss-local\…` | Если мост лежит в другом месте — переопределить |

**Запуск ТОЛЬКО с ОТКЛЮЧЁННЫМ WireGuard-туннелем** — иначе `ss-local` и
активный туннель конфликтуют за маршрутизацию.

### VK: нужны токены

| Компонент | Зачем | Где взять | Срок жизни |
|---|---|---|---|
| **`VK_APP_ID`** | ID Standalone-приложения VK | [vk.com/editapp](https://vk.com/editapp) → создать | бессрочно |
| **`VK_SERVICE_TOKEN`** | Сервисный ключ приложения | Там же, в настройках | бессрочно |
| **`VK_API_VERSION`** | Версия VK API (дефолт `5.199`) | [dev.vk.com](https://dev.vk.com) | — |
| **`data/vk_app_secret.txt`** | Защищённый ключ для `refresh_token`-flow | В настройках приложения (20 символов вида `lLs6C3kQ…b5Cu`) | бессрочно |
| **`data/vk_user_token.txt`** | Пользовательский токен — единственный, что даёт доступ к комментариям (VK API error 1051 для остальных) | Вручную из DevTools: открыть `vk.com`, F12 → Network → фильтр `api.vk.com` → скопировать `access_token=vk1.a.…` из URL | **~1 час** |
| **`data/vk_session.json`** | PKCE-сессия VK ID SDK с `refresh_token` (живёт 1 год) | `py -m scripts.vk.auth` — потребует ручного логина в браузере и вставки `code` | 1 год, автообновление |

> ⚠️ VK в любой момент может сломать страницу `id.vk.com/authorize`
> (такое уже было). Если `auth.py` падает с «Ошибка загрузки» — это баг
> на стороне VK, не ваш. Используйте `user_token` как fallback.

### Стратегия поиска

`data/strategies/individual_rel.json` — рабочий пример: 4 Telegram-канала,
7 VK-групп, 19 взвешенных маркеров. Под свои задачи создайте свой JSON
по тому же шаблону (поля `channels`, `vk_groups`, `markers`, `min_score`,
`output_subdir`).

---

## Установка

```bash
# 1. Клонировать
git clone <repo> find_ppl && cd find_ppl

# 2. Python-зависимости (Telegram-часть)
py -m pip install -r requirements-telegram.txt
# VK использует requests + dotenv, идут транзитивно
```

Заполните `.env` (см. таблицу выше) и положите токены в `data/`. Затем:

```bash
# Первая авторизация VK (откроется браузер → вставить code)
py -m scripts.vk.auth

# Проверить, что токен подхватился
py -c "from scripts.vk.config import get_token; print('token len =', len(get_token()))"
```

---

## Запуск

### Из PowerShell (напрямую, без скилла)

```powershell
# Только VK
powershell -ExecutionPolicy Bypass -File "C:\Users\<user>\.claude\skills\find_ppl\run.ps1" -Source vk -Strategy individual_rel

# Только Telegram
powershell -ExecutionPolicy Bypass -File "C:\Users\<user>\.claude\skills\find_ppl\run.ps1" -Source telegram -Strategy individual_rel

# Оба
powershell -ExecutionPolicy Bypass -File "C:\Users\<user>\.claude\skills\find_ppl\run.ps1" -Source both -Strategy individual_rel
```

**`-ExecutionPolicy Bypass` обязателен** — иначе PowerShell 5.1 не
пускает `.ps1`.

### Из Claude Code

```
/find_ppl              # все источники
/find_ppl vk           # только VK
/find_ppl tg           # только Telegram
/find_ppl both         # telegram + vk
```

### Напрямую Python-модулем

```bash
py -m scripts.vk.run_harvest both --strategy individual_rel
py -m scripts.telegram.run_harvest both --strategy individual_rel
```

Подкоманды: `both` (harvest + filter), `harvest`, `filter`, `resolve`
(ленивое дотягивание профилей авторов).

---

## Куда складываются результаты

```
data/telegram_harvest/
  raw/                   # сырые посты/комменты (НЕ в репе, в .gitignore)
  filtered/              # прошедшие маркерный фильтр (тоже .gitignore)

data/vk_harvest/
  raw/
  filtered/
```

Формат `filtered/*.json` (компактный, читается в Obsidian без скролла):

```jsonc
{
  "group_url": "https://t.me/empathy_rus",
  "entries": [
    { "_score": 3, "author_url": "https://t.me/ivan_petrov", "text": "..." },
    { "_score": 2, "author_url": "Группа",                  "text": "..." }
  ]
}
```

- `author_url` = реальная ссылка на автора, **или** строка `"Группа"`
  (пост от канала), **или** пусто (пост из хештега, отсеян до попадания
  в архив).
- Сортировка: настоящая ссылка → `"Группа"` → пусто; внутри — по
  `_score` убыванию.

---

## Стек

- **Telegram:** [Telethon](https://docs.telethon.dev/) ≥ 1.36,
  проксирование через [python-socks](https://pypi.org/project/python-socks/)
  (НЕ PySocks — имя модуля `python_socks`).
- **VK:** `requests` + `python-dotenv`. Авторизация — PKCE-flow VK ID SDK
  с автообновлением по `refresh_token`.
- **Мост:** [shadowsocks-rust](https://github.com/shadowsocks/shadowsocks-rust)
  (или `shadowsocks-libev`) как SOCKS5-сервер на `127.0.0.1:1080`.
- **Skill runtime:** PowerShell 5.1 (Windows). UTF-8 BOM для `.ps1`
  обязателен.

---

## Документация

- [CLAUDE.md](CLAUDE.md) — заметки для Claude Code: VPN-правила, .env,
  частые ошибки
- Файлы в корне:
  `<локальный notes.md по VPN>` — параметры Shadowsocks-моста
  `2026-06-02-search-strategy-kurpatov.md` — стратегия «individual_rel»

---

## Известные ограничения

| Проблема | Статус |
|---|---|
| VK ID SDK `id.vk.com/authorize` падает с «Ошибка загрузки» | Баг на стороне VK (битая ссылка на CDN `unpkg.com/@vkid/sdk@3.0.0/...`). Fallback — user_token вручную |
| `user_token` живёт 1 час | Руками обновлять через DevTools. Автоматизация запрещена ToS VK (риск бана) |
| Telegram-мост конфликтует с WireGuard | Запускать с отключённым туннелем |
| Дедупликация **не** реализована для `harvest` | Каждый прогон перезаписывает `raw/`. Архив растёт только через `filter` |
| Форумы | Зарезервировано в стратегии (`forums: []`), шаблон источника `sources/forum.ps1` ждёт реализации |

---

## Лицензия

MIT.
