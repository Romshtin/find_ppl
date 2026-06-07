"""Чтение секретов и настроек VK.

Источник токена (по приоритету):
1. data/vk_session.json — VK ID SDK (access + refresh). Автообновление через refresh_token.
2. data/vk_user_token.txt — пользовательский токен, вручную обновлять каждый час.
3. .env VK_SERVICE_TOKEN — сервисный ключ Standalone-приложения (бессрочный, но без комментариев).
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# scripts/vk/config.py -> scripts/vk/ -> scripts/ -> корень
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# VK API версия
API_VERSION = os.environ.get("VK_API_VERSION", "5.199")

# Сервисный ключ Standalone-приложения
SERVICE_TOKEN = os.environ.get("VK_SERVICE_TOKEN")

# Защищённый ключ приложения (нужен для refresh_token flow)
APP_SECRET_FILE = ROOT / "data" / "vk_app_secret.txt"
APP_SECRET = (
    APP_SECRET_FILE.read_text(encoding="utf-8").strip()
    if APP_SECRET_FILE.exists() else None
)
APP_ID = os.environ.get("VK_APP_ID")

# Сессия VK ID SDK (обновляется автоматически через refresh_token)
SESSION_FILE = ROOT / "data" / "vk_session.json"
TOKEN_URL = "https://id.vk.com/oauth2/auth"

# Fallback: пользовательский токен (1 час)
_USER_TOKEN_FILE = ROOT / "data" / "vk_user_token.txt"
_USER_TOKEN = (
    _USER_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if _USER_TOKEN_FILE.exists() else None
) or None

# Простая блокировка, чтобы не было гонок при обновлении
_refresh_lock_path = ROOT / "data" / ".vk_refresh.lock"


def _load_session() -> dict | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_session(session: dict) -> None:
    SESSION_FILE.write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_expired(session: dict) -> bool:
    """Токен истёк или истечёт в ближайшие 60 секунд.

    Принимает expires_at в двух форматах:
    - ISO 8601 строка: "2026-06-06T20:45:50.000802+00:00" (пишут auth.py и _refresh_access_token)
    - Unix timestamp (int/float): 1780775034 (legacy или ручные правки для теста refresh)
    """
    expires_at = session.get("expires_at")
    if not expires_at:
        return True
    try:
        if isinstance(expires_at, (int, float)):
            # Unix timestamp — datetime.fromtimestamp ожидает naive или с tz
            exp = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        else:
            exp = datetime.fromisoformat(expires_at)
    except (ValueError, TypeError, OSError):
        # Битый формат — лучше считать просроченным, чтобы get_token() обновил
        return True
    return datetime.now(timezone.utc) >= exp - timedelta(seconds=60)


def _refresh_access_token(session: dict) -> dict:
    """Обменять refresh_token на новый access_token. Возвращает обновлённую сессию."""
    if not APP_SECRET:
        raise RuntimeError(
            f"Невозможно обновить токен: {APP_SECRET_FILE} не найден или пуст."
        )
    payload = {
        "grant_type": "refresh_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "refresh_token": session["refresh_token"],
        "device_id": session.get("device_id", ""),
        "state": session.get("state", ""),
        "v": "1.0",
    }
    r = requests.post(TOKEN_URL, data=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(
            f"VK отклонил refresh_token: {data}\n"
            f"Возможно, год прошёл или приложение отозвано. Перезапустите "
            f"py -m scripts.vk.auth для новой авторизации."
        )
    if "access_token" not in data:
        raise RuntimeError(f"VK не вернул access_token: {data}")

    expires_in = int(data.get("expires_in", 3600))
    session["access_token"] = data["access_token"]
    if "refresh_token" in data:
        # VK иногда ротирует refresh_token — обновляем если пришёл новый
        session["refresh_token"] = data["refresh_token"]
    session["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    ).isoformat()
    session["last_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    _save_session(session)
    return session


def get_token(for_comments: bool = False) -> str:
    """Вернуть токен для запросов. Приоритет: VK ID session > user_token > service.

    Для VK ID сессии автоматически обновляет access_token через refresh_token,
    если протух или истекает в ближайшие 60 секунд.

    Args:
        for_comments: True — вернуть user_token (нужен для wall.getComments,
            VK ID SDK токен даёт error 1051 на этом методе).
            False — стандартный приоритет (VK ID → user_token → service).
    """
    if for_comments:
        # user_token единственный даёт доступ к комментариям.
        # Не пытаемся использовать VK ID session даже если она есть.
        if _USER_TOKEN:
            return _USER_TOKEN
        # Если user_token нет — пробуем service (он тоже не даст комменты,
        # но _vk_call выдаст структурную ошибку, которую harvest залогирует).
        if SERVICE_TOKEN:
            return SERVICE_TOKEN
        raise RuntimeError(
            "Для сбора комментариев нужен user_token в data/vk_user_token.txt.\n"
            "Добывается вручную из DevTools: см. CLAUDE.md, раздел «Как добыть "
            "пользовательский VK-токен (раз в час)»."
        )

    session = _load_session()
    if session and session.get("refresh_token"):
        if _is_expired(session):
            # Простая защита от гонок: только один процесс обновляет
            try:
                _refresh_lock_path.touch(exist_ok=False)
                try:
                    session = _refresh_access_token(session)
                finally:
                    if _refresh_lock_path.exists():
                        _refresh_lock_path.unlink()
            except FileExistsError:
                # Другой процесс уже обновляет — подождём и возьмём из файла
                time.sleep(2)
                session = _load_session() or session
        return session["access_token"]

    if _USER_TOKEN:
        return _USER_TOKEN

    if SERVICE_TOKEN:
        return SERVICE_TOKEN

    raise RuntimeError(
        "Не задан VK-токен. Запустите:\n"
        "  py -m scripts.vk.auth   # рекомендуется (refresh_token, 1 год)\n"
        "или положите токен в data/vk_user_token.txt,\n"
        "или задайте VK_SERVICE_TOKEN в .env."
    )


def has_id_session() -> bool:
    """Есть ли активная VK ID сессия (с refresh_token)? Используется в логах."""
    session = _load_session()
    return bool(session and session.get("refresh_token"))


# Куда сохранять сырой и отфильтрованный архив VK
RAW_DIR = ROOT / "data" / "vk_harvest" / "raw"
FILTERED_DIR = ROOT / "data" / "vk_harvest" / "filtered"
