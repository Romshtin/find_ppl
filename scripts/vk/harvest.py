"""Сбор постов и комментариев из VK-групп через VK API.

Использует wall.get (посты на стене) + wall.getComments (комментарии к постам).
Токен — пользовательский (data/vk_user_token.txt), чтобы читал и комментарии.

Дедупликация: при наличии data/vk_harvest/.seen.json новые посты
определяются по post_id, при их отсутствии цикл wall.get прерывается
(early break) — повторный harvest занимает секунды, а не минуты.
"""
import time
from datetime import datetime, timezone

import requests

from scripts.common.dedup import (
    filter_posts,
    load_seen,
    merge_channel_seen,
    save_seen,
    seen_path,
)

from .config import API_VERSION, RAW_DIR, get_token, has_id_session

API_BASE = "https://api.vk.com/method"
POSTS_PER_GROUP = 100
# Ограничение по комментариям на пост. ВАЖНО: при --force-full seen
# обновляется ВСЕМ собранным id, а реально стянуто <= COMMENTS_PER_POST
# (VK API режет ответ на 100). В итоге посты с хвостом > 100 комментов
# запоминаются как "полностью собранные", хотя хвост не дотянут. Для
# разведки не критично, но при --force-full с популярными группами
# имейте в виду: вторичный прогон уже не дотянет хвост — он помечен
# seen'ом. Если нужен полный архив — увеличьте лимит или снимайте
# посты с seen вручную.
COMMENTS_PER_POST = 100
INTER_GROUP_PAUSE_SEC = 2


def _build_author_url(from_id: int | None) -> str:
    """Ссылка на профиль VK по числовому from_id.

    from_id > 0 — пользователь: https://vk.com/id<id>
    from_id < 0 — сообщество: https://vk.com/public<abs(id)>
    from_id = None / 0 — пустая строка (пост сообщества без явного автора).
    """
    if not from_id:
        return ""
    if from_id > 0:
        return f"https://vk.com/id{from_id}"
    return f"https://vk.com/public{abs(from_id)}"


def _build_author_fields(from_id: int | None) -> dict:
    """Блок автора поста/комментария: author_id, author_url. _resolved=False —
    имя/аватар подтянет scripts.vk.resolve через users.get.
    """
    return {
        "author_id": from_id,
        "author_username": None,
        "author_name": None,
        "author_url": _build_author_url(from_id),
        "author_photo": None,
        "_resolved": False,
    }


def _vk_call(method: str, params: dict) -> dict:
    """Вызов VK API с обработкой rate limits.

    Для wall.getComments используется user_token (нужен для обхода
    error 1051, который VK ID SDK токен отдаёт на этом методе).
    """
    for_comments = method == "wall.getComments"
    params = {**params, "access_token": get_token(for_comments=for_comments), "v": API_VERSION}
    r = requests.get(f"{API_BASE}/{method}", params=params, timeout=30)
    data = r.json()
    if "error" in data:
        err = data["error"]
        if err.get("error_code") == 6:  # Too many requests
            wait = int(err.get("retry_after", 1))
            print(f"[FLOOD] VK: ждем {wait} сек...")
            time.sleep(wait)
            return _vk_call(method, params)
        raise RuntimeError(f"VK API error in {method}: {err}")
    return data["response"]


def _resolve_group_id(screen_name: str) -> int:
    """Преобразовать короткое имя группы (например, 'psysovet') в numeric id.
    Бросает RuntimeError, если группа не найдена.

    В новом VK API groups.getById возвращает {"groups": [...], "profiles": []}.
    """
    resp = _vk_call("groups.getById", {"group_id": screen_name})
    groups = resp.get("groups", []) if isinstance(resp, dict) else resp
    if not groups:
        raise RuntimeError(f"Группа не найдена: {screen_name}")
    return -abs(groups[0]["id"])  # owner_id для wall.get: <0 = группа


