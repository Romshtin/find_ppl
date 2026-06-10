"""Сбор последних N постов + всех комментариев к ним в указанных чатах.

Дедупликация: при наличии data/telegram_harvest/.seen.json новые посты
определяются по message.id, при их отсутствии цикл iter_messages прерывается
(early break) — повторный harvest занимает секунды, а не минуты.
"""
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import (
    ChatForbiddenError,
    ChatWriteForbiddenError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import User

# Сколько последних постов брать из каждого чата.
POSTS_PER_CHAT = 200
# Сколько последних комментариев к каждому посту.
COMMENTS_PER_POST = 200
# Пауза между каналами, чтобы не словить FloodWait.
INTER_CHANNEL_PAUSE_SEC = 3


def _build_author_fields(sender, fallback_id: int | None) -> dict:
    """Сконструировать блок автора для JSON из Telethon-объекта.

    Возвращает dict с полями:
      author_id, author_username, author_name, author_url, _resolved.
    Если sender не User (канал, анонимный автор) — author_id = fallback_id,
    username/name/photo = None, author_url пустой.
    """
    if isinstance(sender, User):
        uid = sender.id
        uname = sender.username
        first = (sender.first_name or "").strip()
        last = (sender.last_name or "").strip()
        full = (first + " " + last).strip() or None
        photo = None
        try:
            if sender.photo and sender.photo.photo_small_url:  # type: ignore[attr-defined]
                photo = sender.photo.photo_small_url
        except AttributeError:
            pass
        if uname:
            url = f"https://t.me/{uname}"
        else:
            url = f"tg://user?id={uid}"
        return {
            "author_id": uid,
            "author_username": uname,
            "author_name": full,
            "author_url": url,
            "author_photo": photo,
            "_resolved": True,
        }
    # sender — канал/чат/None (анонимный пост в канале).
    return {
        "author_id": fallback_id,
        "author_username": None,
        "author_name": None,
        "author_url": "",
        "author_photo": None,
        "_resolved": False,
    }


async def harvest_chat(
    client: TelegramClient,
    channel: str,
    seen: dict | None = None,
    channel_key: str | None = None,
) -> dict:
    """Собрать посты и комментарии одного канала/чата.

    Только чтение: ничего не отправляется, не подписывается, не лайкается.

    seen — текущий state (мутируется in-place: добавляются новые message.id/comment_id).
           Если None — skip-логика отключена (для --force-full).
    channel_key — ключ для seen (например "telegram:empathy_rus").
                  Если None — берётся "telegram:<channel>".

    Возвращает dict с posts (только НОВЫМИ, если seen не None) + comments.
    """
    if channel_key is None:
        channel_key = f"telegram:{channel}"
    seen_post_ids: set[int] = set()
    seen_comments_map: dict[str, list[int]] = {}
    if seen is not None and channel_key in seen:
        block = seen[channel_key]
        if isinstance(block, dict):
            seen_post_ids = set(block.get("posts", []))
            seen_comments_map = block.get("comments", {}) or {}

    entity = await client.get_entity(channel)
    posts = []
    async for msg in client.iter_messages(entity, limit=POSTS_PER_CHAT):
        # Early break: сообщение уже в seen (iter_messages идёт от новых к старым)
        if seen is not None and msg.id in seen_post_ids:
            print(f"[DEDUP] {channel}: post {msg.id} уже seen, early break")
            break
        if not msg.message:  # пропускаем пустые и чисто медийные
            continue
        author = _build_author_fields(msg.sender, msg.sender_id)
        post = {
            "channel": channel,
            "post_id": msg.id,
            "date": msg.date.isoformat() if msg.date else None,
            "text": msg.message,
            "views": msg.views or 0,
            "forwards": msg.forwards or 0,
            "replies": msg.replies.replies if msg.replies else 0,
            "comments": [],
        }
        post.update(author)
        # Какие комменты уже seen для этого поста
        skip_comment_ids = set(seen_comments_map.get(str(msg.id), []))
        if msg.replies and msg.replies.replies:
            try:
                async for reply in client.iter_messages(
                    entity, reply_to=msg.id, limit=COMMENTS_PER_POST
                ):
                    # Early break по комменту: iter_messages(reply_to=...) идёт от новых к старым
                    if reply.id in skip_comment_ids:
                        break
                    if reply.message:
                        comment_author = _build_author_fields(
                            reply.sender, reply.sender_id
                        )
                        post["comments"].append(
                            {
                                "comment_id": reply.id,
                                "date": reply.date.isoformat() if reply.date else None,
                                "text": reply.message,
                                **comment_author,
                            }
                        )
            except (ChatForbiddenError, ChatWriteForbiddenError) as e:
                post["comments_error"] = f"access denied: {e.__class__.__name__}"
            except (OSError, TimeoutError) as e:
                # Сетевой сбой при загрузке комментов — пост всё равно сохранится
                post["comments_error"] = f"{e.__class__.__name__}: {e}"
        posts.append(post)
        seen_post_ids.add(msg.id)

    # Зафиксировать собранное в seen (если skip-режим)
    if seen is not None:
        # Создать пустой блок, если канал новый
        if channel_key not in seen or not isinstance(seen.get(channel_key), dict):
            seen[channel_key] = {"posts": [], "comments": {}}
        block = seen[channel_key]
        # posts[]: добавить новые id к существующему списку, отсортировать
        existing_posts = set(block.get("posts", []))
        existing_posts |= seen_post_ids
        block["posts"] = sorted(existing_posts, reverse=True)
        # comments[] для постов зафиксировать по собранным comment_id
        for p in posts:
            pid_str = str(p["post_id"])
            existing_comments = set(block.get("comments", {}).get(pid_str, []))
            for c in p.get("comments", []):
                if c.get("comment_id") is not None:
                    existing_comments.add(c["comment_id"])
            block.setdefault("comments", {})[pid_str] = sorted(
                existing_comments, reverse=True
            )

    return {
        "channel": channel,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }


async def harvest_with_retry(
    client: TelegramClient,
    channel: str,
    seen: dict | None = None,
    channel_key: str | None = None,
) -> dict | None:
    """harvest_chat с обработкой FloodWaitError и типичных ошибок доступа.

    Возвращает None, если канал недоступен (приватный, забанен, не существует).
    Это позволяет не падать на середине списка.
    """
    try:
        return await harvest_chat(client, channel, seen=seen, channel_key=channel_key)
    except FloodWaitError as e:
        print(f"[FLOOD] {channel}: ждём {e.seconds} сек…")
        await e.wait()  # type: ignore[attr-defined]
        return await harvest_chat(client, channel, seen=seen, channel_key=channel_key)
    except (
        ChatForbiddenError,
        ChatWriteForbiddenError,
        UsernameNotOccupiedError,  # username не существует
        UsernameInvalidError,      # неверный формат username
    ) as e:
        print(f"[SKIP] {channel}: {type(e).__name__} — канал недоступен")
        return None
    except ValueError as e:
        # Telethon бросает ValueError, если get_entity не нашёл username.
        if "username" in str(e).lower():
            print(f"[SKIP] {channel}: {e} (нет такого username)")
            return None
        raise
