"""Ленивый резолв профилей авторов Telegram.

Проходит по RAW_DIR/*.json, собирает уникальные author_id, у которых ещё
нет author_username / author_name (т.е. _resolved=False), и дотягивает
профиль через Telethon client.get_entity. Записывает обратно в тот же
файл. Повторный запуск безопасен — уже резолвленные записи не трогаем.

Кеш: внутри файла, по флагу _resolved. Без отдельного файла-кеша.

Запуск: py -m scripts.telegram.resolve
"""
import asyncio
import json
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import User

from .client import make_client
from .config import RAW_DIR

# Пауза между резолвами, чтобы не словить FloodWait.
PER_USER_PAUSE_SEC = 0.4


def _collect_user_ids(data: dict) -> set[int]:
    """Пройти один архив, вернуть set уникальных author_id, которые нужно
    резолвить: это User-id (положительные), у которых ещё нет username.
    """
    need: set[int] = set()
    for post in data.get("posts", []):
        uid = post.get("author_id")
        if (
            isinstance(uid, int)
            and uid > 0
            and not post.get("author_username")
        ):
            need.add(uid)
        for c in post.get("comments", []):
            cuid = c.get("author_id")
            if (
                isinstance(cuid, int)
                and cuid > 0
                and not c.get("author_username")
            ):
                need.add(cuid)
    return need


def _apply_profile(record: dict, user: User) -> None:
    """Обновить поля автора в посте/комментарии на месте."""
    uname = user.username
    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()
    full = (first + " " + last).strip() or None
    photo = None
    try:
        if user.photo and user.photo.photo_small_url:  # type: ignore[attr-defined]
            photo = user.photo.photo_small_url
    except AttributeError:
        pass
    if uname:
        record["author_url"] = f"https://t.me/{uname}"
    record["author_username"] = uname
    record["author_name"] = full
    record["author_photo"] = photo
    record["_resolved"] = True


async def _resolve_one(client: TelegramClient, uid: int) -> User | None:
    """Дотянуть профиль одного юзера. None — если не получилось."""
    try:
        entity = await client.get_entity(uid)
    except (UsernameNotOccupiedError, UsernameInvalidError, ValueError):
        return None
    except FloodWaitError as e:
        print(f"[FLOOD] uid={uid}: ждём {e.seconds} сек…")
        await e.wait()  # type: ignore[attr-defined]
        return await _resolve_one(client, uid)
    if not isinstance(entity, User):
        return None
    return entity


async def _resolve_file(client: TelegramClient, path: Path) -> int:
    """Резолвнуть все недостающие author_id в одном raw-файле. Вернуть
    число успешно подтянутых профилей.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    need = _collect_user_ids(data)
    if not need:
        return 0

    resolved_count = 0
    for uid in sorted(need):
        user = await _resolve_one(client, uid)
        if user is None:
            await asyncio.sleep(PER_USER_PAUSE_SEC)
            continue
        # Пробежимся по всем постам/комментариям и обновим.
        for post in data.get("posts", []):
            if post.get("author_id") == uid and not post.get("author_username"):
                _apply_profile(post, user)
                resolved_count += 1
            for c in post.get("comments", []):
                if c.get("author_id") == uid and not c.get("author_username"):
                    _apply_profile(c, user)
                    resolved_count += 1
        await asyncio.sleep(PER_USER_PAUSE_SEC)

    if resolved_count:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return resolved_count


async def resolve_all() -> None:
    """Пройти по RAW_DIR/*.json, дотянуть author_username/name/photo."""
    if not RAW_DIR.exists():
        print(f"[WARN] {RAW_DIR} не существует. Сначала запустите harvest.")
        return
    sources = sorted(RAW_DIR.glob("*.json"))
    if not sources:
        print(f"[WARN] Нет файлов в {RAW_DIR}.")
        return

    client = make_client()
    async with client:
        for src in sources:
            try:
                n = await _resolve_file(client, src)
            except (OSError, ValueError, KeyError) as e:
                # Битый JSON / смена формата / сеть — лог + пропуск файла,
                # остальные источники продолжают работу.
                print(f"[ERR] {src.name}: {e.__class__.__name__}: {e}")
                continue
            if n:
                print(f"[OK] {src.name}: {n} users resolved")


def main() -> None:
    asyncio.run(resolve_all())


if __name__ == "__main__":
    main()
