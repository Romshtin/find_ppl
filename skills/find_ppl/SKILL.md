---
name: find_ppl
description: Универсальный разведывательный скилл — собирает посты/комментарии из выбранных источников (telegram, vk, планируются forums) по заданной стратегии поиска. Использовать, когда нужно прогнать разведку: «обойди каналы», «собери телеграм», «обнови архив», «прогони findppl».
---

# find_ppl

## Когда вызывать

Когда пользователь в любом чате Claude Code пишет `/find_ppl` или просит «обойди каналы», «собрать Telegram», «обновить разведку», «прогони findppl», «собери по стратегии X».

## Что делает

Один скрипт делает всё (для одного или нескольких источников):

1. **Поднимает Shadowsocks-мост** (`ss-local.exe`) на `127.0.0.1:1080` — **только для telegram**, VK и форумы работают напрямую
2. **Ждёт готовности** порта (до 10 сек)
3. **Для каждого источника** запускает `py -m scripts.<source>.run_harvest` с подставленной стратегией (harvest + filter)
4. **Гасит мост** через `Stop-Process` по PID (только тот процесс, который сам запустил) — после telegram

## Аргументы от пользователя (парсит Claude)

Когда пользователь вызывает `/find_ppl [что-то]`, **аргументы приходят ко мне как `args`**. Правила парсинга (см. «Инструкция для Claude» ниже):

| Пользователь ввёл | Что запускаем | Источник |
|---|---|---|
| `/find_ppl` (пусто) | `-Source all` | все реализованные |
| `/find_ppl all` / `всё` / `все` | `-Source all` | все реализованные |
| `/find_ppl both` | `-Source both` | telegram + vk (алиас) |
| `/find_ppl vk` / `вк` | `-Source vk` | только VK |
| `/find_ppl tg` / `telegram` / `telegramm` / `телеграм` / `тг` / `телега` | `-Source telegram` | только Telegram |
| `/find_ppl forums` / `forum` / `форум` / `форумы` | `-Source forums` | только форумы |
| Любой другой текст | fallback: `-Source all` + предупреждение | — |

Стратегия: `individual_rel` по умолчанию. Если пользователь явно назвал стратегию (`unusual_places`, `test`, `kurpatov_extended`) — подставь через `-Strategy`. Иначе дефолт.

## Поддерживаемые значения `-Source` (для run.ps1)

| Значение | Что делает |
|---|---|
| `telegram` | Только Telegram (нужен Shadowsocks-мост) |
| `vk` | Только VK (нужны токены в `data/`) |
| `telegram,vk` | Оба источника последовательно |
| `both` | Алиас для `telegram,vk` |
| `all` | Все реализованные источники (исключая алиасы) |
| `forums` | **Зарезервировано** — выдаст понятную ошибку, пока не реализован |

## Инструкция для Claude (как вызвать run.ps1)

**С ОТКЛЮЧЁННЫМ ТУННЕЛЕМ** (CLAUDE.md) — это требование для Telegram-части, VK и форумы работают без туннеля.

**Шаг 1: Распарси `args`.**

Маппинг токенов (case-insensitive, с опечатками):

```python
TOKEN_MAP = {
    # telegram (с опечатками и русским)
    "tg": "telegram", "telegram": "telegram", "telegramm": "telegram",
    "телеграм": "telegram", "тг": "telegram", "телега": "telegram",
    # vk
    "vk": "vk", "вк": "vk",
    # forums
    "forums": "forums", "forum": "forums",
    "форум": "forums", "форумы": "forums",
    # алиасы
    "both": "both",
    "all": "all", "всё": "all", "все": "all",
}
```

Алгоритм:
1. Если `args` пустой → `Source = "all"`.
2. Иначе разбить `args.lower().split()` → пройти по токенам:
   - если токен в `TOKEN_MAP` → добавить маппированное значение в множество `sources`
   - иначе (незнакомый токен) → fallback на `all`, в чате вывести предупреждение «токен X не распознан, использую all»
3. Если найдено несколько токенов (например `/find_ppl vk tg`) → запустить оба.
4. Если `all` в списке — заменить на `telegram,vk` (или вообще на динамический список реализованных).

**Шаг 2: Запусти через Bash tool.**

Команда:
```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\kiril\.claude\skills\find_ppl\run.ps1" -Source <S> -Strategy individual_rel
```

Где `<S>` — то, что получилось после Шага 1 (через запятую если несколько).

**`-ExecutionPolicy Bypass` обязателен** — PowerShell 5.1 не пускает `.ps1` без него (и в файлах должен быть UTF-8 BOM).

**Используй `run_in_background: true`** — harvest занимает 5-10 минут, синхронный вызов занимает токены. Потом читай `TaskOutput` или `Read <output-path>`.

**Шаг 3: Доложи пользователю.**

- Если был fallback на `all` (незнакомый токен) — упомяни в начале.
- В конце — короткий отчёт: сколько источников отработало, exit code, что в `data/*_harvest/filtered/`.

## Как запускать (для пользователя — копипаст)

Из PowerShell, **С ОТКЛЮЧЁННЫМ ТУННЕЛЕМ**:

