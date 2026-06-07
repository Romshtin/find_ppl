"""Ленивый резолв профилей авторов VK.

Проходит по RAW_DIR/*.json, собирает уникальные положительные from_id
(User-id) у которых ещё нет author_username, и дотягивает профили
одним запросом users.get (до 1000 id за раз). Записывает обратно в файл.

Кеш: внутри файла, по флагу _resolved. Без отдельного файла-кеша.

Запуск: py -m scripts.vk.resolve
"""
import json
from pathlib import Path

from .config import API_VERSION, RAW_DIR, get_token
from .harvest import _build_author_url

API_BASE = "https://api.vk.com/method"
USERS_GET_BATCH = 1000  # макс. по документации VK API.
# Поля, которые забираем у каждого юзера. screen_name = то, что в URL
# после vk.com/. photo_100 — аватар 100px. can_message — можно ли слать
# личное сообщение (для будущего «выйти на контакт»).
USER_FIELDS = (
    "first_name,last_name,screen_name,photo_100,has_photo,"
    "is_closed,can_message,deactivated"
)


def _collect_user_ids(data: dict) -> set[int]:
    """Собрать уникальные положительные author_id, у которых нет username
    и которые ещё не резолвлены.
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


def _build_profile(user: dict) -> dict:
    """Превратить ответ users.get в блок полей, пригодных для записи в JSON."""
    uid = user["id"]
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    full = (first + " " + last).strip() or None
    screen = user.get("screen_name")
    photo = user.get("photo_100")
    return {
        "author_username": screen,
        "author_name": full,
        "author_photo": photo,
        "_resolved": True,
        # Сохраним диагностику — пригодится при выборе стратегии контакта.
        "_vk_is_closed": bool(user.get("is_closed")),
        "_vk_can_message": bool(user.get("can_message")),
        "_vk_deactivated": user.get("deactivated"),
        # Если есть screen_name — обновим ссылку, чтобы вела на
        # короткий адрес vk.com/<screen>, а не на /id<...>.
        "author_url": f"https://vk.com/{screen}" if screen else _build_author_url(uid),
    }


def _apply_profile(record: dict, profile: dict) -> None:
    record.update(profile)


def _fetch_users(ids: list[int]) -> dict[int, dict]:
    """Один батч-запрос users.get. Возвращает {id: user_dict}."""
    import requests

    params = {
        "user_ids": ",".join(str(i) for i in ids),
        "fields": USER_FIELDS,
        "access_token": get_token(for_comments=False),
        "v": API_VERSION,
    }
    r = requests.get(f"{API_BASE}/users.get", params=params, timeout=30)
    data = r.json()
    if "error" in data:
        err = data["error"]
        # error_code 6 = Too many requests. В harvest есть retry с wait,
        # тут простой повтор через 1 сек.
        if err.get("error_code") == 6:
            wait = int(err.get("retry_after", 1))
            print(f"[FLOOD] VK users.get: ждём {wait} сек...")
            import time
            time.sleep(wait)
            return _fetch_users(ids)
        raise RuntimeError(f"VK API error in users.get: {err}")
    items = data.get("response", [])
    return {u["id"]: u for u in items}


def _resolve_file(path: Path) -> int:
    """Резолвнуть все недостающие author_id в одном raw-файле. Вернуть
    число успешно подтянутых профилей.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    need = _collect_user_ids(data)
    if not need:
        return 0

    resolved_count = 0
    ids = sorted(need)
    # Батчами по USERS_GET_BATCH штук.
    for i in range(0, len(ids), USERS_GET_BATCH):
        batch = ids[i : i + USERS_GET_BATCH]
        try:
            profiles = _fetch_users(batch)
        except RuntimeError as e:
            print(f"[ERR] {path.name}: {e}")
            continue
        # Пройти по записям архива, заменить author_username/name/etc.
        for post in data.get("posts", []):
            uid = post.get("author_id")
            if isinstance(uid, int) and uid in profiles and not post.get("author_username"):
                _apply_profile(post, _build_profile(profiles[uid]))
                resolved_count += 1
            for c in post.get("comments", []):
                cuid = c.get("author_id")
                if (
                    isinstance(cuid, int)
                    and cuid in profiles
                    and not c.get("author_username")
                ):
                    _apply_profile(c, _build_profile(profiles[cuid]))
                    resolved_count += 1

    if resolved_count:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return resolved_count


def resolve_all() -> None:
    """Пройти по RAW_DIR/*.json, дотянуть author_username/name/photo."""
    if not RAW_DIR.exists():
        print(f"[WARN] {RAW_DIR} не существует. Сначала запустите harvest.")
        return
    sources = sorted(RAW_DIR.glob("*.json"))
    if not sources:
        print(f"[WARN] Нет файлов в {RAW_DIR}.")
        return

    for src in sources:
        try:
            n = _resolve_file(src)
        except Exception as e:  # noqa: BLE001
            print(f"[ERR] {src.name}: {e.__class__.__name__}: {e}")
            continue
        if n:
            print(f"[OK] {src.name}: {n} users resolved")


def main() -> None:
    resolve_all()


if __name__ == "__main__":
    main()
