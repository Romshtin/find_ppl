"""Фильтрация VK-архива по маркерам стратегии.

Использует общий модуль scripts/common/markers.py — ту же логику, что и
Telegram: на выходе компактный JSON с entries[] = [{_score, author_url, text}],
отсортированный (has_author_url desc, _score desc). Удобно читать
в Obsidian построчно, без скрола вправо.
"""
import json
from pathlib import Path

from scripts.common.markers import (
    build_group_url,
    flatten_and_sort,
    load_strategy,
    score_legacy,
    score_weighted,
)

from .config import FILTERED_DIR, RAW_DIR


def filter_archive(in_path: Path, out_path: Path, strategy: dict | None) -> int:
    """Отфильтровать один JSON VK-группы. Возвращает число оставшихся постов."""
    data = json.loads(in_path.read_text(encoding="utf-8"))
    min_score = strategy.get("min_score", 1) if strategy else 1
    score_fn = score_weighted if strategy else score_legacy
    hits_key = "_marker_hits_weighted" if strategy else "_marker_hits"

    kept_posts = []
    for post in data["posts"]:
        s, hits = score_fn(post["text"], strategy) if strategy else score_legacy(post["text"])
        if s < min_score:
            continue
        post[hits_key] = hits
        post["_score"] = s
        kept_comments = []
        for c in post.get("comments", []):
            cs, chits = score_fn(c["text"], strategy) if strategy else score_legacy(c["text"])
            if cs >= min_score:
                c[hits_key] = chits
                c["_score"] = cs
                kept_comments.append(c)
        post["comments"] = kept_comments
        kept_posts.append(post)

    group_url = build_group_url(data)
    entries = flatten_and_sort(kept_posts, group_url=group_url)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact: каждая entry на одной строке, разделители ",", ": ".
    out_path.write_text(
        json.dumps(
            {
                "group_url": group_url,
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
            separators=(",", ": "),
        ),
        encoding="utf-8",
    )
    return len(kept_posts)


def filter_all(strategy: dict | None) -> None:
    """Отфильтровать raw/*.json в FILTERED_DIR/ — только группы из стратегии.

    Если стратегия задаёт `vk_groups`, берём только их (по stem имени файла).
    Иначе (legacy / нет стратегии) — обрабатываем все .json в RAW_DIR.
    """
    FILTERED_DIR.mkdir(parents=True, exist_ok=True)
    allowed: set[str] | None = None
    if strategy and "vk_groups" in strategy:
        allowed = set(strategy["vk_groups"])
    sources = sorted(RAW_DIR.glob("*.json"))
    if not sources:
        print(f"[WARN] Нет файлов в {RAW_DIR}. Сначала запустите harvest.")
        return
    skipped = 0
    for src in sources:
        if allowed is not None and src.stem not in allowed:
            skipped += 1
            continue
        dst = FILTERED_DIR / src.name
        kept = filter_archive(src, dst, strategy)
        strategy_name = strategy.get("_name") if strategy else "legacy"
        print(f"[OK] {src.name}: {kept} posts kept [{strategy_name}] -> {dst.name}")
    if skipped:
        print(f"[SKIP] {skipped} raw-файл(ов) не в стратегии, не тронуты")