```powershell
# Всё сразу (telegram + vk, форумы когда появятся)
powershell -ExecutionPolicy Bypass -File "C:\Users\kiril\.claude\skills\find_ppl\run.ps1" -Source all -Strategy individual_rel

# Только VK
powershell -ExecutionPolicy Bypass -File "C:\Users\kiril\.claude\skills\find_ppl\run.ps1" -Source vk -Strategy individual_rel

# Только Telegram
powershell -ExecutionPolicy Bypass -File "C:\Users\kiril\.claude\skills\find_ppl\run.ps1" -Source telegram -Strategy individual_rel

# Оба (алиас)
powershell -ExecutionPolicy Bypass -File "C:\Users\kiril\.claude\skills\find_ppl\run.ps1" -Source both -Strategy individual_rel
```

Или из Claude Code — `/find_ppl` (запустит всё), `/find_ppl vk` (только VK), `/find_ppl tg` (только Telegram), `/find_ppl forums` (только форумы).

## Что нужно для работы

- Проект `findppl` с заполненным `.env` (TG_API_ID, TG_API_HASH, опционально SSLOCAL_EXE/SSLOCAL_CONFIG)
- `C:\Tools\ss-local\sslocal.exe` (deployed) и `ss-config.json` с параметрами Shadowsocks — **только для telegram**
- `py -m pip install -r requirements-telegram.txt` (telethon, python-dotenv, python-socks)
- Для VK: `data/vk_session.json` (PKCE-сессия, обновляется автоматически) + опционально `data/vk_user_token.txt` (для комментариев)
- Стратегия в `data/strategies/<name>.json` (см. `individual_rel.json` для шаблона)

## Если что-то не работает

- **«Корень проекта findppl не найден»** — задайте `FINDPPL_ROOT` в `.env` или откройте Claude Code в директории проекта
- **«Стратегия X не найдена»** — проверьте, что `data/strategies/X.json` существует
- **«ss-local не найден»** — укажите путь в `.env`: `SSLOCAL_EXE=D:\путь\к\sslocal.exe`
- **«Мост не поднялся»** — запустите `ss-local.exe -c ss-config.json` вручную, посмотрите ошибки
- **«harvest завершился с кодом N»** — каналы/группы могут быть недоступны; скрипт сам скипает и идёт дальше
- **«Connection refused» от Telethon** — мост упал во время работы; перезапустите скилл
- **«Источник 'X' не реализован»** — вы указали источник, для которого нет `sources/X.ps1`. Сейчас реализованы: telegram, vk. Зарезервированы: forums.
- **VK error 1051 на `wall.getComments`** — обновите `data/vk_user_token.txt` (раз в ~50 мин) — VK ID SDK токен не даёт доступа к комментариям
- **PowerShell `UnauthorizedAccess`** — запуск без `-ExecutionPolicy Bypass`. Используй полную команду из секции «Как запускать».

## Что НЕ делает

- ❌ Не отправляет сообщения
- ❌ Не подписывается на каналы
- ❌ Не модифицирует файлы стратегий
- ❌ Не анализирует результаты — это делает пользователь вручную в Zed

## Связанные файлы

- `scripts/telegram/config.py` — читает `.env`, `SSLOCAL_EXE`, `SSLOCAL_CONFIG`
- `scripts/telegram/run_harvest.py` — основной скрипт сбора и фильтра (читает стратегию)
- `scripts/telegram/harvest.py` — обход каналов, обработка ошибок
- `scripts/telegram/filter.py` — взвешенная фильтрация по маркерам
- `scripts/vk/auth.py` — PKCE-авторизация VK ID SDK
- `scripts/vk/config.py` — `get_token(for_comments=False)`, автообновление через refresh_token
- `scripts/vk/run_harvest.py` — основной скрипт сбора и фильтра
- `scripts/vk/harvest.py` — обход VK-групп
- `scripts/vk/filter.py` — взвешенная фильтрация по маркерам
- `data/strategies/<name>.json` — каналы/группы и маркеры для стратегии
- `<локальный notes.md по VPN>` — параметры Shadowsocks
- `2026-06-02-search-strategy-kurpatov.md` — стратегия «individual_rel»
- `2026-06-04 — vk-id-pkce-fix — notes.md` — что было сломано в VK ID авторизации и как починили

## Расширение

Добавить новый источник (например, `forums`):
1. Создать `sources/forums.ps1` по шаблону `sources/telegram.ps1`
2. Создать в проекте `scripts/forums/{config,harvest,filter,run_harvest}.py`
3. Добавить в стратегию поле для нового источника (например, `forums: [...]`)
4. Добавить новые токены в `TOKEN_MAP` секции «Инструкция для Claude» этого SKILL.md
5. Запустить `/find_ppl` или `/find_ppl forums` — `-Source all` подхватит автоматически

Добавить новую стратегию:
1. Создать `data/strategies/<name>.json` с полями `channels`, `vk_groups`, `markers`, `min_score`, `output_subdir`
2. Запустить `/find_ppl` (по дефолту) или явно через `run.ps1 -Source all -Strategy <name>`
