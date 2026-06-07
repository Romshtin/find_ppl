"""Общая логика маркеров для всех источников (Telegram, VK, форумы).

Стратегия — это файл data/strategies/<name>.json:
  {
    "channels": [...],            # для Telegram
    "vk_groups": [...],           # для VK
    "forums": [...],              # для форумов (будущее)
    "markers": [{"pattern": "...", "weight": N}, ...],
    "min_score": N,
    "output_subdir": "..."
  }

Используется в scripts/telegram/filter.py и scripts/vk/filter.py.
"""
import json
import re
from pathlib import Path

# Корень проекта: scripts/common/ -> scripts/ -> корень
ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_DIR = ROOT / "data" / "strategies"

# Метка для случаев, когда у автора нет персональной ссылки.
# Сейчас в данных это только «пост от имени канала/группы» — используем
# всегда, когда author_url == group_url или author_url пустой. «Аноним»
# больше не пишем: если у записи нет автора-юзера, она = канал/группа.
LABEL_GROUP = "Группа"


def has_author_url(entry) -> int:
    """0/1/2 для сортировки: 2 = настоящая кликабельная ссылка (https://…,
    tg://…); 1 = человекочитаемая метка (Группа/Аноним); 0 = пусто.

    Принимает dict ИЛИ уже вычисленный tuple (для безопасности sorted() —
    она вызывает key() повторно при ties, и наш sort_key_entry
    делегирует в has_author_url, поэтому функция должна быть
    устойчива к tuple на входе).
    """
    if isinstance(entry, dict):
        url = entry.get("author_url", "")
    else:
        # tuple или что-то ещё — нечего извлекать, трактуем как пусто.
        return 0
    if not url or not str(url).strip():
        return 0
    s = str(url).strip()
    if s.startswith(("http://", "https://", "tg://")):
        return 2
    return 1


def is_link(value) -> bool:
    """Является ли значение author_url кликабельной ссылкой."""
    if not value or not str(value).strip():
        return False
    return str(value).strip().startswith(("http://", "https://", "tg://"))


def sort_key_entry(entry: dict) -> tuple:
    """Ключ сортировки: (link_priority desc, _score desc).

    Используется с reverse=True. Приоритеты:
      2 — настоящая ссылка (https://…, tg://…)
      1 — метка (Группа / Аноним)
      0 — пусто
    Внутри каждого приоритета score DESC.
    """
    try:
        score = int(entry["_score"])
    except (KeyError, TypeError, ValueError):
        score = 0
    return (has_author_url(entry), score)


def _is_meaningful_text(text: str) -> bool:
    """False, если пост — «пустышка»: только хештеги, эмодзи или 1-2 слова.

    Условия «осмысленности»:
      - длина после strip > 30 символов И
      - буквенных символов (кириллица/латиница) >= 20.
    Это отсеивает '#эмпатияцитаты', '#психология #отношения' и подобное,
    где маркер случайно сработал на слове «эмпатия» внутри хештега.
    """
    t = (text or "").strip()
    if len(t) <= 30:
        return False
    letters = sum(ch.isalpha() for ch in t)
    return letters >= 20


def _author_label(author_url: str, group_url: str) -> str:
    """Превратить author_url в то, что попадёт в JSON.

    Логика:
      - author_url == group_url  →  "Группа"   (пост от имени канала/группы)
      - author_url == ""         →  "Группа"   (пост от канала, у которого
                                              group_url вычислился; у нас
                                              в данных такого больше не
                                              бывает, но оставляем на
                                              всякий случай)
      - author_url — настоящая   →  оставляем как есть (https://..., tg://...)
    """
    if not author_url or not str(author_url).strip():
        return LABEL_GROUP
    s = str(author_url).strip()
    if group_url and s == group_url:
        return LABEL_GROUP
    return s


