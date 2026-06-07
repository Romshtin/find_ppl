"""CLI: py -m scripts.telegram.run_harvest [harvest|filter|resolve|both] [--strategy NAME]

Стратегия задаётся файлом data/strategies/<NAME>.json.
Без --strategy используются встроенные дефолты (8 каналов из шорт-листа,
19 маркеров из стратегии Курпатова без весов).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from .client import make_client
from .config import FILTERED_DIR, RAW_DIR
from .filter import filter_harvest
from .harvest import INTER_CHANNEL_PAUSE_SEC, harvest_with_retry
from .resolve import resolve_all as resolve_all_authors
from scripts.common.dedup import load_seen, save_seen, seen_path
from scripts.common.markers import load_strategy

# Дефолтный список каналов из шорт-листа 2026-06-02-candidates-shortlist.md,
# раздел «📡 Канал Telegram — не пройден». Используется, если стратегия не задана.
# 2026-06-04: gestalt_msk_chat / gestalt_chat / gestalt_community удалены —
# не прошли верификацию через client.get_entity (см. память findppl-verify-tg-username).
# 2026-06-07: O_soznay и pcap_jung удалены — 0 записей после фильтра,
# не дают вклада (см. CLAUDE.md, раздел «Открытые вопросы»).
DEFAULT_CHANNELS = [
    "empathy_rus",          # «Канал для эмпатов об эмпатах»
    "senseofcalmness",      # Психолог, саногенное мышление
    "empatiaclub",          # Эзотерический уклон, но проверить
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram-разведка по стратегии")
    parser.add_argument(
        "command",
        nargs="?",
        default="both",
        choices=["harvest", "filter", "resolve", "both"],
        help="Что делать: harvest (сбор), filter (фильтр), "
             "resolve (дотянуть author_username/name), both (harvest+filter)",
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Имя стратегии из data/strategies/<NAME>.json (без расширения). "
             "Без флага — встроенные дефолты.",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Пересобрать всё с нуля, игнорируя seen. По умолчанию — skip-режим (инкрементальный harvest).",
    )
    return parser.parse_args()


def resolve_channels(strategy: dict | None) -> list[str]:
    """Каналы из стратегии или дефолтные."""
    if strategy and "channels" in strategy:
        return list(strategy["channels"])
    return list(DEFAULT_CHANNELS)


async def do_harvest(strategy: dict | None, force_full: bool = False) -> None:
    channels = resolve_channels(strategy)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if force_full:
        print("[DEDUP] --force-full: игнорирую seen при чтении, пересобираю всё")
    else:
        print("[DEDUP] skip-режим: посты/комменты из .seen.json пропускаются")
    seen_path_file = seen_path(RAW_DIR)
    # seen_for_update — будем сохранять в конце (даже при force_full — тогда
    # возьмём из собранных данных). skip_seen — то, что реально передаётся
    # в harvest_chat для early break.
    seen_for_update: dict = load_seen(seen_path_file)
    skip_seen: dict | None = None if force_full else seen_for_update
    client = make_client()
    async with client:
        for ch in channels:
            channel_key = f"telegram:{ch}"
            data = await harvest_with_retry(
                client, ch, seen=skip_seen, channel_key=channel_key
            )
            if data is None:
                continue
            out = RAW_DIR / f"{ch}.json"
            # В skip-режиме: если новых постов нет — НЕ трогаем существующий
            # архив (иначе перезапишем его пустым posts:[]). В --force-full
            # перезаписываем всегда, потому что собрали всё заново.
            if not data["posts"] and not force_full:
                print(f"[SKIP] {ch}: нет новых постов, архив не тронут")
                await asyncio.sleep(INTER_CHANNEL_PAUSE_SEC)
                continue
            out.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            total_comments = sum(len(p["comments"]) for p in data["posts"])
            print(
                f"[OK] {ch}: {len(data['posts'])} posts, "
                f"{total_comments} comments -> {out.name}"
            )
            # При force_full skip_seen=None, harvest_chat seen не обновлял.
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
            await asyncio.sleep(INTER_CHANNEL_PAUSE_SEC)
    save_seen(seen_path_file, seen_for_update)


def do_filter(strategy: dict | None) -> None:
    FILTERED_DIR.mkdir(parents=True, exist_ok=True)
    sources = sorted(RAW_DIR.glob("*.json"))
    if not sources:
        print(f"[WARN] Нет файлов в {RAW_DIR}. Сначала запустите harvest.")
        return
    for src in sources:
        dst = FILTERED_DIR / src.name
        kept = filter_harvest(src, dst, strategy=strategy)
        strategy_name = strategy.get("_name") if strategy else "legacy"
        print(f"[OK] {src.name}: {kept} posts kept [{strategy_name}] -> {dst.name}")


def main() -> None:
    args = parse_args()
    strategy = load_strategy(args.strategy)
    if args.strategy and strategy is None:
        print(f"[WARN] Стратегия '{args.strategy}' не найдена, используются дефолты.")
    elif strategy is not None:
        # Запомнить имя для вывода в JSON и логах
        strategy["_name"] = args.strategy
        print(f"[STRATEGY] '{args.strategy}': {len(strategy.get('channels', []))} channels, "
              f"{len(strategy.get('markers', []))} markers, min_score={strategy.get('min_score', 1)}")

    if args.command in ("harvest", "both"):
        asyncio.run(do_harvest(strategy, force_full=args.force_full))
    if args.command in ("filter", "both"):
        do_filter(strategy)
    if args.command == "resolve":
        asyncio.run(resolve_all_authors())


if __name__ == "__main__":
    main()
