"""OAuth-флоу для VK ID SDK: получение access_token + refresh_token.

Использование:
    py -m scripts.vk.auth

Скрипт:
1. Генерирует PKCE-пару (code_verifier + code_challenge), state и device_id
2. Открывает URL авторизации в браузере
3. Ждёт, пока пользователь вставит ПОЛНЫЙ URL из адресной строки после
   редиректа (https://oauth.vk.com/blank.html?code=...&state=...&device_id=...)
4. Обменивает code на access_token + refresh_token через id.vk.com/oauth2/auth
5. Сохраняет результат в data/vk_session.json (вне .env, в .gitignore)

При следующих запусках access_token обновляется автоматически через refresh_token.
Refresh_token живёт 1 год, после — перезапустить этот скрипт.

Заметки:
- VK ID SDK 1.1.1348+ (static.vk.com/vkid/...) требует PKCE обязательно,
  иначе страница падает в "Ошибка загрузки".
- state должен быть >= 32 символов (по докам OAuth 2.1).
- code живёт 10 минут.
"""
import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from .config import ROOT, API_VERSION

AUTH_URL = "https://id.vk.com/authorize"
TOKEN_URL = "https://id.vk.com/oauth2/auth"
REDIRECT_URI = "https://oauth.vk.com/blank.html"
SCOPES = "offline,groups,wall"
SESSION_FILE = ROOT / "data" / "vk_session.json"
APP_SECRET_FILE = ROOT / "data" / "vk_app_secret.txt"

# Читаем app_id из .env
load_dotenv(ROOT / ".env")
APP_ID = os.environ["VK_APP_ID"]


def _load_app_secret() -> str:
    if not APP_SECRET_FILE.exists():
        raise SystemExit(
            f"[ERR] Файл {APP_SECRET_FILE} не найден.\n"
            f"      Положите туда защищённый ключ приложения одной строкой."
        )
    secret = APP_SECRET_FILE.read_text(encoding="utf-8").strip()
    if not secret:
        raise SystemExit(f"[ERR] Файл {APP_SECRET_FILE} пуст.")
    return secret


def _pkce_pair() -> tuple[str, str]:
    """Сгенерировать PKCE-пару: (code_verifier, code_challenge).

    verifier: 64 случайных байта в base64url (~ 86 символов, попадает в 43-128).
    challenge: BASE64URL(SHA256(verifier)) без padding.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_auth_url(
    state: str, device_id: str, code_challenge: str
) -> str:
    """Собрать URL для первичной авторизации с PKCE."""
    params = {
        "client_id": APP_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "device_id": device_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "v": "1.0",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _parse_callback(url: str, expected_state: str) -> tuple[str, str]:
    """Распарсить ПОЛНЫЙ URL после редиректа VK.

    Ожидаемый формат:
        https://oauth.vk.com/blank.html?code=...&state=...&device_id=...

    Возвращает (code, device_id). Проверяет совпадение state.
    """
    parsed = urllib.parse.urlparse(url.strip())
    qs = urllib.parse.parse_qs(parsed.query)
    if "code" not in qs:
        raise SystemExit(
            f"[ERR] В URL не найден параметр 'code'.\n"
            f"      Убедитесь, что вы вставили ПОЛНЫЙ URL из адресной строки\n"
            f"      после редиректа на https://oauth.vk.com/blank.html\n"
            f"      Получено: {url[:200]}"
        )
    code = qs["code"][0]
    returned_state = qs.get("state", [None])[0]
    if returned_state != expected_state:
        raise SystemExit(
            f"[ERR] state из callback не совпадает с отправленным.\n"
            f"      Возможно, вы открыли старую ссылку или подменили URL.\n"
            f"      Ожидалось: {expected_state[:12]}...\n"
            f"      Получено:  {str(returned_state)[:12]}..."
        )
    device_id = qs.get("device_id", [None])[0]
    if not device_id:
        raise SystemExit(
            "[ERR] В URL нет device_id. Перезапустите скрипт и вставьте "
            "ПОЛНЫЙ URL из адресной строки."
        )
    return code, device_id


def _exchange_code_for_tokens(
    code: str, code_verifier: str, device_id: str, state: str, app_secret: str
) -> dict:
    """Обменять code на access_token + refresh_token (с PKCE)."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": app_secret,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": REDIRECT_URI,
        "device_id": device_id,
        "state": state,
        "v": "1.0",
    }
    r = requests.post(TOKEN_URL, data=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        err_code = data.get("error", "")
        hint = ""
        if err_code == "invalid_grant":
            hint = (
                "\n      Возможно, code протух (живёт 10 минут) "
                "или уже использован. Перезапустите скрипт."
            )
        elif err_code == "invalid_request":
            hint = (
                "\n      Проверьте PKCE: code_verifier должен соответствовать "
                "code_challenge из authorize-ссылки."
            )
        raise SystemExit(f"[ERR] VK вернул ошибку: {data}{hint}")
    if "access_token" not in data:
        raise SystemExit(f"[ERR] Неожиданный ответ VK: {data}")
    return data


def _save_session(data: dict, state: str, device_id: str) -> None:
    """Сохранить токены в data/vk_session.json."""
    expires_in = int(data.get("expires_in", 3600))
    session = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "user_id": data.get("user_id"),
        "scope": data.get("scope", SCOPES),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
        "app_id": APP_ID,
        "state": state,
        "device_id": device_id,
        "obtained_via": "vk_id_sdk_pkce",
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    }
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[OK] Сессия сохранена: {SESSION_FILE}")
    print(f"     access_token истекает: {session['expires_at']}")
    print(f"     refresh_token живёт ~1 год (до {(datetime.now(timezone.utc) + timedelta(days=365)).date()})")