def harvest_group(
    screen_name: str, seen: dict | None = None, channel_key: str | None = None
) -> dict | None:
    """Собрать посты и комментарии одной VK-группы.

    screen_name — короткое имя (без @ и без https://vk.com/).
    seen — текущий state (мутируется in-place: добавляются новые post_id/comment_id).
           Если None — skip-логика отключена (для --force-full).
    channel_key — ключ для seen (например "vk:gestalt_program"). Если None —
                  берётся "vk:<screen_name>".

    Возвращает dict с posts/comments (только НОВЫМИ, если seen не None) или
    None, если группа недоступна.
    """
    try:
        owner_id = _resolve_group_id(screen_name)
    except RuntimeError as e:
        print(f"[SKIP] {screen_name}: {e}")
        return None

    if channel_key is None:
        channel_key = f"vk:{screen_name}"
    # Сет seen-post_id для O(1) проверки
    seen_post_ids: set[int] = set()
    if seen is not None:
        # Создать пустой блок, если канал новый (нужно для доступа к comments)
        if channel_key not in seen or not isinstance(seen.get(channel_key), dict):
            seen[channel_key] = {"posts": [], "comments": {}}
        block = seen[channel_key]
        seen_post_ids = set(block.get("posts", []))

    print(f"[GROUP] {screen_name} -> owner_id={owner_id}")
    posts = []
    offset = 0
    while offset < POSTS_PER_GROUP:
        try:
            resp = _vk_call("wall.get", {
                "owner_id": owner_id,
                "count": min(20, POSTS_PER_GROUP - offset),
                "offset": offset,
            })
        except RuntimeError as e:
            print(f"[ERR] {screen_name}: {e}")
            return None

        items = resp.get("items", [])
        if not items:
            break
        # Early break: если самый новый пост из items уже seen — всё дальше точно seen.
        if (
            seen is not None
            and items
            and items[0].get("id") in seen_post_ids
        ):
            print(
                f"[DEDUP] {screen_name}: latest post {items[0]['id']} уже seen, "
                f"early break (offset={offset})"
            )
            break
        for p in items:
            pid = p.get("id")
            # Доп. защита: если API пропустил ID и встретился known пост в середине
            if seen is not None and pid in seen_post_ids:
                continue
            text = p.get("text", "")
            if not text:
                continue  # пропускаем репосты и чисто медийные
            author = _build_author_fields(p.get("from_id"))
            post = {
                "channel": f"vk:{screen_name}",
                "post_id": pid,
                "date": datetime.fromtimestamp(
                    p["date"], tz=timezone.utc
                ).isoformat() if p.get("date") else None,
                "text": text,
                "views": p.get("views", {}).get("count", 0),
                "likes": p.get("likes", {}).get("count", 0),
                "reposts": p.get("reposts", {}).get("count", 0),
                "comments_count": p.get("comments", {}).get("count", 0),
                "comments": [],
            }
            post.update(author)
            # Если пост подписан админом сообщества — отдельная ссылка.
            signer = p.get("signer_id")
            if signer:
                post["signed_by"] = _build_author_url(signer)
            # Комментарии к посту (если есть)
            if post["comments_count"] > 0:
                try:
                    comments = _fetch_comments(
                        owner_id, pid,
                        skip_ids=(
                            set(seen[channel_key].get("comments", {}).get(str(pid), []))
                            if seen is not None else None
                        ),
                    )
                    post["comments"] = comments
                except RuntimeError as e:
                    post["comments_error"] = str(e)
            posts.append(post)
            seen_post_ids.add(pid)
        offset += len(items)
        time.sleep(0.5)  # бережем rate limit

    # Зафиксировать собранное в seen (если включён skip-режим)
    if seen is not None:
        # posts[]: добавить новые id к существующему списку, отсортировать
        existing_posts = set(seen[channel_key].get("posts", []))
        existing_posts |= seen_post_ids
        seen[channel_key]["posts"] = sorted(existing_posts, reverse=True)
        # comments[] для постов без комментов зафиксировать как []
        for p in posts:
            pid_str = str(p["post_id"])
            existing_comments = set(
                seen[channel_key].get("comments", {}).get(pid_str, [])
            )
            for c in p.get("comments", []):
                if c.get("comment_id") is not None:
                    existing_comments.add(c["comment_id"])
            seen[channel_key].setdefault("comments", {})[pid_str] = sorted(
                existing_comments, reverse=True
            )

    return {
        "channel": f"vk:{screen_name}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }


def _fetch_comments(
    owner_id: int, post_id: int, skip_ids: set[int] | None = None
) -> list[dict]:
    """Скачать комментарии к посту. До COMMENTS_PER_POST штук.

    skip_ids — comment_id, которые уже есть в seen (для инкрементального harvest).
    При первом комменте из skip_ids цикл прерывается (early break по id).
    """
    if skip_ids is None:
        skip_ids = set()
    comments = []
    offset = 0
    while offset < COMMENTS_PER_POST:
        resp = _vk_call("wall.getComments", {
            "owner_id": owner_id,
            "post_id": post_id,
            "count": min(50, COMMENTS_PER_POST - offset),
            "offset": offset,
            "extended": 0,
        })
        items = resp.get("items", [])
        if not items:
            break
        # Early break: первый коммент уже seen — всё остальное тоже
        if items[0].get("id") in skip_ids:
            break
        for c in items:
            cid = c.get("id")
            # Защита от дыр в API: если коммент уже seen — пропускаем
            if cid in skip_ids:
                continue
            text = c.get("text", "")
            if not text:
                continue
            author = _build_author_fields(c.get("from_id"))
            comments.append({
                "comment_id": cid,
                "date": datetime.fromtimestamp(
                    c["date"], tz=timezone.utc
                ).isoformat() if c.get("date") else None,
                "text": text,
                "likes": c.get("likes", {}).get("count", 0),
                **author,
            })
        offset += len(items)
        if len(items) < 50:
            break  # меньше страницы — больше нет
        time.sleep(0.3)
    return comments


def harvest_all(screen_names: list[str], force_full: bool = False) -> list[str]:
    """Собрать список групп. Сохраняет каждую в data/vk_harvest/raw/<name>.json.
    Возвращает список успешно собранных групп.

    force_full=False (по умолчанию): пропускает уже seen-посты (инкрементальный harvest).
    force_full=True: пересобирает всё, seen обновляется.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    src = "VK ID (refresh_token)" if has_id_session() else "user_token/service"
    print(f"[TOKEN] source: {src}")
    if force_full:
        print("[DEDUP] --force-full: игнорирую seen при чтении, пересобираю всё")
    else:
        print("[DEDUP] skip-режим: посты/комменты из .seen.json пропускаются")

    seen_path_file = seen_path(RAW_DIR)
    # При force_full skip отключён (seen=None), но seen всё равно загружаем для
    # обновления в конце (новые post_id перезапишут старые).
    seen_for_update: dict = load_seen(seen_path_file)
    skip_seen: dict | None = None if force_full else seen_for_update

    results = []
    for name in screen_names:
        channel_key = f"vk:{name}"
        data = harvest_group(name, seen=skip_seen, channel_key=channel_key)
        if data is None:
            continue
        out = RAW_DIR / f"{name}.json"
        # В skip-режиме: если новых постов нет — НЕ трогаем существующий архив
        # (иначе перезапишем его пустым posts:[]). В --force-full перезаписываем
        # всегда, потому что собрали всё заново.
        if not data["posts"] and not force_full:
            print(f"[SKIP] {name}: нет новых постов, архив не тронут")
            results.append(name)
            time.sleep(INTER_GROUP_PAUSE_SEC)
            continue
        import json
        out.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        total_comments = sum(len(p["comments"]) for p in data["posts"])
        print(
            f"[OK] {name}: {len(data['posts'])} posts, "
            f"{total_comments} comments -> {out.name}"
        )
        # При force_full seen_for_update не обновлялся (skip_seen=None).
        # Обновим здесь: возьмём все post_id из собранных данных.
        if force_full:
            seen_for_update[channel_key] = {
                "posts": sorted([p["post_id"] for p in data["posts"]], reverse=True),
                "comments": {
                    str(p["post_id"]): sorted(
                        [c["comment_id"] for c in p["comments"]], reverse=True
                    )
                    for p in data["posts"]
                },
            }
        results.append(name)
        time.sleep(INTER_GROUP_PAUSE_SEC)

    # Сохраняем seen (один раз в конце — дешевле, чем после каждой группы)
    save_seen(seen_path_file, seen_for_update)
    return results
