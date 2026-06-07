#!/usr/bin/env python3
"""Конвертирует PDF файлы Курпатова в Markdown используя pdfplumber."""

import sys
import pdfplumber
from pathlib import Path


def convert_pdf_to_md(pdf_path: Path, output_path: Path = None) -> str:
    """Конвертирует PDF в Markdown."""
    if output_path is None:
        output_path = pdf_path.with_suffix('.md')

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = '\n\n'.join(page.extract_text() or '' for page in pdf.pages)

        output_path.write_text(text, encoding='utf-8')
        print(f"[OK] {pdf_path.name} -> {output_path.name}")
        return str(output_path)
    except Exception as e:
        print(f"[ERROR] {pdf_path.name}: {e}")
        return None


def main():
    if len(sys.argv) > 1:
        # Если переданы пути к PDF-файлам в аргументах
        pdf_files = [Path(p) for p in sys.argv[1:]]
    else:
        # По умолчанию — все PDF в папке проекта
        books_dir = Path(r"D:\СС\IdeaProjects\findppl")
        pdf_files = sorted(books_dir.glob("*.pdf"))

    if not pdf_files:
        print("PDF файлы не найдены")
        return

    print(f"Найдено {len(pdf_files)} PDF файлов\n")

    converted = 0
    for pdf_path in pdf_files:
        if not pdf_path.exists():
            print(f"[SKIP] {pdf_path} — файл не найден")
            continue
        result = convert_pdf_to_md(pdf_path, pdf_path.with_suffix('.md'))
        if result:
            converted += 1

    print(f"\nГотово! Сконвертировано: {converted}/{len(pdf_files)}")


if __name__ == '__main__':
    main()