def main() -> None:
    print("=== VK ID SDK: первичная авторизация (PKCE) ===\n")
    app_secret = _load_app_secret()
    state = secrets.token_urlsafe(32)        # >= 32 символов (требование VK)
    device_id = secrets.token_urlsafe(16)    # стабильный ID устройства
    code_verifier, code_challenge = _pkce_pair()

    print(f"[1/4] PKCE-пара сгенерирована (challenge = SHA256(verifier), method=S256)")

    auth_url = _build_auth_url(state, device_id, code_challenge)
    print(f"[2/4] Откройте этот URL в браузере (с залогиненным VK):\n\n   {auth_url}\n")

    # Попробуем открыть автоматически
    try:
        if webbrowser.open(auth_url, new=2):
            print("      (URL открыт в браузере автоматически)\n")
    except Exception:
        pass

    print("[3/4] Залогиньтесь и подтвердите права.")
    print("       После редиректа на https://oauth.vk.com/blank.html?code=...&state=...&device_id=...")
    print("       скопируйте ПОЛНЫЙ URL из адресной строки и вставьте сюда:\n")

    raw = input("callback URL> ").strip()
    if not raw:
        raise SystemExit("[ERR] URL не может быть пустым.")

    # Если юзер вставил только code (а не URL) — fallback
    if raw.startswith("vk1.") or ("?" not in raw and "=" not in raw):
        print(
            "[WARN] Похоже, вы вставили только code. Работаю, но state и "
            "device_id придётся указать отдельно."
        )
        code = raw
        returned_device_id = device_id  # используем тот, что отправили
    else:
        code, returned_device_id = _parse_callback(raw, state)

    print("\n[4/4] Обмениваю code на токены...")
    data = _exchange_code_for_tokens(
        code, code_verifier, returned_device_id, state, app_secret
    )
    _save_session(data, state, returned_device_id)

    print("\n[DONE] Авторизация завершена. Теперь:")
    print("  - py -m scripts.vk.run_harvest --strategy individual_rel  # сбор VK")
    print("  - access_token будет автоматически обновляться через refresh_token")
    print("  - refresh_token живёт ~1 год; после истечения — снова запустите этот скрипт")


if __name__ == "__main__":
    main()
