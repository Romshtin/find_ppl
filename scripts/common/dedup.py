"""Дедупликация harvest: state-файл с post_id и comment_id.

Хранит `data/<source>_harvest/.seen.json` рядом с raw-каталогом.
Формат:
    {
        "_updated_at": "2026-06-06T22:50:00+00:00",
        "vk:gestalt_program": {
            "posts": [18679, 18678, ...],         # в порядке убывания id (= даты)
            "comments": {
                "18679": [123, 124, 125],
                "18678": []
            }
        },
        "telegram:empathy_rus": { ... }
    }

Ключ верхнего уровня — f"{kind}:{name}": "vk:gestalt_program" / "telegram:empathy_rus".
Используется и в `post["channel"]` (для VK), и формируется аналогично для TG.

Использование:
    from scripts.common.dedup import (
        seen_path, load_seen, save_seen, filter_posts, merge_channel_seen,
    )

    seen = load_seen(seen_path(RAW_DIR))
    # ... harvest_group() / harvest_chat() собирает posts, но seen уже отфильтровал
    # ... merge результата в seen[channel_key] ...
    save_seen(seen_path(RAW_DIR), seen)

По умолчанию поведение skip (повторный harvest за доли секунды, без API).
Флаг --force-full в run_harvest отключает skip на чтение, но seen всё равно обновляется.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def seen_path(raw_dir: Path) -> Path:
    """Путь к .seen.json рядом с raw-каталогом.

    raw_dir = .../data/vk_harvest/raw
    seen    = .../data/vk_harvest/.seen.json
    """
    return raw_dir.parent / ".seen.json"


def load_seen(path: Path) -> dict:
    """Прочитать state. Вернуть пустую структуру при отсутствии/битом JSON.

    Пустая структура:
        {"_updated_at": None, "<channel_key>": {"posts": [], "comments": {}}}
    """
    if not path.exists():
        return {"_updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"_updated_at": None}
        # Минимальная валидация: верхний уровень = dict, блоки = dict
        for k, v in list(data.items()):
            if k == "_updated_at":
                continue
            if not isinstance(v, dict):
                data.pop(k)
        return data
    except (json.JSONDecodeError, OSError):
        return {"_updated_at": None}


def save_seen(path: Path, seen: dict) -> None:
    """Атомарная запись state: сначала .tmp, затем replace.

    Обновляет _updated_at = now(UTC).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    seen["_updated_at"] = datetime.now(timezone.utc).isoformat()
    # .tmp файл в той же директории, что и целевой (для атомарного replace)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".seen.", suffix=".json.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        # Если что-то пошло не так — убрать .tmp
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _seen_set(seen: dict, channel_key: str) -> tuple[set[int], dict]:
    """Достать (set[post_id], comments_map) для канала, создать если нет.

    Возвращает (posts_set, comments_map) — оба изменяемые.
    """
    block = seen.get(channel_key)
    if not isinstance(block, dict):
        block = {"posts": [], "comments": {}}
        seen[channel_key] = block
    posts_list = block.get("posts", [])
    comments_map = block.get("comments", {})
    if not isinstance(comments_map, dict):
        comments_map = {}
        block["comments"] = comments_map
    return set(posts_list), comments_map


def filter_posts(
    channel_key: str, posts: list[dict], seen: dict
) -> list[dict]:
    """Отфильтровать уже-собранные посты, обновить seen[channel_key] in-place.

    На вход:
        channel_key: "vk:gestalt_program" / "telegram:empathy_rus"
        posts: список собранных постов, УЖЕ в порядке убывания id (как API отдаёт)
        seen: текущий state (мутируется in-place)

    На выход:
        список **только новых** постов (для архива или для merge с raw).

    Побочный эффект:
        seen[channel_key] обновлён: posts[] + comments[] добавлены новые id.
        Если пост уже был в seen — пропускаем И его комменты целиком.
    """
    posts_seen, comments_map = _seen_set(seen, channel_key)
    new_posts: list[dict] = []
    new_post_ids: list[int] = []
    for p in posts:
        pid = p.get("post_id")
        if pid is None:
            continue
        if pid in posts_seen:
            # Пост уже был — комменты не загружаем вообще (skip в harvest_*).
            continue
        # Этот пост — новый. Фильтруем его комменты по seen.
        seen_comments_for_post = set(comments_map.get(str(pid), []))
        kept_comments: list[dict] = []
        new_comment_ids: list[int] = []
        for c in p.get("comments", []):
            cid = c.get("comment_id")
            if cid is None or cid in seen_comments_for_post:
                continue
            kept_comments.append(c)
            new_comment_ids.append(cid)
        p["comments"] = kept_comments
        new_posts.append(p)
        new_post_ids.append(pid)
        # Обновить comments_map для этого поста (сразу, не дожидаясь merge)
        if new_comment_ids:
            existing = comments_map.get(str(pid), [])
            comments_map[str(pid)] = list(existing) + new_comment_ids
        else:
            # Закрепить факт "у поста точно нет комментов" (если comments_count=0)
            comments_map.setdefault(str(pid), [])
        # Пост теперь seen (для следующих итераций)
        posts_seen.add(pid)
    # Обновить posts[] канала (с учётом возможных дублей)
    block = seen[channel_key]
    block["posts"] = sorted(set(block.get("posts", [])) | set(new_post_ids), reverse=True)
    return new_posts


def merge_channel_seen(existing_block: dict, new_block: dict) -> dict:
    """Склеить seen-блок одного канала: сохраняем все известные id, сортируем.

    На вход:
        existing_block: {"posts": [...], "comments": {<pid>: [cid, ...]}}
        new_block:      {"posts": [...], "comments": {<pid>: [cid, ...]}}

    На выход: объединённый блок. Используется в run_harvest после harvest_*
    (где harvest_* мог собрать не всё, а только новую порцию).
    """
    posts = sorted(
        set(existing_block.get("posts", [])) | set(new_block.get("posts", [])),
        reverse=True,
    )
    comments_existing = existing_block.get("comments", {})
    comments_new = new_block.get("comments", {})
    merged_comments: dict[str, list[int]] = {}
    all_pids = set(comments_existing.keys()) | set(comments_new.keys())
    for pid in all_pids:
        ids = sorted(
            set(comments_existing.get(pid, [])) | set(comments_new.get(pid, [])),
            reverse=True,
        )
        merged_comments[pid] = ids
    return {"posts": posts, "comments": merged_comments}
