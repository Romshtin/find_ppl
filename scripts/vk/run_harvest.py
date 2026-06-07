"""CLI: py -m scripts.vk.run_harvest [harvest|filter|resolve|both] [--strategy NAME]

Стратегия — JSON из data/strategies/<NAME>.json, поле 'vk_groups'.
Без --strategy берётся фолбэк (нет групп, пустой прогон).
"""
import argparse
import json
import sys

from scripts.common.markers import load_strategy

from .config import FILTERED_DIR, RAW_DIR
from .filter import filter_all
from .harvest import harvest_all
from .resolve import resolve_all as resolve_all_authors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VK-разведка по стратегии")
    parser.add_argument(
        "command",
        nargs="?",
        default="both",
        choices=["harvest", "filter", "resolve", "both"],
    )
    parser.add_argument(
        "--strategy",
        default=None,
        help="Имя стратегии из data/strategies/<NAME>.json. Поле 'vk_groups' — список групп.",
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Пересобрать всё с нуля, игнорируя seen. По умолчанию — skip-режим (инкрементальный harvest).",
    )
    return parser.parse_args()


def resolve_groups(strategy: dict | None) -> list[str]:
    if strategy and "vk_groups" in strategy:
        return list(strategy["vk_groups"])
    return []


def main() -> None:
    args = parse_args()
    strategy = load_strategy(args.strategy)
    if args.strategy and strategy is None:
        print(f"[WARN] Стратегия '{args.strategy}' не найдена.")
        sys.exit(1)
    if strategy is not None:
        strategy["_name"] = args.strategy
        groups = resolve_groups(strategy)
        print(
            f"[STRATEGY] '{args.strategy}': {len(groups)} vk_groups, "
            f"{len(strategy.get('markers', []))} markers, "
            f"min_score={strategy.get('min_score', 1)}"
        )

    if args.command in ("harvest", "both"):
        groups = resolve_groups(strategy)
        if not groups:
            print("[WARN] Стратегия не задаёт vk_groups, нечего собирать.")
        else:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            harvest_all(groups, force_full=args.force_full)
    if args.command in ("filter", "both"):
        filter_all(strategy)
    if args.command == "resolve":
        resolve_all_authors()


if __name__ == "__main__":
    main()