def flatten_and_sort(kept_posts: list[dict], group_url: str = "") -> list[dict]:
    """Расплющить иерархию posts[].comments[] в плоский entries[].

    Каждый post и каждый comment становятся entry. Сортировка —
    sort_key_entry (link_priority desc, score desc).

    Перед сортировкой фильтруем «пустышки» (только хештеги / 1-2 слова):
    такие записи проходят маркерный скор, но нечего читать.

    Возвращаются МИНИМАЛЬНЫЕ поля: _score, author_url, text.
    В author_url подставляются метки:
      "Группа"  — author_url совпал с group_url (пост от имени канала)
    Иначе оставляется как есть (https://t.me/..., tg://user?id=..., vk.com/...).

    group_url нужен, чтобы отличить «автор = группа» от «автор = юзер
    с совпадающей ссылкой» (на практике не встречается, но логика та же).
    """
    entries: list[dict] = []
    for post in kept_posts:
        text = post.get("text", "")
        if _is_meaningful_text(text):
            entries.append({
                "_score": post.get("_score", 0),
                "author_url": _author_label(post.get("author_url", ""), group_url),
                "text": text,
            })
        for c in post.get("comments", []):
            ctext = c.get("text", "")
            if _is_meaningful_text(ctext):
                entries.append({
                    "_score": c.get("_score", 0),
                    "author_url": _author_label(c.get("author_url", ""), group_url),
                    "text": ctext,
                })
    entries.sort(key=sort_key_entry, reverse=True)
    return entries


def build_group_url(data: dict) -> str:
    """Ссылка на сам канал/группу, откуда пришёл архив.

    Telegram: канал указан username'ом ('empathy_rus') → t.me/<username>.
              Если канал недоступен или username скрыт, поле 'channel'
              может быть пустым — тогда group_url тоже пустая строка.
    VK:      канал указан screen_name ('gestalt.today') — берём
              первый пост: from_id < 0 означает сообщество,
              → vk.com/public<abs(from_id)>.
              Если постов нет или from_id неотрицательный — пустая строка.
    """
    channel = data.get("channel", "")
    if channel.startswith("vk:"):
        # VK: ищем первый пост с from_id < 0.
        for p in data.get("posts", []):
            fid = p.get("from_id") if "from_id" in p else p.get("author_id")
            if isinstance(fid, int) and fid < 0:
                return f"https://vk.com/public{abs(fid)}"
        return ""
    if channel:
        # Telegram: это username (или short-name) канала.
        return f"https://t.me/{channel}"
    return ""


def load_strategy(name: str | None) -> dict | None:
    """Загрузить стратегию из data/strategies/<name>.json.

    Возвращает None, если name пустой или файл не найден.
    В возвращённом dict добавляется ключ '_compiled' — список
    (re.Pattern, weight) для быстрого скоринга.
    """
    if not name:
        return None
    path = STRATEGIES_DIR / f"{name}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_compiled"] = [
        (re.compile(m["pattern"], re.IGNORECASE | re.UNICODE), int(m.get("weight", 1)))
        for m in data.get("markers", [])
    ]
    return data


# Фолбэк на случай, если стратегия не указана: 19 маркеров из старой версии.
_LEGACY_MARKERS = [
    r"\bбез\s+рол",
    r"\bбез\s+маск",
    r"\bмаск[аиуы]?\b",
    r"\bприсутств",
    r"\bэмпати",
    r"\bодиночеств\w*\s+(среди|внутри|между)",
    r"\bнастоящ\w+\s+я",
    r"\bсущност",
    r"\bпаттерн",
    r"\bКурпатов",
    r"\bэкзистенциал",
    r"\bфеноменолог",
    r"\bгештальт",
    r"\bхеллингер",
    r"\bвзаимн\w+\s+присутств",
    r"\bувидеть\s+настоящ",
    r"\bролев\w+\s+общен",
    r"\bсущностн\w+\s+контакт",
    r"\bиндивидуальн\w+\s+отношен",
]
_LEGACY_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in _LEGACY_MARKERS
]


def score_legacy(text: str) -> tuple[int, list[str]]:
    """Без стратегии: счёт = число совпавших паттернов, вес = 1."""
    hits = [p.pattern for p in _LEGACY_PATTERNS if p.search(text)]
    return len(hits), hits


def score_weighted(text: str, strategy: dict) -> tuple[int, list[dict]]:
    """Со стратегией: счёт = сумма весов совпавших маркеров."""
    hits: list[dict] = []
    total = 0
    for pattern, weight in strategy["_compiled"]:
        if pattern.search(text):
            hits.append({"pattern": pattern.pattern, "weight": weight})
            total += weight
    return total, hits
