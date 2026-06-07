"""Telethon-клиент. Подключается через локальный SOCKS5-прокси из .env."""
from python_socks import ProxyType
from telethon import TelegramClient

from .config import (
    API_HASH,
    API_ID,
    SOCKS5_HOST,
    SOCKS5_PORT,
    SESSION_NAME,
)


def make_client() -> TelegramClient:
    """Создаёт Telethon-клиент, ходящий через локальный SOCKS5.

    Предполагается, что на 127.0.0.1:1080 слушает локальный мост
    (ss-local из shadowsocks-libev) — он туннелирует трафик в Shadowsocks
    на VPS <Shadowsocks-VPS>, а оттуда в Telegram DC. См.
    <локальный notes.md по VPN>.
    """
    return TelegramClient(
        SESSION_NAME,
        API_ID,
        API_HASH,
        proxy=(ProxyType.SOCKS5, SOCKS5_HOST, SOCKS5_PORT),
    )
