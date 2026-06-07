"""Чтение секретов из .env. Никаких хардкодов в коде."""
import os
from pathlib import Path

from dotenv import load_dotenv

# scripts/telegram/config.py -> scripts/telegram/ -> scripts/ -> корень проекта
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# KeyError, если переменная не задана — лучше упасть сразу, чем молча.
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.environ.get("TG_SESSION_NAME", "findppl_session")

# SOCKS5-прокси. Telethon подключается к локальному SOCKS5-серверу,
# а тот уже туннелирует трафик в Telegram (через Shadowsocks/WireGuard/что угодно).
# По умолчанию ожидается, что ss-local слушает на 127.0.0.1:1080.
# См. <локальный notes.md по VPN>: VPS <Shadowsocks-VPS>
# (Shadowsocks) + ss-local как локальный мост.
SOCKS5_HOST = os.environ.get("TG_SOCKS5_HOST", "127.0.0.1")
SOCKS5_PORT = int(os.environ.get("TG_SOCKS5_PORT", "1080"))

# Путь к ss-local.exe (Shadowsocks-клиент в режиме SOCKS5-сервера).
# Мост поднимается скиллом find_ppl (источник telegram) перед сбором.
# Дефолт — текущее местоположение, проверенное в ходе разработки.
SSLOCAL_EXE = os.environ.get("SSLOCAL_EXE", r"C:\Tools\ss-local\sslocal.exe")
SSLOCAL_CONFIG = os.environ.get(
    "SSLOCAL_CONFIG", r"C:\Tools\ss-local\ss-config.json"
)

# Куда сохранять сырой и отфильтрованный архив.
RAW_DIR = ROOT / "data" / "telegram_harvest" / "raw"
FILTERED_DIR = ROOT / "data" / "telegram_harvest" / "filtered"
