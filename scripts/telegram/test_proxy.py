"""Тест публичных MTProto-прокси: проверяет, доходит ли TCP-соединение
и отвечает ли handshake. Никаких секретов не нужно.

Использование: py -m scripts.telegram.test_proxy [N]
где N — сколько первых прокси проверить (по умолчанию 5).
"""
import asyncio
import re
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.network.connection.tcpmtproxy import ConnectionTcpMTProxyAbridged

# Глобальный клиент не нужен, создаём свой для каждого прокси.
# Сессию не сохраняем (имя временное), телефон не запрашиваем —
# только проверка TCP-handshake.
API_ID_DUMMY = 12345
API_HASH_DUMMY = "0" * 32


def parse_proxy_line(line: str) -> tuple[str, int, str] | None:
    """Парсит строку вида tg://proxy?server=X&port=Y&secret=Z."""
    m = re.search(r"server=([^&]+)&port=(\d+)&secret=(\S+)", line)
    if not m:
        return None
    host, port, secret = m.group(1), int(m.group(2)), m.group(3)
    # URL-decode не нужен для IP/порта, secret оставляем как есть.
    return host, port, secret


async def test_one(host: str, port: int, secret: str, timeout: float = 8.0) -> bool:
    """Пробует подключиться к прокси. Возвращает True, если удалось."""
    session = f"test_{host}_{port}"
    client = TelegramClient(
        session,
        API_ID_DUMMY,
        API_HASH_DUMMY,
        proxy=(host, port, secret),
        connection=ConnectionTcpMTProxyAbridged,
        connection_retries=0,
    )
    try:
        # connect() без send_code_request() — только TCP-handshake с прокси.
        await asyncio.wait_for(client.connect(), timeout=timeout)
        await client.disconnect()
        return True
    except (asyncio.TimeoutError, ConnectionError, OSError) as e:
        print(f"    [FAIL] {type(e).__name__}: {str(e)[:100]}")
        return False
    except Exception as e:  # noqa: BLE001
        # Если прошёл handshake, но упал на auth — это OK, прокси живой.
        # Telethon выкидывает что-то типа "ApiIdInvalidError" — значит, прокси
        # ответил и дошёл до стадии auth. Считаем успехом.
        if "ApiId" in str(e) or "auth" in str(e).lower():
            return True
        print(f"    [FAIL] {type(e).__name__}: {str(e)[:100]}")
        return False
    finally:
        # Удаляем временную сессию, чтобы не плодить файлы.
        for ext in ("", ".session", ".session-journal"):
            p = Path(f"{session}{ext}")
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


async def main(n: int = 5) -> None:
    proxy_file = Path("data/proxies_raw.txt")
    if not proxy_file.exists():
        print(f"[ERR] {proxy_file} не найден. Сначала скачайте список прокси.")
        sys.exit(1)
    lines = [
        parse_proxy_line(l)
        for l in proxy_file.read_text(encoding="utf-8").splitlines()
        if l.strip().startswith("tg://proxy")
    ]
    lines = [l for l in lines if l is not None][:n]
    if not lines:
        print("[ERR] Не удалось распарсить ни одной строки прокси.")
        sys.exit(1)
    print(f"Проверяю {len(lines)} прокси…\n")
    working: list[tuple[str, int, str]] = []
    for host, port, secret in lines:
        print(f"[{host}:{port}]")
        ok = await test_one(host, port, secret)
        print(f"    [{'OK' if ok else 'DEAD'}]")
        if ok:
            working.append((host, port, secret))
        print()
    if working:
        print("=" * 60)
        print("РАБОЧИЕ ПРОКСИ (скопируйте один в .env):\n")
        for h, p, s in working:
            print(f"TG_PROXY_HOST={h}")
            print(f"TG_PROXY_PORT={p}")
            print(f"TG_PROXY_SECRET={s}")
            print()
    else:
        print("[WARN] Ни один прокси не ответил. Попробуйте позже.")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    asyncio.run(main(n))
